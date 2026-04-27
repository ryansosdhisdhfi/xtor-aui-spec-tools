#!/usr/bin/env python3
"""
aidoc_fix_hierarchy - 标题层级修复工具
=======================================

修复 docling 等 PDF→Markdown 工具产生的标题层级退化问题。
典型场景：所有标题被压平为同一层级（如全部 ##）。

五阶段修复算法：
  Phase 0: 页眉污染检测 — 识别并移除重复出现的文档标题（页眉残留）
  Phase 1: 规则骨架构建 — 利用编号模式（1. / 1.1 / A.1 等）确定绝对层级
  Phase 2: 区间归属分析 — 将无编号标题归属到前后有编号标题构成的区间
  Phase 3: 内联小节处理 — Rules / Permissions / Example 等继承父章节层级+1
  Phase 4: LLM/上下文推断 — 对剩余无编号标题使用 LLM 或规则回退

用法：
  python3 aidoc_fix_hierarchy.py document.md
  python3 aidoc_fix_hierarchy.py document.md --dry-run
  python3 aidoc_fix_hierarchy.py document.md --no-llm
"""

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from aidoc_llm import add_llm_args, create_llm_client
from aidoc_utils import find_code_block_lines, HEADING_PATTERN, print_banner, print_stats


# =============================================================================
# 配置常量
# =============================================================================

# 内联小节标题关键词 — 这些通常出现在编号章节内部，需继承上下文层级
INLINE_SECTION_TITLES = {
    # 规范性文档中常见的无编号子节名
    "rules", "rule",
    "permissions", "permission",
    "recommendations", "recommendation",
    "requirements", "requirement",
    "notes", "note",
    "warnings", "warning",
    "examples", "example",
    "exceptions", "exception",
    "constraints", "constraint",
    "definitions", "definition",
    "objectives", "objective",
    "procedures", "procedure",
    "options", "option",
    "general", "overview",
    "background", "rationale",
    "discussion", "description",
    "syntax", "semantics",
    "inputs", "outputs",
    "parameters", "return value",
}

# 页眉/页脚特征模式 — 用于识别被 OCR 捕获的页眉文字
HEADER_FOOTER_PATTERNS = [
    r'^IEEE\s+Std\s+[\d\.\-]+',
    r'^ISO\s+[\d\.\-]+',
    r'^ISO/IEC\s+[\d\.\-]+',
    r'^[A-Z]+\s+Standard\s+for',
    r'^\d+$',
    r'^Page\s+\d+',
    r'^-\s*\d+\s*-$',
]

# 编号模式正则 — 匹配各类章节编号格式
PATTERNS = {
    # 纯数字编号: 1. / 2. / 10.
    "level1_num": re.compile(r'^(\d+)\.\s+(.+)$'),
    # 两级编号: 1.1 / 2.3
    "level2_num": re.compile(r'^(\d+)\.(\d+)\s+(.+)$'),
    # 三级编号: 1.1.1
    "level3_num": re.compile(r'^(\d+)\.(\d+)\.(\d+)\s+(.+)$'),
    # 四级编号: 1.1.1.1
    "level4_num": re.compile(r'^(\d+)\.(\d+)\.(\d+)\.(\d+)\s+(.+)$'),
    # 五级编号: 1.1.1.1.1
    "level5_num": re.compile(r'^(\d+)\.(\d+)\.(\d+)\.(\d+)\.(\d+)\s+(.+)$'),
    # 字母编号: A. / Annex A
    "annex": re.compile(r'^(?:Annex\s+)?([A-Z])\.\s+(.+)$', re.IGNORECASE),
    # 字母+数字: A.1 / B.2
    "annex_sub": re.compile(r'^([A-Z])\.(\d+)\s+(.+)$', re.IGNORECASE),
    # 字母+数字+数字: A.1.1
    "annex_sub2": re.compile(r'^([A-Z])\.(\d+)\.(\d+)\s+(.+)$', re.IGNORECASE),
    # Figure / Table / Example 引用编号
    "figure": re.compile(r'^(Figure|Table|Example)\s+(\d+)', re.IGNORECASE),
}


# =============================================================================
# 数据结构
# =============================================================================

@dataclass
class HeadingInfo:
    """单个标题的完整分析信息"""
    line_num: int               # 行号 (1-based)
    original_level: int         # 原始 Markdown 层级
    inferred_level: int         # 推断出的正确层级
    title: str                  # 标题文本（不含 # 前缀）
    numbering: str              # 编号部分（如 "1.1.1"），无编号则为空
    inference_method: str       # 推断来源: "rule" / "llm" / "context" / "inline" / "original"
    confidence: float           # 置信度 0.0-1.0
    raw_line: str               # 原始行内容（含换行符）
    is_header_pollution: bool = False   # 是否为页眉污染（需移除）
    is_inline_section: bool = False     # 是否为内联小节标题
    parent_section: str = ""            # 父章节编号（仅内联小节使用）


@dataclass
class FixResult:
    """整个文档的修复结果汇总"""
    source_file: str
    total_headings: int
    fixed_headings: int
    rule_based_fixes: int
    llm_based_fixes: int
    removed_headers: int = 0
    inline_section_fixes: int = 0
    headings: list[HeadingInfo] = field(default_factory=list)


# =============================================================================
# 标题分析器
# =============================================================================

class HeadingAnalyzer:
    """
    标题分析器 — 从 Markdown 文本中提取标题并分析其属性。

    职责：
      - 提取标题（自动跳过代码块）
      - 基于编号模式推断层级
      - 检测页眉污染和内联小节标题
    """

    def __init__(self, lines: list[str]):
        self.lines = lines
        # 利用 aidoc_utils 的共享函数定位代码块
        self.code_block_lines = find_code_block_lines(lines)

    def extract_headings(self) -> list[HeadingInfo]:
        """提取所有非代码块内的标题，返回 HeadingInfo 列表"""
        headings = []

        for i, line in enumerate(self.lines):
            if i in self.code_block_lines:
                continue

            match = HEADING_PATTERN.match(line.strip())
            if match:
                level = len(match.group(1))
                title = match.group(2).strip()
                headings.append(HeadingInfo(
                    line_num=i + 1,
                    original_level=level,
                    inferred_level=level,   # 初始值等于原始层级
                    title=title,
                    numbering="",
                    inference_method="original",
                    confidence=0.0,
                    raw_line=line,
                ))

        return headings

    @staticmethod
    def infer_level_from_numbering(title: str) -> tuple[int, str, float]:
        """
        根据标题编号推断层级。

        编号→层级映射：
          1.          → H2    A.          → H2
          1.1         → H3    A.1         → H3
          1.1.1       → H4    A.1.1       → H4
          1.1.1.1     → H5
          1.1.1.1.1   → H6

        Returns:
            (推断层级, 编号字符串, 置信度)。无法判断时层级返回 -1。
        """
        title = title.strip()

        # 从最长编号开始匹配，避免短编号误匹配长编号
        # 五级编号 → H6
        m = PATTERNS["level5_num"].match(title)
        if m:
            return 6, f"{m.group(1)}.{m.group(2)}.{m.group(3)}.{m.group(4)}.{m.group(5)}", 0.95

        # 四级编号 → H5
        m = PATTERNS["level4_num"].match(title)
        if m:
            return 5, f"{m.group(1)}.{m.group(2)}.{m.group(3)}.{m.group(4)}", 0.95

        # 三级编号 → H4
        m = PATTERNS["level3_num"].match(title)
        if m:
            return 4, f"{m.group(1)}.{m.group(2)}.{m.group(3)}", 0.95

        # Annex 子子级 A.1.1 → H4
        m = PATTERNS["annex_sub2"].match(title)
        if m:
            return 4, f"{m.group(1)}.{m.group(2)}.{m.group(3)}", 0.90

        # 两级编号 → H3
        m = PATTERNS["level2_num"].match(title)
        if m:
            return 3, f"{m.group(1)}.{m.group(2)}", 0.95

        # Annex 子级 A.1 → H3
        m = PATTERNS["annex_sub"].match(title)
        if m:
            return 3, f"{m.group(1)}.{m.group(2)}", 0.90

        # 一级编号 → H2
        m = PATTERNS["level1_num"].match(title)
        if m:
            return 2, f"{m.group(1)}.", 0.95

        # Annex → H2
        m = PATTERNS["annex"].match(title)
        if m:
            return 2, f"{m.group(1)}.", 0.90

        # Figure/Table/Example — 需要上下文才能确定层级
        m = PATTERNS["figure"].match(title)
        if m:
            return -1, "", 0.5

        # 无编号，无法通过规则判断
        return -1, "", 0.0

    @staticmethod
    def is_header_pollution(title: str, _all_titles: list[str],
                            occurrence_count: dict) -> bool:
        """
        检测标题是否为页眉污染（被 OCR 捕获的重复出现的文档标题）。

        判据：标题出现多次 + 匹配已知页眉模式（IEEE/ISO 标准名等）。
        """
        normalized = title.strip().lower()

        # 只有出现多次的标题才可能是页眉
        if occurrence_count.get(normalized, 0) <= 1:
            return False

        # 匹配已知页眉模式
        for pattern in HEADER_FOOTER_PATTERNS:
            if re.search(pattern, title, re.IGNORECASE):
                return True

        # "Standard for" 出现多次 → 几乎确定是页眉
        if "standard for" in normalized:
            return True

        # IEEE / ISO 开头的标题出现多次 → 页眉
        if re.match(r'^IEEE\s+', title, re.IGNORECASE):
            return True
        if re.match(r'^ISO', title, re.IGNORECASE):
            return True

        return False

    @staticmethod
    def is_inline_section_title(title: str) -> bool:
        """
        检测是否为内联小节标题。

        内联小节是编号章节内部的无编号子标题，如：
          ## Rules    ## Example 1    ## Note:
        """
        normalized = title.strip().lower().rstrip(':;.,').strip()

        # 直接匹配
        if normalized in INLINE_SECTION_TITLES:
            return True

        # 复数形式匹配
        if normalized.rstrip('s') in INLINE_SECTION_TITLES:
            return True

        # "Example 1", "Note A" 等带编号的形式
        base_word = normalized.split()[0] if normalized else ""
        if base_word in INLINE_SECTION_TITLES or base_word.rstrip('s') in INLINE_SECTION_TITLES:
            rest = normalized[len(base_word):].strip()
            if not rest or re.match(r'^[a-z0-9]+$', rest):
                return True

        return False


# =============================================================================
# 层级修复器
# =============================================================================

class HierarchyFixer:
    """
    层级修复器 — 五阶段算法的核心实现。

    接收原始行列表和可选的 LLM 客户端，输出 FixResult。
    """

    def __init__(self, lines: list[str], llm=None):
        """
        Args:
            lines: Markdown 文件按行分割的列表
            llm:   LLMClient 实例（来自 aidoc_llm），None 则不使用 LLM
        """
        self.lines = lines
        self.llm = llm
        self.analyzer = HeadingAnalyzer(lines)

    def fix(self, use_llm: bool = True, base_level: int = 1,
            remove_headers: bool = True, verbose: bool = True) -> FixResult:
        """
        执行五阶段修复算法。

        Args:
            use_llm:        是否启用 LLM 辅助推断
            base_level:     文档标题的基础层级（1=H1，2=H2）
            remove_headers: 是否移除页眉污染
            verbose:        是否输出进度信息
        """
        headings = self.analyzer.extract_headings()

        if verbose:
            print(f"  找到 {len(headings)} 个标题")

        if not headings:
            return FixResult(
                source_file="", total_headings=0, fixed_headings=0,
                rule_based_fixes=0, llm_based_fixes=0, headings=[],
            )

        rule_fixes = 0
        llm_fixes = 0
        removed_count = 0
        inline_fixes = 0

        # =====================================================================
        # Phase 0: 页眉污染检测
        # ---------------------------------------------------------------------
        # 统计每个标题的出现次数，标记重复出现且匹配页眉模式的标题。
        # 保留首次出现，后续重复标记为 is_header_pollution。
        # =====================================================================
        if verbose:
            print("  [1/5] 检测页眉污染...")

        all_titles = [h.title for h in headings]
        title_counts: dict[str, int] = {}
        for t in all_titles:
            key = t.strip().lower()
            title_counts[key] = title_counts.get(key, 0) + 1

        if remove_headers:
            first_occurrence: set[str] = set()
            for heading in headings:
                key = heading.title.strip().lower()
                if self.analyzer.is_header_pollution(heading.title, all_titles, title_counts):
                    if key in first_occurrence:
                        heading.is_header_pollution = True
                        removed_count += 1
                    else:
                        first_occurrence.add(key)

        # =====================================================================
        # Phase 1: 规则骨架构建
        # ---------------------------------------------------------------------
        # 遍历所有标题，用编号模式确定绝对层级。
        # 有编号且置信度 > 0.8 的标题构成文档"骨架"。
        # =====================================================================
        if verbose:
            print("  [2/5] 建立文档骨架（有编号标题）...")

        numbered_indices: list[int] = []
        for idx, heading in enumerate(headings):
            if heading.is_header_pollution:
                continue

            level, numbering, confidence = self.analyzer.infer_level_from_numbering(heading.title)
            if level > 0 and confidence > 0.8:
                heading.inferred_level = level
                heading.numbering = numbering
                heading.inference_method = "rule"
                heading.confidence = confidence
                numbered_indices.append(idx)
                if level != heading.original_level:
                    rule_fixes += 1

        if verbose:
            print(f"        骨架标题数: {len(numbered_indices)}")

        # =====================================================================
        # Phase 2: 区间归属分析
        # ---------------------------------------------------------------------
        # 对每个无编号标题，找到它前后最近的骨架标题，形成归属区间。
        # 后续阶段利用区间信息约束推断结果的合法范围。
        # =====================================================================
        if verbose:
            print("  [3/5] 区间归属分析...")

        def find_interval(idx: int) -> tuple[Optional[HeadingInfo], Optional[HeadingInfo]]:
            """返回标题 idx 所属的区间 [prev_numbered, next_numbered)"""
            prev_numbered = None
            next_numbered = None

            # 向前查找最近的骨架标题
            for i in range(idx - 1, -1, -1):
                if headings[i].inference_method == "rule" and not headings[i].is_header_pollution:
                    prev_numbered = headings[i]
                    break

            # 向后查找最近的骨架标题
            for i in range(idx + 1, len(headings)):
                if headings[i].inference_method == "rule" and not headings[i].is_header_pollution:
                    next_numbered = headings[i]
                    break

            return prev_numbered, next_numbered

        # =====================================================================
        # Phase 3: 内联小节处理
        # ---------------------------------------------------------------------
        # Rules / Permissions / Example 等无编号子标题继承父章节层级+1。
        # 利用区间约束确保层级不会越过下一个同级骨架标题。
        # =====================================================================
        if verbose:
            print("  [4/5] 处理内联小节标题...")

        for idx, heading in enumerate(headings):
            if heading.is_header_pollution or heading.inference_method == "rule":
                continue

            if self.analyzer.is_inline_section_title(heading.title):
                heading.is_inline_section = True
                prev_numbered, next_numbered = find_interval(idx)

                if prev_numbered:
                    heading.parent_section = prev_numbered.numbering
                    # 内联小节层级 = 父章节层级 + 1
                    target_level = prev_numbered.inferred_level + 1

                    # 区间约束：不能成为下一个骨架标题的"父级"
                    if next_numbered:
                        min_level = prev_numbered.inferred_level + 1
                        target_level = max(target_level, min_level)

                    heading.inferred_level = min(target_level, 6)
                    heading.inference_method = "inline"
                    heading.confidence = 0.85
                else:
                    # 无前置骨架标题，用基础层级兜底
                    heading.inferred_level = base_level + 1
                    heading.inference_method = "inline"
                    heading.confidence = 0.6

                if heading.inferred_level != heading.original_level:
                    inline_fixes += 1

        # =====================================================================
        # Phase 4: LLM / 上下文推断
        # ---------------------------------------------------------------------
        # 对剩余 inference_method == "original" 的标题：
        #   - 有 LLM → 逐个调用 LLM，结合区间约束
        #   - 无 LLM → 用规则回退（特殊章节名 + 位置启发式）
        # =====================================================================
        unresolved = [h for h in headings
                      if h.inference_method == "original" and not h.is_header_pollution]

        if unresolved:
            if verbose:
                print(f"  [5/5] 处理 {len(unresolved)} 个其他无编号标题...")
            if use_llm and self.llm:
                llm_fixes = self._fix_with_llm(
                    headings, unresolved, base_level, find_interval, verbose)
            else:
                if verbose:
                    print("        使用上下文推断...")
                self._fix_with_context(headings, unresolved, base_level, find_interval)
        elif verbose:
            print("  [5/5] 无需额外推断")

        # 汇总统计
        fixed_count = sum(
            1 for h in headings
            if h.inferred_level != h.original_level and not h.is_header_pollution
        )

        return FixResult(
            source_file="",
            total_headings=len(headings),
            fixed_headings=fixed_count,
            rule_based_fixes=rule_fixes,
            llm_based_fixes=llm_fixes,
            removed_headers=removed_count,
            inline_section_fixes=inline_fixes,
            headings=headings,
        )

    # -------------------------------------------------------------------------
    # 上下文推断（纯规则回退）
    # -------------------------------------------------------------------------

    def _fix_with_context(self, all_headings: list[HeadingInfo],
                          unresolved: list[HeadingInfo], base_level: int,
                          find_interval) -> None:
        """
        基于上下文的启发式推断（不依赖 LLM）。

        利用区间约束 + 特殊章节名匹配 + 位置启发式确定层级。
        """
        for heading in unresolved:
            idx = all_headings.index(heading)
            prev_numbered, next_numbered = find_interval(idx)
            title_lower = heading.title.lower()

            # 计算区间允许的层级范围
            min_level = base_level
            max_level = 6

            if prev_numbered:
                # 无编号标题必须是前一个骨架标题的子级
                min_level = prev_numbered.inferred_level + 1

            if next_numbered and prev_numbered:
                if next_numbered.inferred_level <= prev_numbered.inferred_level:
                    # 前后骨架标题同级或后者更高 → 当前标题是插入的子节
                    min_level = max(min_level, prev_numbered.inferred_level + 1)

            # 文档主标题模式 — 仅在文档开头才可能是 H1
            if any(kw in title_lower for kw in [
                'standard for', 'ieee standard', 'iso standard',
                'specification', 'draft', 'revision',
            ]):
                if idx == 0 or (idx < 3 and not prev_numbered):
                    heading.inferred_level = base_level
                else:
                    heading.inferred_level = max(min_level, base_level + 1)
                heading.inference_method = "context"
                heading.confidence = 0.7
                continue

            # 常见的顶层特殊章节名
            special_sections = {
                'abstract': base_level + 1,
                'foreword': base_level + 1,
                'introduction': base_level + 1,
                'scope': base_level + 1,
                'normative references': base_level + 1,
                'terms and definitions': base_level + 1,
                'bibliography': base_level + 1,
                'annex': base_level + 1,
                'important notice': base_level + 1,
                'acknowledgments': base_level + 1,
                'overview': base_level + 1,
            }

            found_special = False
            for keyword, level in special_sections.items():
                if keyword in title_lower:
                    target = max(level, min_level)
                    heading.inferred_level = min(target, max_level)
                    heading.inference_method = "context"
                    heading.confidence = 0.6
                    found_special = True
                    break

            if not found_special:
                # 默认：作为前一个骨架标题的子级
                if prev_numbered:
                    heading.inferred_level = min(min_level, max_level)
                    heading.inference_method = "context"
                    heading.confidence = 0.5
                else:
                    heading.inferred_level = base_level if idx == 0 else base_level + 1
                    heading.inference_method = "context"
                    heading.confidence = 0.3

    # -------------------------------------------------------------------------
    # LLM 推断（带区间约束）
    # -------------------------------------------------------------------------

    def _fix_with_llm(self, all_headings: list[HeadingInfo],
                      unresolved: list[HeadingInfo], base_level: int,
                      find_interval, verbose: bool = True) -> int:
        """
        使用 LLM 推断无编号标题的层级。

        构建文档标题列表作为上下文，结合区间约束信息生成 prompt，
        逐个调用 LLM 获取层级判断。LLM 失败时回退到上下文推断。

        Returns:
            LLM 成功修正的标题数
        """
        if not self.llm:
            return 0

        llm_fixes = 0
        total = len(unresolved)

        if verbose:
            print(f"        使用 LLM 推断 {total} 个标题...")

        # 构建上下文：所有标题的层级概览
        context_lines = []
        for h in all_headings:
            if h.inference_method == "rule":
                context_lines.append(f"[H{h.inferred_level}] {h.title}")
            elif h.inference_method == "inline":
                context_lines.append(f"[H{h.inferred_level}] {h.title} (子节)")
            else:
                context_lines.append(f"[??] {h.title}")
        context = "\n".join(context_lines)

        # 逐个推断
        for heading in unresolved:
            idx = all_headings.index(heading)
            prev_numbered, next_numbered = find_interval(idx)

            # 区间约束范围
            min_level = base_level
            max_level = 6
            if prev_numbered:
                min_level = prev_numbered.inferred_level + 1

            if verbose:
                title_preview = heading.title[:30] + "..." if len(heading.title) > 30 else heading.title
                constraint_info = f" (约束: H{min_level}-H{max_level})" if prev_numbered else ""
                print(f"        分析: {title_preview}{constraint_info}", end="", flush=True)

            # 构建带约束信息的 prompt
            constraint_text = ""
            if prev_numbered:
                constraint_text = f"""
重要约束:
- 这个标题位于 "{prev_numbered.title}" (H{prev_numbered.inferred_level}) 之后
- 因此层级必须 >= H{min_level}（作为其子级或后续独立章节）"""
                if next_numbered:
                    constraint_text += f"""
- 下一个有编号的标题是 "{next_numbered.title}" (H{next_numbered.inferred_level})
- 所以这个标题不太可能是 H{next_numbered.inferred_level} 或更高级别"""

            prompt = f"""分析以下 Markdown 文档的标题层级结构。

已知的标题列表 (H1-H6 表示已确定层级，?? 表示待确定):
{context}

请判断标题 "{heading.title}" 应该是什么层级 (H1-H6)?
{constraint_text}

规则:
- H1: 文档主标题 (如 "IEEE Standard for...")
- H2: 一级章节 (如 "1. Overview", "Abstract", "Introduction")
- H3: 二级章节 (如 "1.1 Scope")
- H4: 三级章节或子节 (如 "1.1.1 Details", "Example", "Note")
- H5/H6: 更深层级

只回答一个数字 ({min_level}-{max_level})，不要其他内容。"""

            system = "你是一个文档结构分析专家。只输出数字答案，不要解释。"

            response = self.llm.generate(prompt, system, temperature=0.1)

            # 解析 LLM 响应
            try:
                match = re.search(r'[1-6]', response)
                if match:
                    level = int(match.group())
                    # 强制区间约束
                    level = max(level, min_level)
                    level = min(level, max_level)

                    heading.inferred_level = level
                    heading.inference_method = "llm"
                    heading.confidence = 0.75
                    if verbose:
                        print(f" -> H{level}")
                    if level != heading.original_level:
                        llm_fixes += 1
                else:
                    if verbose:
                        print(" -> 回退到上下文推断")
                    self._fix_with_context(all_headings, [heading], base_level, find_interval)
            except (ValueError, AttributeError):
                if verbose:
                    print(" -> 回退到上下文推断")
                self._fix_with_context(all_headings, [heading], base_level, find_interval)

        return llm_fixes


# =============================================================================
# Markdown 输出器
# =============================================================================

class MarkdownWriter:
    """
    Markdown 输出器 — 根据修复结果生成新的 Markdown 内容。

    职责：
      - 替换标题的 # 前缀为推断出的正确层级
      - 移除被标记为页眉污染的标题行（及其后续空行）
    """

    def __init__(self, lines: list[str], headings: list[HeadingInfo]):
        self.lines = lines
        self.headings = headings
        # 行号 → 标题信息的快速查找表
        self.heading_map = {h.line_num: h for h in headings}

    def generate(self, remove_polluted: bool = True) -> str:
        """
        生成修复后的 Markdown 文本。

        Args:
            remove_polluted: 是否移除被标记为页眉污染的行
        """
        output_lines = []
        skip_next_empty = False     # 移除页眉后跳过紧随的空行

        for i, line in enumerate(self.lines):
            line_num = i + 1

            if line_num in self.heading_map:
                heading = self.heading_map[line_num]

                # 页眉污染 → 整行跳过
                if remove_polluted and heading.is_header_pollution:
                    skip_next_empty = True
                    continue

                # 替换标题层级前缀
                new_prefix = "#" * heading.inferred_level
                match = HEADING_PATTERN.match(line.strip())
                if match:
                    title = match.group(2)
                    output_lines.append(f"{new_prefix} {title}\n")
                else:
                    output_lines.append(line)
                skip_next_empty = False
            else:
                # 移除页眉后紧随的空行
                if skip_next_empty and line.strip() == "":
                    skip_next_empty = False
                    continue
                output_lines.append(line)
                if line.strip() != "":
                    skip_next_empty = False

        return "".join(output_lines)


# =============================================================================
# CLI 入口
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="aidoc_fix_hierarchy - 标题层级修复工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例用法:
  # 基本用法 - 修复层级退化问题
  %(prog)s document.md

  # 预览模式 - 只显示修复建议，不修改文件
  %(prog)s document.md --dry-run

  # 不使用 LLM，只用规则和上下文推断
  %(prog)s document.md --no-llm

  # 指定输出文件
  %(prog)s document.md -o fixed_document.md

  # 输出详细的修复报告
  %(prog)s document.md --report fix_report.json

修复策略 (五阶段算法):
  Phase 0 - 页眉污染移除:
     自动检测重复出现的文档标题 (如每页的页眉)
     保留第一次出现，移除后续重复

  Phase 1 - 基于编号规则推断 (高置信度):
     1. / 2. / 3. -> H2 (一级章节)
     1.1 / 2.3    -> H3 (二级章节)
     1.1.1        -> H4 (三级章节)
     1.1.1.1      -> H5 (四级章节)
     A. / Annex A -> H2 (附录)
     A.1 / A.1.1  -> H3/H4 (附录子章节)

  Phase 2 - 区间归属分析:
     将无编号标题归属到前后有编号标题构成的区间

  Phase 3 - 内联小节标题修复:
     Rules, Permissions, Recommendations 等
     自动继承父章节层级 + 1

  Phase 4 - LLM 辅助推断 (处理无编号标题):
     文档标题 -> H1
     Abstract, Introduction -> H2
     IMPORTANT NOTICE -> H2
        """,
    )

    # 文件参数
    parser.add_argument("input", help="输入的 Markdown 文件路径")
    parser.add_argument("-o", "--output", help="输出的修复后文件路径 (默认: 覆盖原文件)")

    # 修复选项
    parser.add_argument("--dry-run", action="store_true",
                        help="预览模式，不修改文件")
    parser.add_argument("--keep-headers", action="store_true",
                        help="保留重复的页眉标题（不移除页眉污染）")
    parser.add_argument("--base-level", type=int, default=1, choices=[1, 2],
                        help="文档标题的基础层级 (默认: 1 表示 H1)")
    parser.add_argument("--report", help="输出详细修复报告 (JSON 格式)")
    parser.add_argument("-v", "--verbose", action="store_true", help="详细输出")

    # LLM 参数（通过 aidoc_llm 统一添加）
    add_llm_args(parser)

    args = parser.parse_args()

    # 检查输入文件
    input_path = Path(args.input)
    if not input_path.exists():
        print(f"错误: 文件不存在 - {input_path}")
        sys.exit(1)

    # 读取文件
    print_banner("aidoc_fix_hierarchy - 标题层级修复工具")
    print(f"输入文件: {input_path}")

    with open(input_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    print(f"文件行数: {len(lines)}")

    # 初始化 LLM 客户端
    llm = create_llm_client(args)
    if llm:
        print(f"LLM 后端: {llm}")
    else:
        print("LLM: (已禁用，仅使用规则引擎)")

    # 执行修复
    print("\n分析标题层级...")
    fixer = HierarchyFixer(lines, llm)
    result = fixer.fix(
        use_llm=(llm is not None),
        base_level=args.base_level,
        remove_headers=not args.keep_headers,
    )
    result.source_file = str(input_path)

    # 显示修复统计
    stats = {
        "总标题数": result.total_headings,
        "需修复数": result.fixed_headings,
        "规则推断": result.rule_based_fixes,
        "内联小节": result.inline_section_fixes,
        "LLM 推断": result.llm_based_fixes,
    }
    if result.removed_headers > 0:
        stats["移除页眉"] = result.removed_headers
    print_stats(stats, "修复结果")

    # 详细输出（verbose 或 dry-run 模式）
    if args.verbose or args.dry_run:
        # 被移除的页眉
        removed = [h for h in result.headings if h.is_header_pollution]
        if removed:
            print("\n移除的页眉污染:")
            print("-" * 60)
            for h in removed:
                print(f"  [移除] L{h.line_num:4d}: {h.title[:50]}")
            print("-" * 60)

        # 层级变更明细
        print("\n标题层级变更:")
        print("-" * 60)
        for h in result.headings:
            if h.is_header_pollution:
                continue
            if h.inferred_level != h.original_level:
                change = f"H{h.original_level} -> H{h.inferred_level}"
                method = f"[{h.inference_method}]"
                title_display = h.title[:45]
                if h.is_inline_section:
                    title_display += f" (in {h.parent_section})"
                print(f"  {change:12} {method:10} {title_display}")
        print("-" * 60)

    # 保存修复报告
    if args.report:
        report_path = Path(args.report)
        report_data = {
            "source_file": result.source_file,
            "total_headings": result.total_headings,
            "fixed_headings": result.fixed_headings,
            "rule_based_fixes": result.rule_based_fixes,
            "inline_section_fixes": result.inline_section_fixes,
            "llm_based_fixes": result.llm_based_fixes,
            "removed_headers": result.removed_headers,
            "headings": [
                {
                    "line_num": h.line_num,
                    "original_level": h.original_level,
                    "inferred_level": h.inferred_level,
                    "title": h.title,
                    "numbering": h.numbering,
                    "inference_method": h.inference_method,
                    "confidence": h.confidence,
                    "is_header_pollution": h.is_header_pollution,
                    "is_inline_section": h.is_inline_section,
                    "parent_section": h.parent_section,
                }
                for h in result.headings
            ],
        }
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report_data, f, ensure_ascii=False, indent=2)
        print(f"\n修复报告已保存: {report_path}")

    # 预览模式 → 不写文件
    if args.dry_run:
        print("\n[预览模式] 未修改任何文件")
        return

    # 生成修复后的内容并安全写入
    writer = MarkdownWriter(lines, result.headings)
    fixed_content = writer.generate()

    output_path = Path(args.output) if args.output else input_path

    # 原子写入：先写临时文件，再重命名（防止中断导致文件损坏）
    temp_path = output_path.with_suffix(output_path.suffix + ".tmp")
    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            f.write(fixed_content)
        temp_path.replace(output_path)
    except KeyboardInterrupt:
        if temp_path.exists():
            temp_path.unlink()
        print("\n\n已取消，原文件未被修改。")
        sys.exit(1)

    print(f"\n修复后的文件已保存: {output_path}")
    print("完成!")


if __name__ == "__main__":
    main()
