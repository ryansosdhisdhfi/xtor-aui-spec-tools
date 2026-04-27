#!/usr/bin/env python3
"""
aidoc_convert - PDF 转 Markdown 工具
=====================================

基于 Docling 引擎的高质量 PDF 解析与 Markdown 转换工具。
作为 aidoc 工具链的第一环节，将 PDF 文档转换为结构化 Markdown，
供下游工具（清洗、摘要、RAG 等）消费。

核心能力：
  - 代码块识别增强（enrich-code）
  - 公式 LaTeX 转换
  - TableFormer 高精度表格提取
  - 位置+重复模式的页眉页脚过滤
  - 跨页代码块自动合并
  - OCR 扫描文档支持

用法示例：
  # 基本转换
  python3 aidoc_convert.py document.pdf

  # 指定输出、快速表格模式
  python3 aidoc_convert.py document.pdf -o output.md --table-mode fast

  # 禁用 OCR、保留页眉页脚
  python3 aidoc_convert.py document.pdf --no-ocr --keep-headers-footers

  # 显示转换统计
  python3 aidoc_convert.py document.pdf --stats -v
"""

import argparse
import json
import re
import sys
import threading
import time
import warnings
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional

from aidoc_utils import print_banner, print_stats

# 抑制无 GPU 环境下 PyTorch 的 pin_memory 警告
warnings.filterwarnings("ignore", message=".*pin_memory.*", category=UserWarning)

# Docling 依赖检查
try:
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import (
        PdfPipelineOptions,
        TableFormerMode,
        TableStructureOptions,
        EasyOcrOptions,
        AcceleratorOptions,
    )
    from docling.datamodel.document import ConversionResult
    from docling_core.types.doc import DocItemLabel, ImageRefMode
except ImportError as e:
    print(f"错误: 缺少 docling 依赖")
    print(f"请安装: pip install docling docling-core")
    print(f"详细错误: {e}")
    sys.exit(1)


# =============================================================================
# 配置常量
# =============================================================================

DEFAULT_TABLE_MODE = "accurate"
DEFAULT_OCR_LANG = ["ch_sim", "en"]
DEFAULT_BATCH_SIZE = 4
DEFAULT_TIMEOUT = 7200

# 假定 A4 @ 72 DPI，缺少页面尺寸信息时的回退值
DEFAULT_PAGE_HEIGHT = 842.0


# =============================================================================
# 核心转换类
# =============================================================================

class DoclingPdfConverter:
    """Docling PDF 转换器，输出优化用于 AI 后续处理"""

    def __init__(
        self,
        enable_code_enrichment: bool = True,
        enable_formula_enrichment: bool = True,
        enable_picture_classification: bool = True,
        enable_picture_description: bool = False,
        enable_ocr: bool = True,
        ocr_languages: Optional[list[str]] = None,
        table_mode: str = DEFAULT_TABLE_MODE,
        device: str = "cpu",
        verbose: bool = False,
        progress_interval: int = 20,
        images_scale: float = 4.0,
    ):
        self.verbose = verbose
        self.enable_code_enrichment = enable_code_enrichment
        self.enable_formula_enrichment = enable_formula_enrichment
        self.enable_picture_classification = enable_picture_classification
        self.enable_picture_description = enable_picture_description
        self.enable_ocr = enable_ocr
        self.ocr_languages = ocr_languages or DEFAULT_OCR_LANG
        self.table_mode = table_mode
        self.device = device
        self.progress_interval = max(0, int(progress_interval))
        self.images_scale = images_scale

        self.converter = self._create_converter()

    def _create_converter(self) -> DocumentConverter:
        """配置并创建 Docling DocumentConverter"""
        opts = PdfPipelineOptions()

        # 表格: do_cell_matching 将识别的结构映射回 PDF 单元格，改善合并列检测
        opts.do_table_structure = True
        opts.table_structure_options = TableStructureOptions(
            mode=TableFormerMode.ACCURATE if self.table_mode == "accurate" else TableFormerMode.FAST,
            do_cell_matching=True,
        )

        opts.do_code_enrichment = self.enable_code_enrichment
        opts.do_formula_enrichment = self.enable_formula_enrichment
        opts.do_picture_classification = self.enable_picture_classification
        opts.do_picture_description = self.enable_picture_description
        opts.generate_picture_images = True
        opts.generate_page_images = True
        opts.images_scale = self.images_scale

        # OCR: 仅对需要的区域做 OCR，全页 OCR 在大多数场景下浪费且降低质量
        opts.do_ocr = self.enable_ocr
        if self.enable_ocr:
            try:
                opts.ocr_options = EasyOcrOptions(
                    lang=self.ocr_languages,
                    force_full_page_ocr=False,
                )
            except Exception as e:
                if self.verbose:
                    print(f"警告: EasyOCR 配置失败，使用默认 OCR: {e}")

        # 批处理大小统一设置，避免内存抖动
        opts.table_batch_size = DEFAULT_BATCH_SIZE
        opts.layout_batch_size = DEFAULT_BATCH_SIZE
        opts.ocr_batch_size = DEFAULT_BATCH_SIZE

        if self.device != "auto":
            try:
                opts.accelerator_options = AcceleratorOptions(device=self.device)
            except Exception:
                pass

        opts.document_timeout = DEFAULT_TIMEOUT

        if self.verbose:
            print("Pipeline 配置:")
            print(f"  代码增强: {self.enable_code_enrichment}")
            print(f"  公式增强: {self.enable_formula_enrichment}")
            print(f"  图片分类: {self.enable_picture_classification}")
            print(f"  OCR: {self.enable_ocr}")
            print(f"  表格模式: {self.table_mode}")
            print(f"  图片缩放: {self.images_scale}x")

        return DocumentConverter(
            format_options={
                InputFormat.PDF: PdfFormatOption(pipeline_options=opts),
            }
        )

    def convert(self, pdf_path: str) -> ConversionResult:
        """转换 PDF 文件，返回 Docling ConversionResult"""
        path = Path(pdf_path)
        if not path.exists():
            raise FileNotFoundError(f"PDF 文件不存在: {pdf_path}")

        print(f"开始转换: {path.name}")
        start_time = time.time()
        done_event = threading.Event()

        def _heartbeat():
            # Docling 在初始化与推理阶段可能长时间无输出，这里定时打印心跳进度。
            while not done_event.wait(self.progress_interval):
                elapsed = time.time() - start_time
                print(f"[进度] 仍在转换中，已耗时 {elapsed:.1f}s ...")

        heartbeat_thread = None
        if self.progress_interval > 0:
            heartbeat_thread = threading.Thread(target=_heartbeat, daemon=True)
            heartbeat_thread.start()

        try:
            result = self.converter.convert(str(path))
        finally:
            done_event.set()
            if heartbeat_thread is not None:
                heartbeat_thread.join(timeout=0.2)

        elapsed = time.time() - start_time
        print(f"转换完成，耗时: {elapsed:.1f}s")

        return result

    def to_markdown(
        self,
        result: ConversionResult,
        filter_headers_footers: bool = True,
        image_placeholder: str = "[图片]",
        image_mode: str = "embedded",
    ) -> str:
        """
        将 ConversionResult 导出为 Markdown。

        Args:
            filter_headers_footers: 过滤页眉页脚（通过排除对应 label 实现）
            image_mode: embedded=base64 内嵌, placeholder=占位符, referenced=外部引用
        """
        doc = result.document

        image_mode_map = {
            "embedded": ImageRefMode.EMBEDDED,
            "placeholder": ImageRefMode.PLACEHOLDER,
            "referenced": ImageRefMode.REFERENCED,
        }
        img_mode = image_mode_map.get(image_mode, ImageRefMode.EMBEDDED)
        # referenced/embedded 需要遍历图片节点，否则可能退化为占位符输出
        export_kwargs = {
            "image_mode": img_mode,
            "traverse_pictures": img_mode in (ImageRefMode.EMBEDDED, ImageRefMode.REFERENCED),
        }
        if img_mode == ImageRefMode.PLACEHOLDER:
            export_kwargs["image_placeholder"] = image_placeholder

        if filter_headers_footers:
            # Docling 的 export_to_markdown 支持通过 labels 参数白名单过滤
            filtered_labels = [
                label for label in DocItemLabel
                if label not in (DocItemLabel.PAGE_HEADER, DocItemLabel.PAGE_FOOTER)
            ]
            markdown = doc.export_to_markdown(
                labels=filtered_labels,
                **export_kwargs,
            )
        else:
            markdown = doc.export_to_markdown(
                **export_kwargs,
            )

        return markdown

    def save_markdown_with_assets(
        self,
        result: ConversionResult,
        output_path: str,
        filter_headers_footers: bool = True,
        image_placeholder: str = "[图片]",
        image_mode: str = "referenced",
    ) -> str:
        """
        使用 Docling 原生保存 Markdown 与图片资源（referenced）。
        返回保存后的 markdown 文本，用于后续项目内后处理。
        """
        doc = result.document
        output = Path(output_path).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)

        image_mode_map = {
            "embedded": ImageRefMode.EMBEDDED,
            "placeholder": ImageRefMode.PLACEHOLDER,
            "referenced": ImageRefMode.REFERENCED,
        }
        img_mode = image_mode_map.get(image_mode, ImageRefMode.REFERENCED)

        save_kwargs = {
            "image_mode": img_mode,
        }
        if img_mode == ImageRefMode.PLACEHOLDER:
            save_kwargs["image_placeholder"] = image_placeholder

        if filter_headers_footers:
            filtered_labels = [
                label for label in DocItemLabel
                if label not in (DocItemLabel.PAGE_HEADER, DocItemLabel.PAGE_FOOTER)
            ]
            save_kwargs["labels"] = filtered_labels

        # 关键：先让 Docling 原生落盘 referenced 资源目录
        try:
            doc.save_as_markdown(output, **save_kwargs)
        except TypeError as e:
            # 兼容旧版 docling：遇到未知参数时进行降级重试
            if "unexpected keyword argument" in str(e):
                save_kwargs.pop("image_placeholder", None)
                doc.save_as_markdown(output, **save_kwargs)
            else:
                raise
        return output.read_text(encoding="utf-8")

    def _safe_get(self, obj: Any, name: str, default=None):
        """安全获取对象属性，避免兼容性字段缺失导致失败。"""
        try:
            return getattr(obj, name, default)
        except Exception:
            return default

    def export_image_index(
        self,
        result: ConversionResult,
        output_md_path: str,
    ) -> str:
        """
        导出图片索引 JSON，并尝试将图片单独落盘。

        输出:
          - <name>.images.json
          - <name>_images/fig_xxxx.png
        """
        doc = result.document
        output_md = Path(output_md_path)
        image_dir = output_md.parent / f"{output_md.stem}_images"
        image_dir.mkdir(parents=True, exist_ok=True)

        items = []
        pic_idx = -1

        for item, _ in doc.iterate_items():
            label = self._safe_get(item, "label")
            if label != DocItemLabel.PICTURE:
                continue

            pic_idx += 1
            image_id = f"fig_{pic_idx:04d}"
            item_data = {
                "image_id": image_id,
                "label": str(label),
                "page_no": None,
                "bbox": None,
                "text": self._safe_get(item, "text"),
                "caption": None,
                "image_path": None,
                "prov": [],
            }

            prov_list = self._safe_get(item, "prov", []) or []
            for prov in prov_list:
                page_no = self._safe_get(prov, "page_no")
                bbox = self._safe_get(prov, "bbox")
                prov_entry = {
                    "page_no": page_no,
                    "bbox": {
                        "l": self._safe_get(bbox, "l"),
                        "t": self._safe_get(bbox, "t"),
                        "r": self._safe_get(bbox, "r"),
                        "b": self._safe_get(bbox, "b"),
                    } if bbox is not None else None,
                }
                item_data["prov"].append(prov_entry)
                if item_data["page_no"] is None:
                    item_data["page_no"] = page_no
                if item_data["bbox"] is None and prov_entry["bbox"] is not None:
                    item_data["bbox"] = prov_entry["bbox"]

            annotations = self._safe_get(item, "annotations", []) or []
            captions = []
            for ann in annotations:
                ann_text = self._safe_get(ann, "text")
                if ann_text:
                    captions.append(ann_text)
            if captions:
                item_data["caption"] = "\n".join(captions)

            try:
                image_obj = self._safe_get(item, "image")
                if image_obj is not None:
                    img_filename = f"{image_id}.png"
                    img_path = image_dir / img_filename
                    image_obj.save(img_path)
                    item_data["image_path"] = str(img_path.relative_to(output_md.parent))
            except Exception:
                # 某些 Docling 版本的图片对象不支持直接 save，这里保持降级兼容。
                pass

            items.append(item_data)

        json_path = output_md.with_suffix(".images.json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)

        if self.verbose:
            print(f"图片索引已保存: {json_path}")
            print(f"图片项数量: {len(items)}")

        return str(json_path)

    def convert_and_save(
        self,
        pdf_path: str,
        output_path: Optional[str] = None,
        filter_headers_footers: bool = True,
    ) -> str:
        """一步完成: 转换 PDF 并保存为 Markdown 文件，返回输出路径"""
        pdf_path = Path(pdf_path)

        if output_path is None:
            output_path = pdf_path.with_suffix(".md")
        else:
            output_path = Path(output_path)

        result = self.convert(str(pdf_path))
        markdown = self.to_markdown(result, filter_headers_footers=filter_headers_footers)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(markdown)

        if self.verbose:
            print(f"已保存: {output_path}")
            print(f"文件大小: {output_path.stat().st_size / 1024:.1f} KB")

        return str(output_path)


# =============================================================================
# 后处理: 页眉页脚检测
# =============================================================================

def detect_headers_footers_by_position(
    result: ConversionResult,
    margin_ratio: float = 0.08,
    min_repeat: int = 3,
    min_pages_ratio: float = 0.1,
    verbose: bool = False,
) -> set[str]:
    """
    基于位置+重复模式检测页眉页脚文本。

    Docling 的布局模型对页眉页脚识别效果不佳，此函数通过两个启发式规则补充检测：
      1. 文本位于页面顶部或底部边缘区域（由 margin_ratio 控制）
      2. 相同文本在多个页面重复出现（由 min_repeat 和 min_pages_ratio 控制）
    """
    doc = result.document

    # 收集页面高度信息
    page_heights = {}
    if hasattr(doc, 'pages') and doc.pages:
        for page_no, page in doc.pages.items():
            if hasattr(page, 'size') and page.size:
                page_heights[page_no] = page.size.height

    total_pages = len(doc.pages) if hasattr(doc, 'pages') else 1

    # 统计每个边缘文本出现在哪些页面
    edge_texts: dict[str, set[int]] = defaultdict(set)

    for item, _ in doc.iterate_items():
        text = getattr(item, 'text', None)
        if not text or len(text.strip()) < 2:
            continue

        text_clean = text.strip()

        for prov in getattr(item, 'prov', []):
            bbox = prov.bbox
            if bbox is None:
                continue

            page_no = prov.page_no
            page_height = page_heights.get(page_no, DEFAULT_PAGE_HEIGHT)

            # bbox 坐标原点可能在左下或左上，两种情况下"顶部/底部"的判断逻辑相反
            coord_origin = getattr(bbox, 'coord_origin', None)

            if coord_origin and str(coord_origin).upper() == 'BOTTOMLEFT':
                is_at_top = bbox.t > page_height * (1 - margin_ratio)
                is_at_bottom = bbox.b < page_height * margin_ratio
            else:
                is_at_top = bbox.t < page_height * margin_ratio
                is_at_bottom = bbox.b > page_height * (1 - margin_ratio)

            if is_at_top or is_at_bottom:
                edge_texts[text_clean].add(page_no)

    # 同时满足绝对次数和相对占比要求才判定为页眉页脚
    min_pages = max(min_repeat, int(total_pages * min_pages_ratio))

    header_footer_texts = set()
    for text, pages in edge_texts.items():
        if len(pages) >= min_pages:
            header_footer_texts.add(text)

    if verbose and header_footer_texts:
        print(f"检测到 {len(header_footer_texts)} 个页眉页脚文本模式")
        for text in list(header_footer_texts)[:5]:
            display = text[:50] + "..." if len(text) > 50 else text
            print(f"  - \"{display}\"")
        if len(header_footer_texts) > 5:
            print(f"  ... 及其他 {len(header_footer_texts) - 5} 个")

    return header_footer_texts


def filter_headers_footers_from_markdown(
    markdown: str,
    header_footer_texts: set[str],
) -> tuple[str, int]:
    """
    从 Markdown 文本中移除匹配的页眉页脚行。

    除精确匹配外，还处理含变化页码的情况（如 "Page 1" / "Page 2"），
    通过去除数字后比较来匹配这类模式。
    """
    if not header_footer_texts:
        return markdown, 0

    lines = markdown.split('\n')
    filtered_lines = []
    removed_count = 0

    for line in lines:
        line_stripped = line.strip()
        should_remove = False

        for hf_text in header_footer_texts:
            if line_stripped == hf_text:
                should_remove = True
                break
            # 页码变化匹配: 去除所有数字后比较，捕获 "Page 1"/"Page 2" 类模式
            line_no_digits = re.sub(r'\d+', '', line_stripped)
            hf_no_digits = re.sub(r'\d+', '', hf_text)
            if line_no_digits and line_no_digits == hf_no_digits and len(line_no_digits) > 3:
                should_remove = True
                break

        if should_remove:
            removed_count += 1
        else:
            filtered_lines.append(line)

    return '\n'.join(filtered_lines), removed_count


# =============================================================================
# 后处理: 代码块合并
# =============================================================================

def merge_consecutive_code_blocks(markdown: str) -> str:
    """
    合并连续的代码块，修复 Docling 跨页代码分割问题。

    Docling 逐页识别代码块，跨页代码会被拆成多个独立块。
    当两个代码块之间只有空白、且语言标识兼容（相同或其中一个为空）时，合并它们。
    """
    code_block_pattern = re.compile(r'```(\w*)\n(.*?)```', re.DOTALL)

    blocks = list(code_block_pattern.finditer(markdown))
    if len(blocks) < 2:
        return markdown

    # 从后向前处理，避免替换后的位置偏移
    result = markdown
    i = len(blocks) - 1

    while i > 0:
        curr_block = blocks[i]
        prev_block = blocks[i - 1]

        between = result[prev_block.end():curr_block.start()]
        if between.strip() == '':
            prev_lang = prev_block.group(1)
            curr_lang = curr_block.group(1)

            # 空语言标识视为通配，可与任何语言合并
            if prev_lang == curr_lang or not prev_lang or not curr_lang:
                merged_lang = prev_lang or curr_lang
                prev_code = prev_block.group(2).rstrip('\n')
                curr_code = curr_block.group(2).lstrip('\n')

                merged = f"```{merged_lang}\n{prev_code}\n{curr_code}```"
                result = result[:prev_block.start()] + merged + result[curr_block.end():]

                # 位置已变化，重新扫描
                blocks = list(code_block_pattern.finditer(result))
                i = min(i - 1, len(blocks) - 1)
                continue

        i -= 1

    return result


# =============================================================================
# 统计信息
# =============================================================================

def get_conversion_stats(result: ConversionResult) -> dict:
    """从 ConversionResult 中提取各类元素的统计信息"""
    doc = result.document

    stats = {
        "状态": str(result.status),
        "页数": 0,
        "表格": 0,
        "图片": 0,
        "代码块": 0,
        "公式": 0,
        "页眉页脚(已过滤)": 0,
        "章节标题": 0,
    }

    heading_levels: dict[int, int] = {}

    for item, _ in doc.iterate_items():
        label = getattr(item, "label", None)
        if label == DocItemLabel.TABLE:
            stats["表格"] += 1
        elif label == DocItemLabel.PICTURE:
            stats["图片"] += 1
        elif label == DocItemLabel.CODE:
            stats["代码块"] += 1
        elif label == DocItemLabel.FORMULA:
            stats["公式"] += 1
        elif label in (DocItemLabel.PAGE_HEADER, DocItemLabel.PAGE_FOOTER):
            stats["页眉页脚(已过滤)"] += 1
        elif label == DocItemLabel.SECTION_HEADER:
            stats["章节标题"] += 1
            level = getattr(item, "level", 2)
            heading_levels[level] = heading_levels.get(level, 0) + 1

    if hasattr(doc, "pages"):
        stats["页数"] = len(doc.pages)

    if heading_levels:
        levels_str = ", ".join(f"H{k}:{v}" for k, v in sorted(heading_levels.items()))
        stats["标题层级分布"] = levels_str

    return stats


# =============================================================================
# CLI 入口
# =============================================================================

def build_parser() -> argparse.ArgumentParser:
    """构建命令行参数解析器"""
    parser = argparse.ArgumentParser(
        description="aidoc_convert - PDF 转 Markdown 工具 (基于 Docling)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 基本用法
  %(prog)s document.pdf

  # 指定输出文件
  %(prog)s document.pdf -o output.md

  # 快速表格模式
  %(prog)s document.pdf --table-mode fast

  # 禁用 OCR (纯文本 PDF)
  %(prog)s document.pdf --no-ocr

  # 启用图片描述 (需要 VLM 模型)
  %(prog)s document.pdf --picture-description

  # 指定 OCR 语言
  %(prog)s document.pdf --ocr-lang en,ch_sim,ja

  # GPU 加速
  %(prog)s document.pdf --device cuda

  # 批量处理
  for f in *.pdf; do %(prog)s "$f"; done
        """
    )

    # 输入输出
    parser.add_argument("input", help="输入 PDF 文件路径")
    parser.add_argument("-o", "--output", help="输出 Markdown 文件路径 (默认: <input>.md)")

    # 功能开关
    parser.add_argument("--no-code-enrichment", action="store_true",
                        help="禁用代码块识别增强")
    parser.add_argument("--no-formula-enrichment", action="store_true",
                        help="禁用公式 LaTeX 转换")
    parser.add_argument("--no-picture-classification", action="store_true",
                        help="禁用图片分类")
    parser.add_argument("--picture-description", action="store_true",
                        help="启用图片描述生成 (需要 VLM)")
    parser.add_argument("--no-ocr", action="store_true",
                        help="禁用 OCR")
    parser.add_argument("--no-merge-code", action="store_true",
                        help="禁用连续代码块合并 (跨页代码修复)")
    parser.add_argument("--keep-headers-footers", action="store_true",
                        help="保留页眉页脚 (默认过滤)")

    # 配置选项
    parser.add_argument("--ocr-lang", default="ch_sim,en",
                        help="OCR 语言列表，逗号分隔 (默认: ch_sim,en)")
    parser.add_argument("--table-mode", choices=["accurate", "fast"],
                        default="accurate",
                        help="表格识别模式 (默认: accurate)")
    parser.add_argument("--device", choices=["auto", "cpu", "cuda", "mps"],
                        default="cpu",
                        help="计算设备 (默认: cpu)")
    parser.add_argument("--image-mode", choices=["embedded", "placeholder", "referenced"],
                        default="referenced",
                        help="图片模式 (默认: referenced)")
    parser.add_argument("--images-scale", type=float, default=4.0,
                        help="导出图片缩放因子，1.0=72DPI，2.0=144DPI，4.0=288DPI (默认: 4.0)")

    # 其他
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="详细输出")
    parser.add_argument("--stats", action="store_true",
                        help="显示转换统计信息")
    parser.add_argument("--progress-interval", type=int, default=20,
                        help="转换阶段心跳进度打印间隔（秒，0=关闭，默认: 20）")

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    # 验证输入文件
    input_path = Path(args.input)
    if not input_path.exists():
        print(f"错误: 文件不存在 - {input_path}")
        sys.exit(1)

    if input_path.suffix.lower() != ".pdf":
        print(f"警告: 文件可能不是 PDF - {input_path}")

    output_path = Path(args.output) if args.output else input_path.with_suffix(".md")
    ocr_languages = [lang.strip() for lang in args.ocr_lang.split(",")]

    # 打印运行信息
    print_banner("aidoc_convert - PDF 转 Markdown 工具")
    print(f"输入文件: {input_path}")
    print(f"输出文件: {output_path}")
    print()

    try:
        # 初始化转换器
        converter = DoclingPdfConverter(
            enable_code_enrichment=not args.no_code_enrichment,
            enable_formula_enrichment=not args.no_formula_enrichment,
            enable_picture_classification=not args.no_picture_classification,
            enable_picture_description=args.picture_description,
            enable_ocr=not args.no_ocr,
            ocr_languages=ocr_languages,
            table_mode=args.table_mode,
            device=args.device,
            verbose=args.verbose,
            progress_interval=args.progress_interval,
            images_scale=args.images_scale,
        )

        print("开始转换...")
        start_time = time.time()

        # 第一步: Docling 解析
        result = converter.convert(str(input_path))

        # 显示统计
        if args.stats or args.verbose:
            stats = get_conversion_stats(result)
            print_stats(stats, title="转换统计")

        # 第二步: 用 Docling 原生方式导出 Markdown+图片资源，再进行项目后处理
        markdown = converter.save_markdown_with_assets(
            result,
            str(output_path),
            filter_headers_footers=not args.keep_headers_footers,
            image_mode=args.image_mode,
        )

        # 第三步: 增强页眉页脚过滤（基于位置+重复，补充 Docling 内置过滤的不足）
        if not args.keep_headers_footers:
            if args.verbose:
                print("\n执行增强页眉页脚检测...")
            hf_texts = detect_headers_footers_by_position(result, verbose=args.verbose)
            if hf_texts:
                markdown, removed_count = filter_headers_footers_from_markdown(markdown, hf_texts)
                if args.verbose or args.stats:
                    print(f"增强过滤移除了 {removed_count} 行页眉页脚")

        # 第四步: 合并跨页代码块
        if not args.no_merge_code:
            original_len = len(markdown)
            markdown = merge_consecutive_code_blocks(markdown)
            if args.verbose and len(markdown) != original_len:
                print("已合并跨页代码块")

        # 保存结果
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(markdown)

        # 第五步: 导出图片索引与图片文件（独立于 Markdown 资源引用，便于后续 OCR/VLM）
        converter.export_image_index(result, str(output_path))

        elapsed = time.time() - start_time

        print_stats(
            {
                "耗时": f"{elapsed:.1f}s",
                "输出": str(output_path),
                "大小": f"{output_path.stat().st_size / 1024:.1f} KB",
            },
            title="转换完成",
        )

    except Exception as e:
        print(f"\n错误: 转换失败 - {e}")
        if args.verbose:
            import traceback
            traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
