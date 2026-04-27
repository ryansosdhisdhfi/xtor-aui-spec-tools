#!/usr/bin/env python3
"""
aidoc_fix_codeblocks - 代码块边界修复工具
==========================================

问题背景：
PDF 转 Markdown 后，代码块的 ``` 标记可能错位，导致：
1. 正文内容被错误地包含在代码块内
2. 代码内容暴露在代码块外
3. 代码块边界混乱
4. 缩进的 ``` 被当作内容而非边界（4+ 空格缩进在 Markdown 中是代码块语法）
5. 代码块未正确闭合

修复策略：
1. 检测代码块内的"正文行"（Markdown 标题、段落文本、表格等）
2. 检测"畸形 fence"（缩进 4+ 空格的 ```，应该是边界但被当作内容）
3. 在正文行周围插入适当的 ``` 标记来分割代码块
4. 修复畸形 fence 的缩进
5. 可选：对复杂情况调用 LLM 辅助判断
6. 清理产生的空代码块和多余标记

使用方法：
    python3 aidoc_fix_codeblocks.py input.md -o output.md
    python3 aidoc_fix_codeblocks.py input.md --dry-run       # 只分析不修改
    python3 aidoc_fix_codeblocks.py input.md --verbose        # 显示详细信息
    python3 aidoc_fix_codeblocks.py input.md --no-llm         # 禁用 LLM（默认启用）
    python3 aidoc_fix_codeblocks.py input.md --fix-indent     # 修复缩进的 fence
"""

import re
import argparse
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional
from enum import Enum

from aidoc_utils import (
    CODE_FENCE_PATTERN,
    INDENTED_FENCE_PATTERN,
    print_banner,
    print_stats,
)
from aidoc_llm import add_llm_args, create_llm_client, extract_json, LLMClient


# =============================================================================
# 问题类型与置信度枚举
# =============================================================================

class IssueType(Enum):
    """
    代码块问题类型分类。

    每种类型对应一种特定的 fence 错位模式，修复策略各不相同：
      - PROSE_IN_CODE:   需要在正文周围插入 fence 来切割代码块
      - CODE_OUTSIDE:    需要在代码周围包裹 fence
      - INDENTED_FENCE:  需要去除多余缩进使 fence 生效
      - UNCLOSED_BLOCK:  需要在文件末尾补充闭合 fence
      - ORPHAN_FENCE:    孤立的 fence，可能是 OCR/转换残留
      - FENCE_MISMATCH:  开闭 fence 的 ` 和 ~ 类型不匹配
    """
    PROSE_IN_CODE = 'prose_in_code'
    CODE_OUTSIDE = 'code_outside'
    INDENTED_FENCE = 'indented_fence'
    UNCLOSED_BLOCK = 'unclosed_block'
    ORPHAN_FENCE = 'orphan_fence'
    FENCE_MISMATCH = 'fence_mismatch'


class Confidence(Enum):
    """
    检测置信度三级分类。

    置信度决定修复策略：
      - HIGH:   规则引擎高度确信，直接自动修复
      - MEDIUM: 有一定把握但存在歧义，建议 LLM 验证后修复
      - LOW:    不确定，必须 LLM 判断或人工确认
    """
    HIGH = 'high'
    MEDIUM = 'medium'
    LOW = 'low'


# =============================================================================
# 数据类定义
# =============================================================================

@dataclass
class Issue:
    """代码块问题描述，包含位置、类型、上下文和置信度"""
    line_num: int                                       # 问题所在行号（1-based）
    issue_type: IssueType                               # 问题类型
    content: str                                        # 问题行内容（截断）
    context: str = ""                                   # 人可读的上下文描述
    confidence: Confidence = Confidence.HIGH             # 检测置信度
    suggested_fix: str = ""                             # 建议的修复内容
    surrounding_lines: list = field(default_factory=list)  # 周围行用于 LLM 上下文


@dataclass
class CodeBlockState:
    """
    代码块状态追踪器。

    在逐行扫描过程中维护当前是否处于代码块内部，
    以及当前代码块的起始信息，用于匹配 fence 对。
    """
    in_code: bool = False          # 当前是否在代码块内
    start_line: int = 0            # 当前代码块起始行号
    fence_type: str = ""           # 开始 fence 的字符类型（``` 或 ~~~）
    indent_level: int = 0          # 开始 fence 的缩进空格数


# =============================================================================
# 代码块分析器
# =============================================================================

class CodeBlockAnalyzer:
    """
    Markdown 代码块问题分析器。

    检测策略采用「多模式匹配 + 反向排除」的两阶段方法：
      1. 正向匹配：通过正文特征模式（标题、长段落、表格等）识别不应出现在代码块内的行
      2. 反向排除：通过代码特征模式（语法关键字、分号结尾等）排除误报
      3. 置信度评估：根据特征强度和上下文为每个问题分配置信度
    """

    # ---- 正文特征模式（不应出现在代码块内） ----
    PROSE_PATTERNS = [
        # Markdown 标题（# 到 ######）
        re.compile(r'^#{1,6}\s+\w'),
        # 以大写字母开头的长段落（>50字符，通常是英文正文）
        re.compile(r'^[A-Z][a-z].{50,}'),
        # Markdown 表格行
        re.compile(r'^\|[^|]+\|[^|]+\|'),
        # Markdown 图片引用
        re.compile(r'^!\[.*\]\(.*\)'),
        # Markdown 链接（独占一行）
        re.compile(r'^\[.*\]\(.*\)$'),
        # 无序列表项（长句子形式，通常是正文）
        re.compile(r'^[-*]\s+[A-Z][a-z].{30,}'),
        # 有序列表项
        re.compile(r'^\d+\.\s+[A-Z][a-z].{20,}'),
        # 引用块
        re.compile(r'^>\s+[A-Z]'),
    ]

    # ---- 代码特征模式（应出现在代码块内） ----
    CODE_PATTERNS = [
        # VHDL 关键字
        re.compile(r'\b(entity|architecture|port|signal|begin|end\s+\w+|process|component)\b', re.I),
        # Verilog 关键字
        re.compile(r'\b(module|endmodule|wire|reg|assign|always|input|output|inout)\b', re.I),
        # SystemVerilog 关键字
        re.compile(r'\b(interface|endinterface|class|endclass|function|endfunction|task|endtask)\b', re.I),
        # C/C++ 关键字
        re.compile(r'\b(void|int|char|return|struct|typedef|#include|#define)\b'),
        # Python 关键字
        re.compile(r'\b(def\s+\w+|class\s+\w+|import\s+\w+|from\s+\w+\s+import)\b'),
        # 运算符/赋值符号（多种语言通用）
        re.compile(r':=|<=|=>|==|!=|\+=|-=|\*=|/=|&&|\|\|'),
        # 函数调用或定义（括号 + 可选分号/花括号）
        re.compile(r'\w+\s*\([^)]*\)\s*[;{]?$'),
        # VHDL 字符串连接 & 运算符
        re.compile(r'"\s*&\s*$'),
        re.compile(r'^\s*".*"\s*&'),
        # 各语言注释
        re.compile(r'^\s*(--|//|#(?!#)|/\*)'),
        # VHDL attribute 声明
        re.compile(r'^\s*attribute\s+\w+', re.I),
        # 缩进 + 控制流关键字（代码块内的典型缩进结构）
        re.compile(r'^\s{4,}(if|for|while|return|begin|end)\b'),
    ]

    # ---- 排除模式：匹配了正文模式但实际是代码的情况 ----
    NOT_PROSE_PATTERNS = [
        re.compile(r'^\s*--'),      # VHDL/Ada 行注释
        re.compile(r'^\s*//'),      # C/C++/Java 行注释
        re.compile(r'^\s*"'),       # 字符串字面量
        re.compile(r'.*;$'),        # 分号结尾（代码语句）
        re.compile(r'.*\{$'),       # 花括号结尾（代码块开始）
    ]

    # 强代码语法特征（单字符/双字符标记，出现即可排除正文误判）
    STRONG_CODE_INDICATORS = [';', '{', '}', ':=', '<=', '=>', '()', '[]', '->']

    def __init__(self, verbose: bool = False):
        self.verbose = verbose

    def analyze(self, lines: list[str], options: Optional[dict] = None) -> list[Issue]:
        """
        分析文件内容，返回检测到的所有代码块问题。

        核心逻辑：逐行扫描，维护 fence 开关状态，在代码块内部检测正文行，
        在代码块外部可选检测裸露的代码行。

        Args:
            lines: 文件按行分割的列表
            options: 分析选项
                - check_code_outside:    是否检查代码块外的代码（默认 False，误报率较高）
                - check_indented_fences: 是否检查畸形缩进 fence（默认 True）

        Returns:
            Issue 列表，按行号顺序排列
        """
        if options is None:
            options = {}

        check_code_outside = options.get('check_code_outside', False)
        check_indented_fences = options.get('check_indented_fences', True)

        issues = []
        state = CodeBlockState()
        fence_stack = []  # 记录 fence 开闭配对历史

        for i, line in enumerate(lines):
            line_num = i + 1

            # ---- 步骤 1: 检测合法 fence（0-3 空格缩进） ----
            valid_match = CODE_FENCE_PATTERN.match(line)
            if valid_match:
                indent, fence_chars, lang, rest = valid_match.groups()

                if not state.in_code:
                    # 开启代码块
                    state.in_code = True
                    state.start_line = line_num
                    state.fence_type = fence_chars
                    state.indent_level = len(indent)
                    fence_stack.append((line_num, 'open', fence_chars))
                else:
                    # 尝试关闭代码块：fence 字符类型和长度必须匹配
                    if (fence_chars[0] == state.fence_type[0] and
                            len(fence_chars) >= len(state.fence_type)):
                        state.in_code = False
                        fence_stack.append((line_num, 'close', fence_chars))
                    # 不匹配则视为代码块内的普通内容
                continue

            # ---- 步骤 2: 检测畸形 fence（4+ 空格缩进） ----
            if check_indented_fences:
                indented_match = INDENTED_FENCE_PATTERN.match(line)
                if indented_match:
                    indent, fence_chars, rest = indented_match.groups()
                    surrounding = self._get_surrounding_lines(lines, i, 3)
                    confidence = self._assess_indented_fence_confidence(lines, i, state)

                    issues.append(Issue(
                        line_num=line_num,
                        issue_type=IssueType.INDENTED_FENCE,
                        content=line.rstrip()[:80],
                        context=f"缩进{len(indent)}空格，在{'代码块内' if state.in_code else '代码块外'}",
                        confidence=confidence,
                        suggested_fix=fence_chars + rest.strip(),
                        surrounding_lines=surrounding,
                    ))
                    continue

            # ---- 步骤 3: 代码块内检测正文 ----
            if state.in_code:
                stripped = line.strip()
                if self._is_prose(stripped, lines, i):
                    surrounding = self._get_surrounding_lines(lines, i, 2)
                    confidence = self._assess_prose_confidence(stripped, lines, i)

                    issues.append(Issue(
                        line_num=line_num,
                        issue_type=IssueType.PROSE_IN_CODE,
                        content=stripped[:100],
                        context=f"代码块从第{state.start_line}行开始",
                        confidence=confidence,
                        surrounding_lines=surrounding,
                    ))
            # ---- 步骤 4: 代码块外检测裸露代码（可选，误报率较高） ----
            elif check_code_outside:
                stripped = line.strip()
                if self._is_code(stripped) and len(stripped) > 15:
                    if not self._is_false_positive_code(stripped, lines, i):
                        surrounding = self._get_surrounding_lines(lines, i, 2)
                        issues.append(Issue(
                            line_num=line_num,
                            issue_type=IssueType.CODE_OUTSIDE,
                            content=stripped[:100],
                            context="在代码块外",
                            confidence=Confidence.MEDIUM,
                            surrounding_lines=surrounding,
                        ))

        # ---- 步骤 5: 检查文件结束时未闭合的代码块 ----
        if state.in_code:
            issues.append(Issue(
                line_num=state.start_line,
                issue_type=IssueType.UNCLOSED_BLOCK,
                content=(lines[state.start_line - 1].rstrip()[:80]
                         if state.start_line <= len(lines) else ""),
                context=f"代码块从第{state.start_line}行开始，文件结束时未闭合",
                confidence=Confidence.HIGH,
                suggested_fix="在文件末尾添加 ```",
            ))

        return issues

    # -------------------------------------------------------------------------
    # 上下文提取
    # -------------------------------------------------------------------------

    def _get_surrounding_lines(self, lines: list[str], index: int, radius: int) -> list[str]:
        """获取指定行周围的上下文行，用于 LLM 提示和诊断输出"""
        start = max(0, index - radius)
        end = min(len(lines), index + radius + 1)
        result = []
        for i in range(start, end):
            # 当前行用 >>> 标记，其余用空格对齐
            prefix = ">>>" if i == index else "   "
            result.append(f"{prefix} {i + 1:5d}: {lines[i].rstrip()[:70]}")
        return result

    # -------------------------------------------------------------------------
    # 置信度评估
    # -------------------------------------------------------------------------

    def _assess_indented_fence_confidence(
        self, lines: list[str], index: int, state: CodeBlockState
    ) -> Confidence:
        """
        评估缩进 fence 的置信度。

        判断依据：前后行是否具有代码特征。
        如果前后都是代码，说明这个缩进的 fence 很可能是真正的代码块边界。
        """
        prev_is_code = index > 0 and self._is_code(lines[index - 1].strip())
        next_is_code = index < len(lines) - 1 and self._is_code(lines[index + 1].strip())

        if prev_is_code and next_is_code:
            return Confidence.HIGH
        elif prev_is_code or next_is_code:
            return Confidence.MEDIUM
        else:
            return Confidence.LOW

    def _assess_prose_confidence(
        self, text: str, lines: list[str], index: int
    ) -> Confidence:
        """
        评估「代码块内正文」判断的置信度。

        分级依据：
          - Markdown 标题和表格是最强的正文信号 -> HIGH
          - 长段落（80+ 字符，10+ 单词）较可靠 -> MEDIUM
          - 其余情况存在歧义 -> LOW
        """
        # Markdown 标题：最强正文信号
        if re.match(r'^#{1,6}\s+\w', text):
            return Confidence.HIGH

        # Markdown 表格行
        if re.match(r'^\|[^|]+\|', text):
            return Confidence.HIGH

        # 长段落：较可靠但不绝对
        if len(text) > 80 and len(text.split()) > 10:
            return Confidence.MEDIUM

        return Confidence.LOW

    # -------------------------------------------------------------------------
    # 内容分类判断
    # -------------------------------------------------------------------------

    def _is_prose(self, text: str, lines: list[str] = None, index: int = 0) -> bool:
        """
        判断一行文本是否为正文（不应出现在代码块内）。

        两阶段判断：
          1. 排除检查：如果匹配「非正文」模式（注释、字符串、分号结尾），直接返回 False
          2. 正向检查：如果匹配「正文」模式，再确认没有强代码特征后返回 True
          3. 兜底规则：大写开头 + 足够长 + 足够多单词 = 英文段落
        """
        if not text or len(text) < 15:
            return False

        # 阶段 1: 排除——有代码特征的行不算正文
        for pattern in self.NOT_PROSE_PATTERNS:
            if pattern.match(text):
                return False

        # 阶段 2: 正向匹配正文模式
        for pattern in self.PROSE_PATTERNS:
            if pattern.match(text):
                # 防御：同时有强代码语法的不算正文
                if self._has_strong_code_syntax(text):
                    return False
                return True

        # 阶段 3: 兜底——英文段落启发式
        if (text[0].isupper() and
                len(text) > 60 and
                len(text.split()) > 8 and
                not self._has_strong_code_syntax(text)):
            return True

        return False

    def _is_code(self, text: str) -> bool:
        """判断一行文本是否具有代码特征"""
        if not text:
            return False
        for pattern in self.CODE_PATTERNS:
            if pattern.search(text):
                return True
        return False

    def _has_strong_code_syntax(self, text: str) -> bool:
        """检查是否包含强代码语法标记（分号、花括号、赋值运算符等）"""
        return any(ind in text for ind in self.STRONG_CODE_INDICATORS)

    def _is_false_positive_code(self, text: str, lines: list[str], index: int) -> bool:
        """排除代码块外代码检测的误报（如 Markdown 表格被误判为代码）"""
        # 含多个 | 的行通常是 Markdown 表格
        if '|' in text and text.count('|') >= 2:
            return True
        return False


# =============================================================================
# LLM 辅助判断
# =============================================================================

class LLMHelper:
    """
    LLM 辅助判断器。

    对规则引擎无法高置信度判断的边界情况（MEDIUM/LOW），
    调用 LLM 进行语义级别的验证，提高修复准确率。

    通过 aidoc_llm.LLMClient 统一接口调用，支持 Ollama 和 OpenAI 后端。
    """

    def __init__(self, client: LLMClient):
        """
        Args:
            client: aidoc_llm.LLMClient 实例（OllamaClient 或 OpenAIClient）
        """
        self.client = client

    @property
    def available(self) -> bool:
        """LLM 是否可用"""
        return self.client is not None and self.client.available

    def verify_issue(self, issue: Issue, context_lines: list[str]) -> dict:
        """
        使用 LLM 验证一个检测到的问题是否为真实问题。

        Args:
            issue:         待验证的 Issue
            context_lines: 额外上下文行（优先于 issue.surrounding_lines）

        Returns:
            {
                'is_issue':         bool,   # 是否为真实问题
                'confidence':       float,  # LLM 判断置信度 (0.0-1.0)
                'explanation':      str,    # LLM 给出的理由
                'suggested_action': str,    # 建议动作: fix / skip / keep
            }
        """
        if not self.available:
            return {
                'is_issue': True,
                'confidence': 0.5,
                'explanation': 'LLM 不可用，保持原判断',
                'suggested_action': 'keep',
            }

        prompt = self._build_prompt(issue, context_lines)
        # 低温度以获得更确定的判断
        response = self.client.generate(prompt, system="", temperature=0.1)
        return self._parse_response(response)

    def _build_prompt(self, issue: Issue, context_lines: list[str]) -> str:
        """
        构建 LLM 验证提示词。

        根据问题类型生成针对性的提示，要求 LLM 以 JSON 格式回答。
        """
        context = '\n'.join(context_lines) if context_lines else '\n'.join(issue.surrounding_lines)

        if issue.issue_type == IssueType.INDENTED_FENCE:
            return f"""分析以下 Markdown 代码块边界问题：

第 {issue.line_num} 行有一个缩进的 ``` 标记：
{issue.content}

上下文：
{context}

问题：这个缩进的 ``` 应该是：
A) 代码块边界（需要修复缩进）
B) 代码内容的一部分（保持原样）

请用 JSON 格式回答：
{{"answer": "A" 或 "B", "confidence": 0.0-1.0, "reason": "..."}}"""

        elif issue.issue_type == IssueType.PROSE_IN_CODE:
            return f"""分析以下 Markdown 代码块内容：

第 {issue.line_num} 行在代码块内，但看起来像正文：
{issue.content}

上下文：
{context}

问题：这行内容是：
A) 正文（不应该在代码块内）
B) 代码或代码注释（应该在代码块内）

请用 JSON 格式回答：
{{"answer": "A" 或 "B", "confidence": 0.0-1.0, "reason": "..."}}"""

        return ""

    def _parse_response(self, response: str) -> dict:
        """
        解析 LLM 的 JSON 响应。

        使用 aidoc_llm.extract_json 安全提取，解析失败则回退到保守默认值。
        """
        data = extract_json(response)
        if data:
            try:
                is_issue = data.get('answer', 'A') == 'A'
                return {
                    'is_issue': is_issue,
                    'confidence': float(data.get('confidence', 0.5)),
                    'explanation': data.get('reason', ''),
                    'suggested_action': 'fix' if is_issue else 'skip',
                }
            except (ValueError, TypeError):
                pass

        # 解析失败：保守处理，保留原判断
        return {
            'is_issue': True,
            'confidence': 0.5,
            'explanation': '无法解析 LLM 响应',
            'suggested_action': 'keep',
        }


# =============================================================================
# 代码块修复器
# =============================================================================

class CodeBlockFixer:
    """
    代码块问题修复器。

    修复流程：
      1. 调用 CodeBlockAnalyzer 扫描所有问题
      2. 可选：对中低置信度问题调用 LLM 验证，过滤误报
      3. 分类处理：缩进 fence → 代码块内正文 → 未闭合代码块
      4. 清理：合并相邻的空代码块标记

    修复顺序很重要：先修复缩进 fence 可能会解决部分正文检测问题，
    因为修复 fence 后代码块边界变化，之前"在代码块内"的正文可能不再有问题。
    """

    def __init__(self, verbose: bool = False, llm_client: Optional[LLMClient] = None):
        """
        Args:
            verbose:    是否输出详细日志
            llm_client: LLM 客户端实例，None 则不使用 LLM
        """
        self.verbose = verbose
        self.llm_client = llm_client
        self.analyzer = CodeBlockAnalyzer(verbose)
        self.llm = LLMHelper(llm_client) if llm_client else None
        self.stats = {
            'prose_fixed': 0,
            'indent_fixed': 0,
            'unclosed_fixed': 0,
            'llm_verified': 0,
            'llm_rejected': 0,
        }

    @property
    def use_llm(self) -> bool:
        """是否启用了 LLM 辅助"""
        return self.llm is not None and self.llm.available

    def fix(self, content: str, options: dict = None) -> tuple[str, list[str]]:
        """
        修复代码块问题。

        Args:
            content: 文件内容
            options: 修复选项
                - fix_indent:    是否修复缩进的 fence（默认 True）
                - llm_threshold: 使用 LLM 验证的最低置信度阈值（默认 MEDIUM）

        Returns:
            (修复后的内容, 修改记录列表)
        """
        if options is None:
            options = {}

        fix_indent = options.get('fix_indent', True)
        llm_threshold = options.get('llm_threshold', Confidence.MEDIUM)

        lines = content.split('\n')
        issues = self.analyzer.analyze(lines, {
            'check_indented_fences': fix_indent,
        })

        if not issues:
            return content, []

        # LLM 验证：过滤低置信度问题中的误报
        if self.use_llm:
            issues = self._llm_filter_issues(issues, lines, llm_threshold)

        changes = []

        # 分类问题
        prose_issues = [i for i in issues if i.issue_type == IssueType.PROSE_IN_CODE]
        indent_issues = [i for i in issues if i.issue_type == IssueType.INDENTED_FENCE]
        unclosed_issues = [i for i in issues if i.issue_type == IssueType.UNCLOSED_BLOCK]

        # 修复顺序 1: 缩进 fence（可能连带解决其他问题）
        if fix_indent and indent_issues:
            lines, indent_changes = self._fix_indented_fences(lines, indent_issues)
            changes.extend(indent_changes)
            self.stats['indent_fixed'] += len(indent_changes)

        # 修复顺序 2: 代码块内的正文（在正文周围插入 fence 切割）
        if prose_issues:
            lines, prose_changes = self._fix_prose_in_code(lines, prose_issues)
            changes.extend(prose_changes)
            self.stats['prose_fixed'] += len(prose_changes)

        # 修复顺序 3: 未闭合的代码块（在文件末尾补充 fence）
        if unclosed_issues:
            lines, unclosed_changes = self._fix_unclosed_blocks(lines, unclosed_issues)
            changes.extend(unclosed_changes)
            self.stats['unclosed_fixed'] += len(unclosed_changes)

        # 清理：合并相邻的空代码块标记（修复过程可能产生 ``` ``` 对）
        result = self._cleanup_fences('\n'.join(lines))

        return result, changes

    # -------------------------------------------------------------------------
    # LLM 过滤
    # -------------------------------------------------------------------------

    def _llm_filter_issues(
        self, issues: list[Issue], lines: list[str], threshold: Confidence
    ) -> list[Issue]:
        """
        使用 LLM 过滤低置信度问题。

        高于阈值的直接保留，低于阈值的逐个调用 LLM 验证。
        """
        filtered = []

        threshold_order = {Confidence.HIGH: 3, Confidence.MEDIUM: 2, Confidence.LOW: 1}
        threshold_value = threshold_order[threshold]

        for issue in issues:
            issue_confidence = threshold_order[issue.confidence]

            # 高于阈值：直接保留，无需 LLM 验证
            if issue_confidence >= threshold_value:
                filtered.append(issue)
                continue

            # 低于阈值：调用 LLM 做二次验证
            if self.verbose:
                print(f"  LLM 验证: 行 {issue.line_num} ({issue.confidence.value})")

            result = self.llm.verify_issue(issue, issue.surrounding_lines)

            if result['is_issue'] and result['confidence'] > 0.5:
                filtered.append(issue)
                self.stats['llm_verified'] += 1
                if self.verbose:
                    print(f"    -> 确认问题 ({result['confidence']:.2f}): "
                          f"{result['explanation'][:50]}")
            else:
                self.stats['llm_rejected'] += 1
                if self.verbose:
                    print(f"    -> 排除 ({result['confidence']:.2f}): "
                          f"{result['explanation'][:50]}")

        return filtered

    # -------------------------------------------------------------------------
    # 修复操作
    # -------------------------------------------------------------------------

    def _fix_indented_fences(
        self, lines: list[str], issues: list[Issue]
    ) -> tuple[list[str], list[str]]:
        """
        修复缩进的 fence：去除多余前导空格，使其成为合法 fence。

        从后往前处理以避免行号偏移。
        """
        changes = []

        for issue in sorted(issues, key=lambda x: x.line_num, reverse=True):
            idx = issue.line_num - 1
            old_line = lines[idx]

            # 提取缩进、fence 字符、后续内容
            match = re.match(r'^(\s+)(`{3,}|~{3,})(.*)$', old_line)
            if match:
                indent, fence, rest = match.groups()
                new_line = fence + rest.rstrip()
                lines[idx] = new_line
                changes.append(
                    f"行 {issue.line_num}: 修复缩进 fence "
                    f"'{old_line.strip()[:30]}' -> '{new_line[:30]}'"
                )

        return lines, changes

    def _fix_prose_in_code(
        self, lines: list[str], issues: list[Issue]
    ) -> tuple[list[str], list[str]]:
        """
        修复代码块内的正文：在正文段落前后插入 fence 来切割代码块。

        连续的正文行会被合并为一组，只在组的前后各插入一个 fence，
        避免产生过多的代码块碎片。

        从后往前插入以避免行号偏移。
        """
        changes = []
        insertions = []  # (行号, 'before'|'after', 插入内容)

        # 合并连续的正文行（间隔 <= 1 行的算连续）
        i = 0
        while i < len(issues):
            issue = issues[i]
            start = issue.line_num
            end = start

            j = i + 1
            while j < len(issues) and issues[j].line_num <= end + 2:
                end = issues[j].line_num
                j += 1

            # 在这组正文前插入 ``` 关闭当前代码块
            insertions.append((start, 'before', '```'))
            # 在这组正文后插入 ``` 重新开启代码块
            insertions.append((end, 'after', '```'))

            changes.append(f"行 {start}-{end}: 在正文周围插入代码块边界")
            i = j

        # 从后往前插入
        insertions.sort(key=lambda x: (x[0], x[1] == 'before'), reverse=True)

        for line_num, position, text in insertions:
            idx = line_num - 1
            if position == 'before':
                lines.insert(idx, text)
            else:
                lines.insert(idx + 1, text)

        return lines, changes

    def _fix_unclosed_blocks(
        self, lines: list[str], issues: list[Issue]
    ) -> tuple[list[str], list[str]]:
        """修复未闭合的代码块：在文件末尾追加闭合 fence"""
        changes = []

        for issue in issues:
            lines.append('```')
            changes.append(f"行 {issue.line_num}: 代码块未闭合，在文件末尾添加 ```")

        return lines, changes

    # -------------------------------------------------------------------------
    # 清理
    # -------------------------------------------------------------------------

    def _cleanup_fences(self, content: str) -> str:
        """
        清理修复过程中产生的多余代码块标记。

        模式：连续的两个 fence（中间可能有空行）会形成空代码块，
        直接移除这一对即可。
        """
        lines = content.split('\n')
        result = []
        i = 0

        while i < len(lines):
            line = lines[i]
            stripped = line.strip()

            # 检测 fence 行（纯 fence，可选语言标记）
            if re.match(r'^(`{3,}|~{3,})(\w*)$', stripped):
                # 向后跳过空行，看下一个非空行是否也是同类型 fence
                j = i + 1
                while j < len(lines) and lines[j].strip() == '':
                    j += 1

                if j < len(lines):
                    next_stripped = lines[j].strip()
                    if re.match(r'^(`{3,}|~{3,})(\w*)$', next_stripped):
                        # 类型匹配（都是 ` 或都是 ~）则合并移除
                        if stripped[0] == next_stripped[0]:
                            i = j + 1
                            continue

            result.append(line)
            i += 1

        return '\n'.join(result)


# =============================================================================
# CLI 入口
# =============================================================================

# 问题类型的中文显示名称
_TYPE_NAMES = {
    IssueType.PROSE_IN_CODE: '代码块内的正文',
    IssueType.CODE_OUTSIDE: '代码块外的代码',
    IssueType.INDENTED_FENCE: '缩进的 fence（4+ 空格）',
    IssueType.UNCLOSED_BLOCK: '未闭合的代码块',
    IssueType.ORPHAN_FENCE: '孤立的 fence',
    IssueType.FENCE_MISMATCH: 'fence 不匹配',
}


def main():
    parser = argparse.ArgumentParser(
        description='aidoc_fix_codeblocks - 代码块边界修复工具',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
    %(prog)s input.md                      # 分析并修复，输出到 input_fixed.md
    %(prog)s input.md -o output.md         # 指定输出文件
    %(prog)s input.md --dry-run            # 只分析不修改
    %(prog)s input.md --fix-indent         # 修复缩进的 fence
    %(prog)s input.md --no-llm             # 禁用 LLM 验证
    %(prog)s input.md -v --dry-run         # 详细分析报告
""",
    )

    parser.add_argument('input', help='输入 Markdown 文件')
    parser.add_argument('-o', '--output', help='输出文件（默认：input_fixed.md）')
    parser.add_argument('--dry-run', action='store_true', help='只分析，不修改')
    parser.add_argument('--verbose', '-v', action='store_true', help='显示详细信息')
    parser.add_argument(
        '--fix-indent', action='store_true', default=True,
        help='修复缩进的 fence（默认启用）',
    )
    parser.add_argument(
        '--no-fix-indent', action='store_false', dest='fix_indent',
        help='不修复缩进的 fence',
    )
    parser.add_argument(
        '--check-code-outside', action='store_true',
        help='检查代码块外的代码（可能有误报）',
    )

    # LLM 参数：使用 aidoc_llm 的标准参数组
    add_llm_args(parser)

    args = parser.parse_args()

    # ---- 输入验证 ----
    input_path = Path(args.input)
    if not input_path.exists():
        print(f"错误：文件不存在: {input_path}")
        return 1

    content = input_path.read_text(encoding='utf-8')

    # ---- 打印横幅 ----
    print_banner("aidoc_fix_codeblocks - 代码块边界修复工具")

    # ---- 创建 LLM 客户端（如果启用） ----
    llm_client = create_llm_client(args)

    # ---- 分析阶段 ----
    analyzer = CodeBlockAnalyzer(verbose=args.verbose)
    lines = content.split('\n')
    issues = analyzer.analyze(lines, {
        'check_code_outside': args.check_code_outside,
        'check_indented_fences': args.fix_indent,
    })

    print(f"分析文件: {input_path}")
    print(f"总行数: {len(lines)}")
    print(f"LLM: {llm_client if llm_client else '禁用'}")
    print(f"发现问题: {len(issues)} 处")
    print()

    # ---- 按类型统计 ----
    by_type: dict[IssueType, list[Issue]] = {}
    for issue in issues:
        by_type.setdefault(issue.issue_type, []).append(issue)

    for issue_type, items in by_type.items():
        print(f"  {_TYPE_NAMES.get(issue_type, issue_type.value)}: {len(items)} 处")

        # 详细模式：按置信度细分
        if args.verbose:
            by_conf: dict[Confidence, list[Issue]] = {}
            for item in items:
                by_conf.setdefault(item.confidence, []).append(item)
            for conf, conf_items in sorted(by_conf.items(), key=lambda x: x[0].value):
                print(f"    - {conf.value}: {len(conf_items)} 处")

    # ---- 详细模式：打印问题详情（最多 30 条） ----
    if args.verbose and issues:
        print("\n=== 问题详情（前 30 个）===")
        for issue in issues[:30]:
            print(f"\n行 {issue.line_num:5d} [{issue.issue_type.value}] ({issue.confidence.value}):")
            print(f"  内容: {issue.content[:70]}...")
            if issue.context:
                print(f"  上下文: {issue.context}")
            if issue.surrounding_lines:
                print("  周围行:")
                for sl in issue.surrounding_lines:
                    print(f"    {sl}")

    # ---- dry-run 模式到此为止 ----
    if args.dry_run:
        print("\n--dry-run 模式，不进行修改")
        return 0

    # ---- 修复阶段 ----
    fixer = CodeBlockFixer(verbose=args.verbose, llm_client=llm_client)
    fixed_content, changes = fixer.fix(content, {
        'fix_indent': args.fix_indent,
    })

    if not changes:
        print("\n没有需要修复的问题")
        return 0

    print(f"\n=== 执行了 {len(changes)} 处修复 ===")
    if args.verbose:
        for change in changes[:30]:
            print(f"  {change}")
        if len(changes) > 30:
            print(f"  ... 还有 {len(changes) - 30} 处")

    # ---- LLM 统计 ----
    if fixer.use_llm:
        print_stats({
            'LLM 验证通过': fixer.stats['llm_verified'],
            'LLM 排除': fixer.stats['llm_rejected'],
        }, title="LLM 统计")

    # ---- 输出结果 ----
    if args.output:
        output_path = Path(args.output)
    else:
        output_path = input_path.with_stem(input_path.stem + '_fixed')

    output_path.write_text(fixed_content, encoding='utf-8')
    print(f"\n输出文件: {output_path}")

    # ---- 验证修复效果 ----
    fixed_lines = fixed_content.split('\n')
    new_issues = analyzer.analyze(fixed_lines, {
        'check_indented_fences': args.fix_indent,
    })

    print_stats({
        '修复前问题': len(issues),
        '执行修复': len(changes),
        '修复后剩余': len(new_issues),
        '缩进 fence 修复': fixer.stats['indent_fixed'],
        '正文切割修复': fixer.stats['prose_fixed'],
        '未闭合修复': fixer.stats['unclosed_fixed'],
    }, title="修复统计")

    return 0


if __name__ == '__main__':
    exit(main())
