#!/usr/bin/env python3
"""
aidoc_index.py - RAG 语义索引工具
==================================

对 Markdown 文件进行语义切片与索引构建，通过 LLM 生成章节摘要和关键字。

功能：
  1. 基于标题层级的语义切片（自动根据文件大小选择切分粒度）
  2. LLM 驱动的章节摘要生成与关键字提取
  3. 层级目录树（TOC）构建
  4. 倒排关键字索引
  5. 结构化 JSON 索引输出
"""

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

from aidoc_llm import LLMClient, add_llm_args, create_llm_client, extract_json
from aidoc_utils import (
    extract_headings,
    find_code_block_lines,
    print_banner,
    print_stats,
    ProgressPrinter,
)


# =============================================================================
# 切片策略配置
# =============================================================================
#
# 文件大小 → 切分深度映射：
#   - 小文件（<50KB）：文档结构简单，切到 H4 获取细粒度语义单元
#   - 中文件（50-200KB）：平衡精度与数量，切到 H3
#   - 大文件（>200KB）：避免 chunk 过多导致索引膨胀，只切到 H2
#
# 这一策略确保无论文档大小，每个 chunk 都保持合理的上下文长度，
# 既不会太短（丢失语义）也不会太长（降低检索精度）。

SMALL_FILE_THRESHOLD = 50 * 1024       # 50KB
MEDIUM_FILE_THRESHOLD = 200 * 1024     # 200KB

DEPTH_CONFIG = {
    "small": 4,     # 小文件：切到 H4
    "medium": 3,    # 中文件：切到 H3
    "large": 2,     # 大文件：切到 H2
}

# b7 摘要角色说明。系统提示保持最短，完整约束写入 user prompt。
INDEX_SUMMARY_ROLE = (
    "You are a document analysis assistant. "
    "Return JSON only. "
    "The summary and keywords must be in English."
)
INDEX_SUMMARY_SYSTEM = "Return JSON only. Use English for summary and keywords."

# =============================================================================
# 数据结构
# =============================================================================
#
# 索引由三层结构组成：
#   ChunkInfo   - 单个语义切片的完整信息（位置、内容摘要、关键字）
#   TOCNode     - 目录树节点，反映文档的层级结构
#   DocumentIndex - 顶层索引，聚合所有 chunk、TOC 和倒排索引

@dataclass
class ChunkInfo:
    """语义切片信息"""
    id: str                                # 唯一标识（chunk_001 格式）
    title: str                             # 章节标题
    level: int                             # 标题层级（1-6）
    start_line: int                        # 起始行号（1-based）
    end_line: int                          # 结束行号（含）
    line_count: int                        # 行数
    char_count: int                        # 字符数
    content_preview: str                   # 内容预览（前 200 字符）
    summary: str = ""                      # LLM 生成的摘要
    keywords: list = field(default_factory=list)   # LLM 提取的关键字
    children: list = field(default_factory=list)   # 子章节 ID 列表


@dataclass
class TOCNode:
    """目录树节点"""
    id: str
    title: str
    level: int
    children: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "level": self.level,
            "children": [c.to_dict() for c in self.children],
        }


@dataclass
class DocumentIndex:
    """文档索引顶层结构"""
    source_file: str                       # 源文件路径
    file_size: int                         # 文件大小（字节）
    total_lines: int                       # 总行数
    depth_level: int                       # 实际使用的切分深度
    toc_tree: dict                         # TOC 树（序列化后）
    chunks: dict                           # chunk_id -> ChunkInfo
    keyword_index: dict                    # keyword -> [chunk_id, ...]
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "source_file": self.source_file,
            "file_size": self.file_size,
            "total_lines": self.total_lines,
            "depth_level": self.depth_level,
            "toc_tree": self.toc_tree,
            "chunks": {k: asdict(v) for k, v in self.chunks.items()},
            "keyword_index": self.keyword_index,
            "metadata": self.metadata,
        }


# =============================================================================
# Markdown 解析器
# =============================================================================

class MarkdownParser:
    """
    Markdown 文件解析器。

    负责文件加载、标题提取和内容切片。标题提取和代码块检测
    委托给 aidoc_utils 中的共享实现，确保工具链行为一致。
    """

    def __init__(self, filepath: str):
        self.filepath = Path(filepath)
        self.lines: list[str] = []
        self.file_size = 0
        self._load_file()

    def _load_file(self):
        """加载文件内容，记录文件大小"""
        if not self.filepath.exists():
            raise FileNotFoundError(f"文件不存在: {self.filepath}")

        self.file_size = self.filepath.stat().st_size
        with open(self.filepath, "r", encoding="utf-8") as f:
            self.lines = f.readlines()

    def get_depth_level(self) -> int:
        """根据文件大小自动确定切分深度"""
        if self.file_size < SMALL_FILE_THRESHOLD:
            return DEPTH_CONFIG["small"]
        elif self.file_size < MEDIUM_FILE_THRESHOLD:
            return DEPTH_CONFIG["medium"]
        else:
            return DEPTH_CONFIG["large"]

    def get_headings(self, max_level: int) -> list[tuple[int, int, str]]:
        """
        提取标题列表（自动跳过代码块中的伪标题）。

        委托 aidoc_utils.extract_headings() 实现，保证与其他工具的解析行为一致。

        Returns:
            [(行号(1-based), 层级, 标题文本), ...]
        """
        return extract_headings(self.lines, max_level)

    def get_chunk_content(self, start_line: int, end_line: int) -> str:
        """获取指定行范围的内容（行号均为 1-based）"""
        start_idx = start_line - 1
        end_idx = end_line
        return "".join(self.lines[start_idx:end_idx])


# =============================================================================
# 摘要生成
# =============================================================================

def _normalize_summary_payload(data: dict) -> tuple[str, list[str]]:
    """兼容 summary/摘要、keywords/关键词 等键名。"""
    summary = (
        data.get("summary")
        or data.get("摘要")
        or data.get("Summary")
        or ""
    )
    if not isinstance(summary, str):
        summary = str(summary)
    summary = summary.strip()[:100]

    raw_kw = data.get("keywords") or data.get("关键词") or data.get("Keywords")
    keywords: list[str] = []
    if isinstance(raw_kw, list):
        for x in raw_kw[:5]:
            s = str(x).strip()
            if s:
                keywords.append(s)
    elif isinstance(raw_kw, str) and raw_kw.strip():
        keywords = [k.strip() for k in raw_kw.replace("，", ",").split(",") if k.strip()][:5]

    return summary, keywords


def _contains_cjk(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


def _summary_input_char_limit() -> Optional[int]:
    """
    摘要输入正文长度上限（字符）。默认不截断，整段 chunk 交给 LLM。
    若网关/模型有硬性 context 限制，可设环境变量 AIDOC_INDEX_MAX_CHUNK_CHARS（正整数）。
    """
    raw = (os.environ.get("AIDOC_INDEX_MAX_CHUNK_CHARS") or "").strip()
    if not raw or raw == "0":
        return None
    try:
        n = int(raw)
    except ValueError:
        return None
    return n if n > 0 else None


def _strip_figure_enrich_blocks(content: str) -> str:
    """索引摘要不需要 VLM 注入块；去掉可显著缩小 enriched 章节。"""
    return re.sub(
        r"<!--\s*figure-enrich[^>]*-->.*?<!--\s*/figure-enrich\s*-->",
        "",
        content,
        flags=re.DOTALL | re.IGNORECASE,
    )


def _auto_summary_char_limit(content_len: int) -> Optional[int]:
    """未设 AIDOC_INDEX_MAX_CHUNK_CHARS 时，对超大节自动截断，降低 400。"""
    explicit = _summary_input_char_limit()
    if explicit is not None:
        return explicit
    if content_len > 200_000:
        return 120_000
    if content_len > 80_000:
        return 80_000
    if content_len > 40_000:
        return 50_000
    return None


# 失败后逐级缩短正文再试（须递减；可按需 export AIDOC_INDEX_MAX_CHUNK_CHARS 覆盖首档）
_SUMMARY_RETRY_FALLBACKS = (80_000, 50_000, 30_000, 15_000, 8_000)


def _truncate_for_summary(content: str, limit: Optional[int]) -> str:
    if limit is None or len(content) <= limit:
        return content
    return content[:limit] + "\n...[truncated for index summary]"


def _title_fallback_summary(title: str) -> tuple[str, list[str]]:
    """正文过短（如 Preface 仅标题）时用标题作摘要，不再调 LLM。"""
    t = (title or "").strip()
    if not t:
        return "", []
    words = [w for w in re.sub(r"[^\w\s-]", " ", t).split() if w][:5]
    return t[:100], words[:5] if words else [t[:40]]


def _summary_retry_limits(content_len: int) -> list[Optional[int]]:
    """先试自动/显式上限，失败后逐步缩短正文再试。"""
    primary = _auto_summary_char_limit(content_len)
    limits: list[Optional[int]] = []
    if primary is not None:
        limits.append(primary)
    elif content_len > 15_000:
        limits.append(15_000)
    else:
        limits.append(None)
    for fallback in _SUMMARY_RETRY_FALLBACKS:
        if primary is not None and fallback < primary:
            limits.append(fallback)
        elif primary is None and content_len > fallback:
            limits.append(fallback)
    # 去重保序
    seen: set[Optional[int]] = set()
    out: list[Optional[int]] = []
    for lim in limits:
        if lim not in seen:
            seen.add(lim)
            out.append(lim)
    return out


def summarize_chunk(
    llm: LLMClient,
    content: str,
    title: str,
    *,
    verbose: bool = False,
    max_attempts: int = 3,
) -> tuple[str, list[str]]:
    """
    调用 LLM 为单个 chunk 生成摘要和关键字。

    默认将本 chunk 全文（不截断）写入提示；过长时仅当设置
    AIDOC_INDEX_MAX_CHUNK_CHARS 才会截断。

    Returns:
        (摘要文本, 关键字列表)；内容过短或调用失败时返回 ("", [])
    """
    content = _strip_figure_enrich_blocks(content)
    if len(content.strip()) < 50:
        return _title_fallback_summary(title)

    limits = _summary_retry_limits(len(content))
    system = INDEX_SUMMARY_SYSTEM

    last_response = ""
    for lim in limits:
        piece = _truncate_for_summary(content, lim)
        prompt = f"""{INDEX_SUMMARY_ROLE}

Analyze the following section and produce a concise summary and keywords.

Requirements:
- summary must be in English
- keywords must be in English
- keep summary to one short sentence, ideally under 25 words
- return JSON only

Section title: {title}

Section content:
{piece}

Return exactly this JSON shape:
{{
    "summary": "one-sentence English summary",
    "keywords": ["english_keyword_1", "english_keyword_2", "english_keyword_3"]
}}
"""
        for attempt in range(max_attempts):
            temp = 0.15 if attempt == 0 else 0.25 + 0.05 * attempt
            response = llm.generate(prompt, system, temperature=min(temp, 0.5))
            last_response = response or ""
            data = extract_json(last_response)
            if data and isinstance(data, dict):
                summary, keywords = _normalize_summary_payload(data)
                if summary and not _contains_cjk(summary) and not any(_contains_cjk(k) for k in keywords):
                    if verbose and lim is not None and len(content) > len(piece):
                        print(
                            f"[aidoc_index] 章节「{title[:40]}」使用截断正文 "
                            f"({len(piece)}/{len(content)} chars)",
                            file=sys.stderr,
                        )
                    return summary, keywords
        if verbose and lim is not None and len(piece) < len(content):
            print(
                f"[aidoc_index] 章节「{title[:40]}」截断至 {len(piece)}/{len(content)} chars 仍失败，尝试更短…",
                file=sys.stderr,
            )

    if verbose:
        snippet = (last_response[:800] + "…") if len(last_response) > 800 else last_response
        print(
            f"\n[aidoc_index 调试] 章节「{title[:60]}」摘要仍为空，已尝试 {max_attempts} 次。"
            f" 最后模型原文（截断）:\n{snippet}\n",
            file=sys.stderr,
        )

    # 小节 LLM 仍失败（如网关 400）时用标题+正文预览兜底，避免索引长期空白
    if len(content.strip()) < 4_000:
        fb_sum, fb_kw = _title_fallback_summary(title)
        preview = content.strip().replace("\n", " ")[:120]
        if preview and preview.lower() not in fb_sum.lower():
            fb_sum = f"{fb_sum}: {preview}"[:100]
        return fb_sum, fb_kw

    return "", []


# =============================================================================
# 索引构建器
# =============================================================================

class IndexBuilder:
    """
    文档索引构建器。

    构建流程：
      1. 提取标题 → 确定 chunk 边界
      2. 创建 ChunkInfo 列表
      3. （可选）调用 LLM 生成摘要和关键字
      4. 构建层级目录树
      5. 构建倒排关键字索引
      6. 组装 DocumentIndex
    """

    def __init__(
        self,
        parser: MarkdownParser,
        llm: Optional[LLMClient] = None,
        verbose: bool = False,
    ):
        self.parser = parser
        self.llm = llm
        self.verbose = verbose
        self.depth_level = parser.get_depth_level()
        self.chunks: dict[str, ChunkInfo] = {}
        self.keyword_index: dict[str, list[str]] = {}
        # LLM 摘要仍为空 的节（chunk_id, title），供 metadata 与 --strict-llm 使用
        self._empty_summary_chunks: list[tuple[str, str]] = []

    def build(self, use_llm: bool = True) -> DocumentIndex:
        """构建完整索引并返回 DocumentIndex"""
        print(f"文件大小: {self.parser.file_size / 1024:.1f} KB")
        print(f"切分深度: H1-H{self.depth_level}")
        print(f"总行数: {len(self.parser.lines)}")

        # 第一步：提取标题，确定 chunk 边界
        headings = self.parser.get_headings(self.depth_level)
        print(f"识别到 {len(headings)} 个章节")

        # 第二步：创建 chunk 数据
        self._create_chunks(headings)

        # 第三步：LLM 摘要生成
        if use_llm and self.llm:
            self._generate_summaries()

        # 第四步：构建目录树
        toc_tree = self._build_toc_tree(headings)

        # 第五步：构建倒排关键字索引
        self._build_keyword_index()

        meta: dict = {
            "model": self.llm.model if self.llm else None,
            "depth_config": DEPTH_CONFIG,
        }
        if use_llm and self.llm and self._empty_summary_chunks:
            meta["empty_summary_count"] = len(self._empty_summary_chunks)
            meta["empty_summary_chunks"] = [
                {"id": cid, "title": t} for cid, t in self._empty_summary_chunks
            ]
        else:
            meta["empty_summary_count"] = 0
            meta["empty_summary_chunks"] = []
        if not (use_llm and self.llm):
            meta["llm_summaries"] = False
        else:
            meta["llm_summaries"] = True

        return DocumentIndex(
            source_file=str(self.parser.filepath),
            file_size=self.parser.file_size,
            total_lines=len(self.parser.lines),
            depth_level=self.depth_level,
            toc_tree=toc_tree.to_dict() if toc_tree else {},
            chunks=self.chunks,
            keyword_index=self.keyword_index,
            metadata=meta,
        )

    def _create_chunks(self, headings: list[tuple[int, int, str]]):
        """
        根据标题列表创建 chunk。

        每个 chunk 的范围是从当前标题行到下一个标题行之前（或文件末尾）。
        """
        total_lines = len(self.parser.lines)

        for i, (line_num, level, title) in enumerate(headings):
            # chunk 结束于下一个标题行之前，或文件末尾
            if i + 1 < len(headings):
                end_line = headings[i + 1][0] - 1
            else:
                end_line = total_lines

            chunk_id = f"chunk_{i + 1:03d}"
            content = self.parser.get_chunk_content(line_num, end_line)
            line_count = end_line - line_num + 1

            # 生成内容预览（压缩换行，截断到 200 字符）
            preview = content[:200].replace("\n", " ").strip()
            if len(content) > 200:
                preview += "..."

            self.chunks[chunk_id] = ChunkInfo(
                id=chunk_id,
                title=title,
                level=level,
                start_line=line_num,
                end_line=end_line,
                line_count=line_count,
                char_count=len(content),
                content_preview=preview,
            )

    def _generate_summaries(self):
        """使用 LLM 为每个 chunk 生成摘要和关键字"""
        self._empty_summary_chunks = []
        progress = ProgressPrinter(total=len(self.chunks), prefix="摘要生成")

        for i, (chunk_id, chunk) in enumerate(self.chunks.items(), 1):
            progress.update(i, detail=chunk.title)

            content = self.parser.get_chunk_content(chunk.start_line, chunk.end_line)
            summary, keywords = summarize_chunk(
                self.llm,
                content,
                chunk.title,
                verbose=self.verbose,
            )

            chunk.summary = summary
            chunk.keywords = keywords
            if not summary:
                self._empty_summary_chunks.append((chunk_id, chunk.title))
            progress.item_done(success=bool(summary))

        progress.finish()

    def _build_toc_tree(self, headings: list[tuple[int, int, str]]) -> Optional[TOCNode]:
        """
        从扁平标题列表构建层级目录树。

        算法：使用栈维护当前路径上的祖先节点。
        遇到新标题时，回退栈直到找到层级更高（数字更小）的父节点，
        然后将新节点挂载为其子节点。
        """
        if not headings:
            return None

        # 虚拟根节点（level=0），作为所有顶层标题的父节点
        root = TOCNode(id="root", title="", level=0)
        stack = [root]
        chunk_idx = 0

        for line_num, level, title in headings:
            chunk_id = f"chunk_{chunk_idx + 1:03d}"
            node = TOCNode(id=chunk_id, title=title, level=level)

            # 回退栈：找到层级严格更高的祖先作为父节点
            while stack and stack[-1].level >= level:
                stack.pop()

            if stack:
                stack[-1].children.append(node)

            stack.append(node)
            chunk_idx += 1

        return root

    def _build_keyword_index(self):
        """
        构建倒排关键字索引（keyword -> chunk_id 列表）。

        关键字统一转小写以支持大小写无关检索。
        """
        self.keyword_index = {}
        for chunk_id, chunk in self.chunks.items():
            for keyword in chunk.keywords:
                keyword_lower = keyword.lower()
                if keyword_lower not in self.keyword_index:
                    self.keyword_index[keyword_lower] = []
                if chunk_id not in self.keyword_index[keyword_lower]:
                    self.keyword_index[keyword_lower].append(chunk_id)


def _chunk_from_dict(raw: dict) -> ChunkInfo:
    return ChunkInfo(
        id=str(raw.get("id") or ""),
        title=str(raw.get("title") or ""),
        level=int(raw.get("level") or 0),
        start_line=int(raw.get("start_line") or 0),
        end_line=int(raw.get("end_line") or 0),
        line_count=int(raw.get("line_count") or 0),
        char_count=int(raw.get("char_count") or 0),
        content_preview=str(raw.get("content_preview") or ""),
        summary=str(raw.get("summary") or ""),
        keywords=list(raw.get("keywords") or []),
        children=list(raw.get("children") or []),
    )


def load_document_index(path: Path) -> DocumentIndex:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"索引根节点须为 object: {path}")
    chunks_raw = data.get("chunks")
    if not isinstance(chunks_raw, dict):
        raise ValueError(f"索引缺少 chunks: {path}")
    chunks = {k: _chunk_from_dict(v) for k, v in chunks_raw.items() if isinstance(v, dict)}
    return DocumentIndex(
        source_file=str(data.get("source_file") or ""),
        file_size=int(data.get("file_size") or 0),
        total_lines=int(data.get("total_lines") or 0),
        depth_level=int(data.get("depth_level") or 2),
        toc_tree=data.get("toc_tree") if isinstance(data.get("toc_tree"), dict) else {},
        chunks=chunks,
        keyword_index={},
        metadata=dict(data.get("metadata") or {}),
    )


def rebuild_keyword_index(chunks: dict[str, ChunkInfo]) -> dict[str, list[str]]:
    kw_index: dict[str, list[str]] = {}
    for chunk_id, chunk in chunks.items():
        for keyword in chunk.keywords:
            keyword_lower = keyword.lower()
            if keyword_lower not in kw_index:
                kw_index[keyword_lower] = []
            if chunk_id not in kw_index[keyword_lower]:
                kw_index[keyword_lower].append(chunk_id)
    return kw_index


def fill_empty_summaries(
    index: DocumentIndex,
    parser: MarkdownParser,
    llm: LLMClient,
    *,
    verbose: bool = False,
) -> tuple[int, int]:
    """
    仅对 summary 为空的 chunk 重新调用 LLM，保留已有摘要与 TOC 结构。

    Returns:
        (本次新补齐数量, 仍为空数量)
    """
    targets = [
        (cid, chunk)
        for cid, chunk in index.chunks.items()
        if not (chunk.summary or "").strip()
    ]
    if not targets:
        print("无需补齐：所有章节已有摘要。")
        index.keyword_index = rebuild_keyword_index(index.chunks)
        index.metadata["empty_summary_count"] = 0
        index.metadata["empty_summary_chunks"] = []
        return 0, 0

    print(f"补齐模式：共 {len(targets)}/{len(index.chunks)} 个空摘要节待处理")
    lim = _summary_input_char_limit()
    if lim:
        print(f"正文截断: AIDOC_INDEX_MAX_CHUNK_CHARS={lim}")

    progress = ProgressPrinter(total=len(targets), prefix="摘要补齐")
    filled = 0
    still_empty: list[tuple[str, str]] = []

    for i, (chunk_id, chunk) in enumerate(targets, 1):
        progress.update(i, detail=chunk.title)
        content = parser.get_chunk_content(chunk.start_line, chunk.end_line)
        summary, keywords = summarize_chunk(
            llm,
            content,
            chunk.title,
            verbose=verbose,
        )
        chunk.summary = summary
        chunk.keywords = keywords
        if summary:
            filled += 1
        else:
            still_empty.append((chunk_id, chunk.title))
        progress.item_done(success=bool(summary))

    progress.finish()

    index.keyword_index = rebuild_keyword_index(index.chunks)
    index.metadata["empty_summary_count"] = len(still_empty)
    index.metadata["empty_summary_chunks"] = [
        {"id": cid, "title": t} for cid, t in still_empty
    ]
    index.metadata["llm_summaries"] = True
    if llm:
        index.metadata["model"] = llm.model
    index.metadata["fill_empty_last_run"] = True

    return filled, len(still_empty)


# =============================================================================
# CLI 入口
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Markdown 语义切片与 RAG 索引工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
常用示例:
  # 基本用法 - 使用默认模型处理文档
  %(prog)s document.md

  # 快速预览 - 只生成结构索引，不调用 LLM
  %(prog)s document.md --no-llm

  # 指定输出文件名
  %(prog)s document.md -o my_index.json

  # 使用其他模型
  %(prog)s document.md --model deepseek-r1:32b

  # 强制指定切分深度（覆盖自动检测）
  %(prog)s large_doc.md --depth 2   # 只切到 H2
  %(prog)s small_doc.md --depth 4   # 切到 H4

  # 仅补齐已有索引里 summary 为空的章节（不重跑全量 LLM）
  export AIDOC_INDEX_MAX_CHUNK_CHARS=80000
  %(prog)s doc_enriched.md -o doc_enriched.index.h4.json --fill-empty --depth 4

  # 批量处理
  for f in docs/*.md; do %(prog)s "$f"; done

切分策略 (自动根据文件大小选择):
  - 小文件 (<50KB):   细粒度，切到 H4
  - 中文件 (50-200KB): 中等粒度，切到 H3
  - 大文件 (>200KB):  粗粒度，切到 H2
        """,
    )

    parser.add_argument("input", help="输入的 Markdown 文件路径")
    parser.add_argument(
        "-o", "--output",
        help="输出的索引文件路径（默认: <input>.index_full.json 或 <input>.index.json）",
    )
    parser.add_argument(
        "--depth", type=int, choices=[1, 2, 3, 4, 5, 6],
        help="强制指定切分深度（覆盖自动检测）",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="详细输出")
    parser.add_argument(
        "--strict-llm",
        action="store_true",
        help="使用 LLM 时若有章节摘要仍为空则退出码 1（便于脚本/CI 发现网关波动）",
    )
    parser.add_argument(
        "--fill-empty",
        action="store_true",
        help="加载 -o 已有索引，仅对 summary 为空的 chunk 调用 LLM 并写回（须与 --depth 一致）",
    )

    # 添加统一的 LLM 参数（--api, --model, --api-url, --api-key, --no-llm）
    add_llm_args(parser)

    args = parser.parse_args()

    # 检查输入文件
    input_path = Path(args.input)
    if not input_path.exists():
        print(f"错误: 文件不存在 - {input_path}")
        sys.exit(1)

    # 确定输出路径
    if args.output:
        output_path = Path(args.output)
    else:
        suffix = ".index.json" if getattr(args, "no_llm", False) else ".index_full.json"
        output_path = input_path.parent / f"{input_path.name}{suffix}"

    fill_empty = bool(getattr(args, "fill_empty", False))
    if fill_empty and getattr(args, "no_llm", False):
        print("错误: --fill-empty 不能与 --no-llm 同时使用", file=sys.stderr)
        sys.exit(1)
    if fill_empty and not args.output:
        print("错误: --fill-empty 须配合 -o 指定已有索引 JSON", file=sys.stderr)
        sys.exit(1)
    if fill_empty and not output_path.is_file():
        print(f"错误: --fill-empty 找不到已有索引: {output_path}", file=sys.stderr)
        sys.exit(1)

    # 打印横幅
    use_llm = not getattr(args, "no_llm", False)
    model_display = getattr(args, "model", None) or "(自动)"
    print_banner("aidoc_index - RAG 语义索引工具")
    print(f"输入文件: {input_path}")
    print(f"输出文件: {output_path}")
    print(f"模型: {model_display if use_llm else '(不使用)'}")
    if fill_empty:
        print("模式: --fill-empty（仅补齐空摘要节）")
    print()

    # 解析 Markdown
    md_parser = MarkdownParser(str(input_path))

    # 如果指定了深度，覆盖自动检测
    if args.depth:
        forced_depth = args.depth
        md_parser.get_depth_level = lambda _d=forced_depth: _d  # type: ignore[method-assign, assignment]

    # 创建 LLM 客户端（通过统一工厂函数）
    llm = create_llm_client(args) if use_llm else None

    if fill_empty:
        print(f"加载已有索引: {output_path}")
        index = load_document_index(output_path)
        idx_depth = index.depth_level
        if args.depth and args.depth != idx_depth:
            print(
                f"警告: CLI --depth {args.depth} 与索引内 depth_level {idx_depth} 不一致，"
                f"补齐仍使用索引内行号，请确保 -o 文件与 depth 匹配",
                file=sys.stderr,
            )
        md_parser.get_depth_level = lambda _d=idx_depth: _d  # type: ignore[method-assign, assignment]
        if llm is None:
            print("错误: --fill-empty 需要 LLM", file=sys.stderr)
            sys.exit(1)
        print("开始补齐空摘要…")
        filled, n_empty = fill_empty_summaries(index, md_parser, llm, verbose=bool(args.verbose))
        print(f"\n本次新补齐: {filled} 节；仍为空: {n_empty} 节")
    else:
        # 构建索引
        print("开始构建索引...")
        builder = IndexBuilder(md_parser, llm, verbose=bool(args.verbose))
        index = builder.build(use_llm=use_llm)
        n_empty = int(index.metadata.get("empty_summary_count") or 0)
        filled = None

    # 保存索引
    print(f"\n保存索引到: {output_path}")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(index.to_dict(), f, ensure_ascii=False, indent=2)

    # 输出统计
    stats = {
        "章节数量": len(index.chunks),
        "关键字数量": len(index.keyword_index),
        "切分深度": f"H1-H{index.depth_level}",
    }
    if fill_empty:
        stats["本次新补齐"] = filled
    if use_llm and index.metadata.get("llm_summaries"):
        stats["摘要为空节数"] = n_empty
    print_stats(stats, title="索引统计")
    if n_empty > 0 and use_llm and index.metadata.get("llm_summaries"):
        titles = [x["title"] for x in index.metadata.get("empty_summary_chunks") or []]
        hint = "可设 AIDOC_INDEX_MAX_CHUNK_CHARS 后加 --fill-empty 仅补空节"
        print(
            f"\n警告: 有 {n_empty} 个章节仍无有效摘要，"
            f"RAG 关键词可能偏少。{hint}；"
            f"节标题: {titles[:8]}{'…' if len(titles) > 8 else ''}\n",
            file=sys.stderr,
        )
    if args.strict_llm and use_llm and n_empty > 0 and index.metadata.get("llm_summaries"):
        print("错误: --strict-llm 已启用且存在空摘要节，退出 1", file=sys.stderr)
        sys.exit(1)
    print("完成!")


if __name__ == "__main__":
    main()
