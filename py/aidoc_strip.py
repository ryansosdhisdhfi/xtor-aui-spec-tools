#!/usr/bin/env python3
"""
aidoc_strip - 页眉页脚剥离工具
====================================

将 PDF 转换的 Markdown 中残留的页眉、页脚、页码、水印等重复行清除，
修复被分页打断的代码块和表格。

四层检测架构:
    输入 Markdown
         ↓
    ┌──────────────────────────────────────────────────────┐
    │  Layer 1: PatternDetector  - 统计高频重复行           │
    │  Layer 2: HeuristicFilter  - 结构特征分类 & 置信度    │
    │  Layer 3: LLMValidator     - 中置信度调 LLM 确认      │
    │  Layer 3.5: CodeBlockCleaner - 代码块内特征词组清理    │
    │  Layer 4: ContentMerger    - 合并被分割的代码块/表格   │
    └──────────────────────────────────────────────────────┘
         ↓
    输出: 清理后的 Markdown

置信度系统:
    初始 = 0.4 + (count/total)*50 + (count/100)*0.3
    页码 +0.25 / 水印 +0.20 / 页眉页脚 +0.15 / LLM 确认 +0.20 / LLM 判正文 -0.30
    >= 0.6 直接删除  |  [0.35, 0.6) 调 LLM  |  < 0.35 丢弃

使用示例:
    python3 aidoc_strip.py input.md -o output.md
    python3 aidoc_strip.py input.md --no-llm
    python3 aidoc_strip.py input.md --interactive
    python3 aidoc_strip.py input.md --verbose --dry-run
"""

import re
import argparse
from pathlib import Path
from dataclasses import dataclass, field
from collections import Counter
from difflib import SequenceMatcher
from typing import Optional

# 共享模块
from aidoc_llm import add_llm_args, create_llm_client, extract_json, LLMClient
from aidoc_utils import (
    find_code_block_lines,
    normalize_whitespace,
    print_banner,
    print_stats,
)


# =============================================================================
# 数据类
# =============================================================================

@dataclass
class DetectedPattern:
    """表示一条检测到的页眉/页脚模式。"""
    text: str
    pattern_type: str  # 'header', 'footer', 'page_number', 'watermark', 'unknown', 'content'
    count: int
    confidence: float  # 0.0 - 1.0
    line_numbers: list[int] = field(default_factory=list)
    similar_variants: list[str] = field(default_factory=list)

    def __str__(self):
        return f"[{self.pattern_type.upper()}] ({self.count}x, {self.confidence:.0%}) {self.text[:60]}..."


@dataclass
class ProcessingStats:
    """处理过程统计数据。"""
    original_lines: int = 0
    final_lines: int = 0
    patterns_detected: int = 0
    patterns_removed: int = 0
    llm_validations: int = 0
    code_blocks_merged: int = 0
    tables_merged: int = 0
    removed_lines: list = field(default_factory=list)   # (行号, 内容, 原因)
    cleanup_lines: list = field(default_factory=list)    # (行号, 内容, 原因)


# =============================================================================
# Layer 1: 统计模式检测器
# =============================================================================

class PatternDetector:
    """
    统计模式检测器 — 通过频率分析发现重复行。

    四种检测手段:
      1. 精确频率 — 完全相同的行出现 N 次
      2. 归一化频率 — 替换页码后再计数 ("Page 1" ≈ "Page 2")
      3. 相似行聚类 — 编辑距离 < 0.25 归为同一模式（OCR 容错）
      4. 页码前缀 — "35 Some Footer Text" 模式
    """

    def __init__(self, min_frequency: int = 5, similarity_threshold: float = 0.75):
        """
        Args:
            min_frequency:      最少出现次数才视为模式
            similarity_threshold: 相似行聚类阈值 (0.75 = 75% 相似)
        """
        self.min_frequency = min_frequency
        self.similarity_threshold = similarity_threshold

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    def analyze(self, lines: list[str]) -> list[DetectedPattern]:
        """分析所有行，返回按置信度降序排列的候选模式列表。"""
        patterns = []
        patterns.extend(self._analyze_exact_frequency(lines))
        patterns.extend(self._analyze_normalized_frequency(lines))
        patterns.extend(self._cluster_similar_lines(lines))
        patterns.extend(self._detect_common_footers(lines))

        patterns = self._deduplicate_patterns(patterns)
        patterns.sort(key=lambda p: (p.confidence, p.count), reverse=True)
        return patterns

    # ------------------------------------------------------------------
    # 精确频率
    # ------------------------------------------------------------------

    def _analyze_exact_frequency(self, lines: list[str]) -> list[DetectedPattern]:
        """统计完全相同行的出现次数。"""
        counter: Counter = Counter()
        line_positions: dict[str, list[int]] = {}

        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped and len(stripped) > 3:
                counter[stripped] += 1
                line_positions.setdefault(stripped, []).append(i)

        total = len(lines)
        threshold = max(self.min_frequency, total // 100)
        patterns = []

        for text, count in counter.items():
            if count >= threshold:
                ratio = count / max(1, total)
                confidence = min(0.9, 0.4 + ratio * 50 + (count / 100) * 0.3)
                patterns.append(DetectedPattern(
                    text=text, pattern_type='unknown', count=count,
                    confidence=confidence, line_numbers=line_positions[text],
                ))
        return patterns

    # ------------------------------------------------------------------
    # 归一化频率 (忽略页码变化)
    # ------------------------------------------------------------------

    _NORMALIZERS = [
        (re.compile(r'\b\d{1,4}\b'), '{NUM}'),
        (re.compile(r'\b[ivxlcdmIVXLCDM]+\b'), '{ROMAN}'),
    ]

    def _analyze_normalized_frequency(self, lines: list[str]) -> list[DetectedPattern]:
        """将页码替换为占位符后再统计，捕获 'Page 1'/'Page 2' 类模式。"""
        normalized_counter: Counter = Counter()
        original_mapping: dict[str, list[str]] = {}

        for line in lines:
            stripped = line.strip()
            if not stripped or len(stripped) <= 3:
                continue
            normalized = stripped
            for pat, repl in self._NORMALIZERS:
                normalized = pat.sub(repl, normalized)
            if normalized != stripped:
                normalized_counter[normalized] += 1
                original_mapping.setdefault(normalized, []).append(stripped)

        total = len(lines)
        threshold = max(self.min_frequency, total // 100)
        patterns = []

        for normalized, count in normalized_counter.items():
            if count >= threshold:
                originals = original_mapping[normalized]
                ratio = count / max(1, total)
                confidence = min(0.85, 0.35 + ratio * 50 + (count / 100) * 0.3)
                patterns.append(DetectedPattern(
                    text=originals[0], pattern_type='unknown', count=count,
                    confidence=confidence, similar_variants=list(set(originals[:10])),
                ))
        return patterns

    # ------------------------------------------------------------------
    # 相似行聚类 (OCR 容错)
    # ------------------------------------------------------------------

    def _cluster_similar_lines(self, lines: list[str]) -> list[DetectedPattern]:
        """用 SequenceMatcher 聚类编辑距离接近的行。"""
        line_counts = Counter(line.strip() for line in lines if line.strip())
        candidates = [t for t, c in line_counts.items() if c >= 2 and 10 < len(t) < 200]

        # 候选行过多时只取最高频的 500 条
        if len(candidates) > 500:
            candidates = [t for t, _ in line_counts.most_common(500) if 10 < len(t) < 200]

        clusters: list[list[str]] = []
        used: set[str] = set()

        for i, text1 in enumerate(candidates):
            if text1 in used:
                continue
            cluster = [text1]
            used.add(text1)
            for text2 in candidates[i + 1:]:
                if text2 in used:
                    continue
                if SequenceMatcher(None, text1, text2).ratio() >= self.similarity_threshold:
                    cluster.append(text2)
                    used.add(text2)
            if len(cluster) >= 2:
                clusters.append(cluster)

        patterns = []
        for cluster in clusters:
            total_count = sum(line_counts[t] for t in cluster)
            if total_count >= self.min_frequency:
                representative = max(cluster, key=lambda t: line_counts[t])
                confidence = min(0.8, 0.3 + (total_count / len(lines)) * 5)
                patterns.append(DetectedPattern(
                    text=representative, pattern_type='unknown', count=total_count,
                    confidence=confidence, similar_variants=cluster,
                ))
        return patterns

    # ------------------------------------------------------------------
    # 页码前缀检测 ("35 Footer Text" 模式)
    # ------------------------------------------------------------------

    _PAGE_PREFIX_RE = re.compile(r'^([ivxlcdmIVXLCDM]+|\d{1,4})\s+(.{10,80})$')

    def _detect_common_footers(self, lines: list[str]) -> list[DetectedPattern]:
        """检测以页码为前缀、后面跟随重复文本的行。"""
        suffix_groups: dict[str, list[tuple[int, str, str]]] = {}

        for i, line in enumerate(lines):
            stripped = line.strip()
            m = self._PAGE_PREFIX_RE.match(stripped)
            if m:
                suffix = m.group(2).strip()
                norm = re.sub(r'\d+', '{N}', suffix)
                suffix_groups.setdefault(norm, []).append((i, stripped, suffix))

        patterns = []
        for _norm, occurrences in suffix_groups.items():
            if len(occurrences) >= self.min_frequency:
                texts = [o[2] for o in occurrences]
                representative = Counter(texts).most_common(1)[0][0]
                confidence = min(0.8, 0.4 + len(occurrences) / len(lines) * 30)
                patterns.append(DetectedPattern(
                    text=representative, pattern_type='footer', count=len(occurrences),
                    confidence=confidence,
                    line_numbers=[o[0] for o in occurrences],
                    similar_variants=list(set(o[1] for o in occurrences[:20])),
                ))
        return patterns

    # ------------------------------------------------------------------
    # 去重
    # ------------------------------------------------------------------

    @staticmethod
    def _deduplicate_patterns(patterns: list[DetectedPattern]) -> list[DetectedPattern]:
        """保留同一文本中置信度最高的版本。"""
        seen: dict[str, DetectedPattern] = {}
        for p in patterns:
            key = p.text.strip().lower()
            if key not in seen or p.confidence > seen[key].confidence:
                seen[key] = p
        return list(seen.values())


# =============================================================================
# Layer 2: 启发式分类器
# =============================================================================

class HeuristicFilter:
    """
    基于结构特征而非内容特征进行分类。

    原则: 用通用符号 (©®™) 而非 hardcode ("Copyright IEEE")。

    分类 -> 置信度调整:
      页码   +0.25 | 水印   +0.20
      页眉   +0.15 | 页脚   +0.15
      判为正文 -> confidence = 0
    """

    # 页码模式 (多语言)
    PAGE_NUMBER_PATTERNS = [
        re.compile(r'^[ivxlcdmIVXLCDM]+$', re.IGNORECASE),
        re.compile(r'^\d{1,4}$'),
        re.compile(r'^Page\s+\d+', re.IGNORECASE),
        re.compile(r'^-\s*\d+\s*-$'),
        re.compile(r'^\d+\s*/\s*\d+$'),
        re.compile(r'^第\s*\d+\s*页', re.IGNORECASE),
        re.compile(r'^Seite\s+\d+', re.IGNORECASE),
        re.compile(r'^Página\s+\d+', re.IGNORECASE),
    ]

    # 页眉结构特征
    HEADER_STRUCTURAL_PATTERNS = [
        re.compile(r'^#{1,3}\s+.{15,}$'),
    ]

    # 页脚结构特征
    FOOTER_STRUCTURAL_PATTERNS = [
        re.compile(r'[©®™]'),
        re.compile(r'\b\d{4}\b.*\b\d{4}\b'),
        re.compile(r'All\s+rights?\s+reserved', re.IGNORECASE),
        re.compile(r'版权|著作权|保留.*权利', re.IGNORECASE),
    ]

    # 水印特征
    WATERMARK_STRUCTURAL_PATTERNS = [
        re.compile(r'(licensed|authorized)\s+(use|to)', re.IGNORECASE),
        re.compile(r'downloaded\s+(on|from|at)', re.IGNORECASE),
        re.compile(r'restrictions?\s+apply', re.IGNORECASE),
    ]

    # ------------------------------------------------------------------
    # 分类入口
    # ------------------------------------------------------------------

    def classify(self, pattern: DetectedPattern) -> DetectedPattern:
        """根据结构特征分类并调整置信度。"""
        text = pattern.text.strip()

        if self._is_page_number(text):
            pattern.pattern_type = 'page_number'
            pattern.confidence = min(1.0, pattern.confidence + 0.25)
            return pattern

        if self._is_likely_header(text, pattern.count):
            pattern.pattern_type = 'header'
            pattern.confidence = min(1.0, pattern.confidence + 0.15)
            return pattern

        if self._is_watermark(text):
            pattern.pattern_type = 'watermark'
            pattern.confidence = min(1.0, pattern.confidence + 0.2)
            return pattern

        if self._is_likely_footer(text):
            pattern.pattern_type = 'footer'
            pattern.confidence = min(1.0, pattern.confidence + 0.15)
            return pattern

        pattern.pattern_type = 'unknown'
        return pattern

    def filter_content_lines(self, pattern: DetectedPattern) -> bool:
        """返回 True 表示该模式可能是正文，应被过滤。"""
        text = pattern.text.strip()

        # 过长 -> 正文
        if len(text) > 300:
            return True

        # 多句子 -> 正文
        if len(re.split(r'[.!?]\s+[A-Z]', text)) > 2:
            return True

        # 以常见段落开头词起始 -> 可能是正文（但 Markdown 标题除外）
        content_starters = [
            r'^(The|This|These|Those|A|An|In|On|At|For|With|By|From)\s+',
            r'^(If|When|While|Although|Because|Since|After|Before)\s+',
            r'^(Note|Example|Figure|Table|See|Refer)\s*[:\-]?\s*\d*',
        ]
        for starter in content_starters:
            if re.match(starter, text, re.IGNORECASE):
                if any(p.match(text) for p in self.HEADER_STRUCTURAL_PATTERNS):
                    return False
                return True

        return False

    # ------------------------------------------------------------------
    # 内部判断函数
    # ------------------------------------------------------------------

    def _is_page_number(self, text: str) -> bool:
        return any(p.match(text) for p in self.PAGE_NUMBER_PATTERNS)

    def _is_likely_header(self, text: str, frequency: int) -> bool:
        if not (15 < len(text) < 200):
            return False
        if any(p.match(text) for p in self.HEADER_STRUCTURAL_PATTERNS):
            return True
        # 高频 + 非句末标点 + 首字母大写 → 可能是页眉
        if frequency >= 10 and not text.rstrip().endswith(('.', '?', '!', '。', '？', '！')):
            if text[0].isupper() or text.startswith('#'):
                return True
        return False

    def _is_likely_footer(self, text: str) -> bool:
        return any(p.search(text) for p in self.FOOTER_STRUCTURAL_PATTERNS)

    def _is_watermark(self, text: str) -> bool:
        return any(p.search(text) for p in self.WATERMARK_STRUCTURAL_PATTERNS)


# =============================================================================
# Layer 3: LLM 验证器 (薄封装 aidoc_llm.LLMClient)
# =============================================================================

class LLMValidator:
    """
    对中置信度模式调用 LLM 进行语义确认。

    触发条件: confidence 在 [0.35, 0.6) 之间
    置信度调整: LLM 判为页眉/页脚 +0.20，判为正文 -0.30

    本类不再自行实现 HTTP 调用，而是委托给 aidoc_llm.LLMClient。
    """

    def __init__(self, client: LLMClient):
        """
        Args:
            client: aidoc_llm.LLMClient 实例 (OllamaClient 或 OpenAIClient)
        """
        self.client = client
        self.available = client.available if client else False

    # ------------------------------------------------------------------
    # 批量验证
    # ------------------------------------------------------------------

    def validate_patterns(self, patterns: list[DetectedPattern],
                          sample_context: list[str] = None) -> list[DetectedPattern]:
        """对一批模式调用 LLM 分类，更新置信度后返回。"""
        if not self.available or not patterns:
            return patterns

        lines_to_check = [f'{i + 1}. "{p.text[:100]}"' for i, p in enumerate(patterns)]
        prompt = self._build_prompt(lines_to_check)

        try:
            response = self.client.generate(prompt, temperature=0.1, max_tokens=1024)
            classifications = extract_json(response) or {}

            for i, pattern in enumerate(patterns):
                key = str(i + 1)
                if key not in classifications:
                    continue
                cls = classifications[key].upper()
                if cls in ('HEADER', 'FOOTER', 'PAGE_NUMBER', 'WATERMARK'):
                    pattern.confidence = min(1.0, pattern.confidence + 0.2)
                    type_map = {
                        'HEADER': 'header', 'FOOTER': 'footer',
                        'PAGE_NUMBER': 'page_number', 'WATERMARK': 'watermark',
                    }
                    pattern.pattern_type = type_map[cls]
                elif cls == 'CONTENT':
                    pattern.confidence = max(0.0, pattern.confidence - 0.3)
                    pattern.pattern_type = 'content'
        except Exception as e:
            print(f"LLM 验证失败: {e}")

        return patterns

    # ------------------------------------------------------------------
    # 单行分类
    # ------------------------------------------------------------------

    def classify_single_line(self, line: str) -> str:
        """对单行调用 LLM 分类，返回小写类别名。失败时返回 'content' (安全默认)。"""
        if not self.available:
            return 'content'

        prompt = (
            "Classify this line from a PDF document as one of:\n"
            "- HEADER: Page header (document title, chapter name)\n"
            "- FOOTER: Page footer (copyright notice, page number with text)\n"
            "- PAGE_NUMBER: Standalone page number\n"
            "- WATERMARK: Download/license watermark\n"
            "- CONTENT: Actual document content\n\n"
            f'Line: "{line[:150]}"\n\n'
            "Reply with ONE word only: HEADER, FOOTER, PAGE_NUMBER, WATERMARK, or CONTENT"
        )

        try:
            response = self.client.generate(prompt, temperature=0.1, max_tokens=64)
            upper = response.strip().upper()
            for cls in ('HEADER', 'FOOTER', 'PAGE_NUMBER', 'WATERMARK', 'CONTENT'):
                if cls in upper:
                    return cls.lower()
        except Exception:
            pass
        return 'content'

    # ------------------------------------------------------------------
    # Prompt
    # ------------------------------------------------------------------

    @staticmethod
    def _build_prompt(lines: list[str]) -> str:
        text = '\n'.join(lines)
        return (
            "You are a document analysis assistant. Classify each line as one of:\n"
            "- HEADER: Page header (document title, chapter name repeated on each page)\n"
            "- FOOTER: Page footer (copyright, page numbers with text)\n"
            "- PAGE_NUMBER: Standalone page number\n"
            "- WATERMARK: Download/license watermark\n"
            "- CONTENT: Actual document content (should NOT be removed)\n\n"
            f"Lines to classify:\n{text}\n\n"
            'Return ONLY a JSON object like: {"1": "HEADER", "2": "FOOTER", "3": "CONTENT"}\n'
            "No explanation, just the JSON."
        )


# =============================================================================
# Layer 3.5: 代码块内部清理器
# =============================================================================

class CodeBlockCleaner:
    """
    在代码块内部检测并移除混入的页眉/页脚。

    代码块内理应只有代码；如果出现版权声明、独立页码等明显非代码行，
    就很可能是 PDF 分页残留。

    检测优先级:
      1. 独立页码        (最可靠)
      2. 版权/时间戳特征  (正则)
      3. 特征词组匹配     (多关键词同现)
      4. 代码语法排除     (防误删)
    """

    # 特征词组: (类型, 关键词列表, 最少匹配数, 说明)
    KEYWORD_GROUPS = [
        ("watermark", ["authorized", "licensed", "use", "limited"], 3, "授权声明"),
        ("watermark", ["authorized", "licensed", "university"], 3, "大学授权"),
        ("watermark", ["downloaded", "from", "utc", "restrictions"], 3, "下载时间戳"),
        ("watermark", ["restrictions", "apply"], 2, "限制声明"),
        ("header", ["ieee", "standard", "for"], 3, "IEEE标准"),
        ("header", ["ieee", "standard", "test", "access"], 4, "IEEE JTAG"),
        ("header", ["ieee", "standard", "boundary", "scan"], 4, "IEEE边界扫描"),
        ("header", ["standard", "for", "test", "access", "port"], 5, "JTAG标准"),
        ("header", ["standard", "boundary", "scan", "architecture"], 4, "边界扫描架构"),
        ("header", ["standard", "for", "systemverilog"], 3, "SystemVerilog标准"),
        ("header", ["standard", "unified", "hardware", "design"], 4, "硬件设计标准"),
    ]

    # 独立页码正则
    PAGE_NUMBER_PATTERNS = [
        re.compile(r'^\s*\d{1,4}\s*$'),
        re.compile(r'^\s*\d{1,4}\.\s*$'),
        re.compile(r'^\s*[ivxlcdmIVXLCDM]+\s*$'),
        re.compile(r'^\s*-\s*\d+\s*-\s*$'),
        re.compile(r'^\s*page\s+\d+\s*$', re.IGNORECASE),
    ]

    # 明确页眉/页脚特征
    HEADER_FOOTER_PATTERNS = [
        re.compile(r'copyright\s*[©®]\s*\d{4}', re.IGNORECASE),
        re.compile(r'[©®]\s*\d{4}'),
        re.compile(r'downloaded\s+on\s+\w+\s+\d+,?\s*\d{4}\s+at\s*\d+:\d+', re.IGNORECASE),
        re.compile(r'(university|college|institute).*downloaded', re.IGNORECASE),
        re.compile(r'authorized.*university', re.IGNORECASE),
    ]

    # 强代码语法特征 (用于排除误判)
    _STRONG_CODE_PATTERNS = [
        re.compile(r'[;{}()\[\]]'),
        re.compile(r':=|<=|=>'),
        re.compile(r'"\s*&\s*'),
        re.compile(r'^\s*(--|//|#)'),
    ]

    # 代码指示器 (更宽泛)
    CODE_INDICATORS = [
        re.compile(r'\s--\s'),
        re.compile(r'\s//\s'),
        re.compile(r'^\s*(--|//|#)'),
        re.compile(r'[;{}()\[\]]'),
        re.compile(r':=|<=|=>'),
        re.compile(r'"\s*&\s*$'),
        re.compile(r'\b(entity|architecture|port|signal|begin|end)\b', re.IGNORECASE),
        re.compile(r'\b(module|wire|reg|assign|always)\b', re.IGNORECASE),
        re.compile(r'\b(def|class|import|return|if|else|for|while)\b'),
    ]

    def __init__(self, llm_validator: Optional[LLMValidator] = None, verbose: bool = False):
        self.llm = llm_validator
        self.verbose = verbose

    # ------------------------------------------------------------------

    def clean_code_blocks(self, content: str, removed_log: list = None) -> tuple[str, int]:
        """清理代码块内部的页眉页脚，返回 (cleaned_content, removed_count)。"""
        if removed_log is None:
            removed_log = []

        lines = content.split('\n')
        result: list[str] = []
        removed_count = 0
        in_code_block = False
        i = 0

        while i < len(lines):
            line = lines[i]
            stripped = line.strip()

            # 代码块边界
            if stripped.startswith('```'):
                in_code_block = not in_code_block
                result.append(line)
                i += 1
                continue

            if in_code_block:
                is_hf, reason = self._detect_header_footer_in_code(stripped)
                if is_hf:
                    # 较长行可选 LLM 二次确认
                    if self.llm and self.llm.available and len(stripped) > 30:
                        if self.llm.classify_single_line(stripped) == 'content':
                            result.append(line)
                            i += 1
                            continue

                    removed_log.append((i + 1, line, f"code_block_{reason}"))
                    removed_count += 1
                    if self.verbose:
                        print(f"  [Code Block] 移除行 {i+1}: {stripped[:60]}...")
                    i += 1
                    continue

            result.append(line)
            i += 1

        return '\n'.join(result), removed_count

    # ------------------------------------------------------------------
    # 内部检测
    # ------------------------------------------------------------------

    def _detect_header_footer_in_code(self, text: str) -> tuple[bool, str]:
        """判断代码块内的一行是否为页眉/页脚。"""
        if not text or len(text) < 5:
            return False, ""

        # 1. 独立页码
        for pat in self.PAGE_NUMBER_PATTERNS:
            if pat.match(text):
                return True, "page_number"

        # 2. 明确页眉/页脚正则
        for pat in self.HEADER_FOOTER_PATTERNS:
            if pat.search(text):
                return True, "header_footer_pattern"

        # 3. 特征词组 (需要多关键词同时出现)
        words = set(re.findall(r'[a-z]+', text.lower()))
        for _ptype, keywords, min_match, desc in self.KEYWORD_GROUPS:
            if sum(1 for kw in keywords if kw in words) >= min_match:
                # 如果同时有强代码语法，不删除
                if self._has_strong_code_syntax(text):
                    continue
                return True, f"keyword_group({desc})"

        return False, ""

    def _has_strong_code_syntax(self, text: str) -> bool:
        """判断是否包含明确的代码语法符号。"""
        return any(p.search(text) for p in self._STRONG_CODE_PATTERNS)

    def _is_code_line(self, text: str) -> bool:
        """判断是否为代码行（任何代码指示器匹配即可）。"""
        return any(p.search(text) for p in self.CODE_INDICATORS)


# =============================================================================
# Layer 4: 内容合并器
# =============================================================================

class ContentMerger:
    """
    删除页眉页脚后的后处理:
      - 合并被分页打断的代码块 (``...`` 结束 + 空行 + ``...`` 开始)
      - 合并被打断的表格行
      - 压缩多余连续空行 (最多保留 2 个)
    """

    def process(self, content: str, cleanup_log: list = None) -> tuple[str, int, int]:
        """返回 (processed_content, code_blocks_merged, tables_merged)。"""
        if cleanup_log is None:
            cleanup_log = []

        content, code_merged = self._merge_code_blocks(content, cleanup_log)
        content, table_merged = self._merge_tables(content, cleanup_log)
        content = self._cleanup_whitespace(content, cleanup_log=cleanup_log)
        return content, code_merged, table_merged

    # ------------------------------------------------------------------
    # 代码块合并
    # ------------------------------------------------------------------

    def _merge_code_blocks(self, content: str, cleanup_log: list) -> tuple[str, int]:
        """
        合并仅被空行分隔的相邻代码块。

        只有当一个代码块结束 (```) 后紧跟空行再开始另一个无语言标识的 ```,
        才将两者合并为一个代码块。
        """
        lines = content.split('\n')
        result: list[str] = []
        merged_count = 0
        in_code_block = False
        i = 0

        while i < len(lines):
            line = lines[i]
            stripped = line.strip()

            if stripped.startswith('```'):
                if not in_code_block:
                    # 代码块开始
                    in_code_block = True
                    result.append(line)
                else:
                    # 代码块结束 — 查看后面是否紧跟另一个代码块
                    in_code_block = False
                    j = i + 1
                    empty_indices: list[int] = []
                    while j < len(lines) and lines[j].strip() == '':
                        empty_indices.append(j)
                        j += 1

                    if j < len(lines) and lines[j].strip() == '```':
                        # 下一个也是纯 ``` (无语言标识) → 合并
                        cleanup_log.append((i + 1, line, "code_block_merge_fence"))
                        for idx in empty_indices:
                            cleanup_log.append((idx + 1, lines[idx], "code_block_merge_empty"))
                        cleanup_log.append((j + 1, lines[j], "code_block_merge_fence"))
                        merged_count += 1
                        in_code_block = True
                        i = j + 1
                        continue
                    else:
                        result.append(line)
            else:
                result.append(line)

            i += 1

        return '\n'.join(result), merged_count

    # ------------------------------------------------------------------
    # 表格合并
    # ------------------------------------------------------------------

    _TABLE_ROW_RE = re.compile(r'^\s*\|.*\|\s*$')

    def _merge_tables(self, content: str, cleanup_log: list) -> tuple[str, int]:
        """合并被空行分隔的连续表格行。"""
        lines = content.split('\n')
        result: list[str] = []
        merged_count = 0
        i = 0

        while i < len(lines):
            line = lines[i]
            result.append(line)

            if self._TABLE_ROW_RE.match(line):
                j = i + 1
                empty_indices: list[int] = []
                while j < len(lines) and lines[j].strip() == '':
                    empty_indices.append(j)
                    j += 1
                if j < len(lines) and self._TABLE_ROW_RE.match(lines[j]):
                    if empty_indices:
                        for idx in empty_indices:
                            cleanup_log.append((idx + 1, lines[idx], "table_merge_empty"))
                        merged_count += 1
                        i = j - 1  # 下次迭代将 append 表格行

            i += 1

        return '\n'.join(result), merged_count

    # ------------------------------------------------------------------
    # 空行清理
    # ------------------------------------------------------------------

    @staticmethod
    def _cleanup_whitespace(content: str, max_empty: int = 2, cleanup_log: list = None) -> str:
        """连续空行超过 max_empty 个时裁剪。"""
        lines = content.split('\n')
        result: list[str] = []
        empty_count = 0

        for line_num, line in enumerate(lines, 1):
            if line.strip() == '':
                empty_count += 1
                if empty_count <= max_empty:
                    result.append(line)
                elif cleanup_log is not None:
                    cleanup_log.append((line_num, "(empty line)", "excess_empty_line"))
            else:
                empty_count = 0
                result.append(line)

        return '\n'.join(result)


# =============================================================================
# 主处理类
# =============================================================================

class MarkdownCleaner:
    """
    协调整个清理流程:
        L1 PatternDetector → L2 HeuristicFilter → L3 LLMValidator
        → L3.5 CodeBlockCleaner → L4 ContentMerger

    置信度阈值:
      HIGH_CONFIDENCE  = 0.6  (直接删除)
      MEDIUM_CONFIDENCE = 0.35 ([0.35, 0.6) 调 LLM)
    """

    HIGH_CONFIDENCE = 0.6
    MEDIUM_CONFIDENCE = 0.35

    def __init__(self, llm_client: Optional[LLMClient] = None,
                 interactive: bool = False, verbose: bool = False):
        """
        Args:
            llm_client:  aidoc_llm.LLMClient 实例，None 则不使用 LLM
            interactive: 交互模式，逐个确认每个检测到的模式
            verbose:     显示详细检测过程
        """
        self.interactive = interactive
        self.verbose = verbose

        self.detector = PatternDetector()
        self.heuristic = HeuristicFilter()
        self.merger = ContentMerger()

        # LLM 相关
        self.llm_validator = LLMValidator(llm_client) if llm_client else None
        self.code_block_cleaner = CodeBlockCleaner(
            llm_validator=self.llm_validator,
            verbose=verbose,
        )

    # ------------------------------------------------------------------

    def process(self, content: str) -> tuple[str, ProcessingStats]:
        """处理 Markdown 内容，返回 (cleaned_content, stats)。"""
        stats = ProcessingStats()
        lines = content.split('\n')
        stats.original_lines = len(lines)

        # --- Layer 1: 统计检测 ---
        if self.verbose:
            print("=== Layer 1: 统计模式检测 ===")
        patterns = self.detector.analyze(lines)
        if self.verbose:
            print(f"发现 {len(patterns)} 个候选模式")

        # --- Layer 2: 启发式分类 ---
        if self.verbose:
            print("\n=== Layer 2: 启发式分类 ===")
            print("分类前:")
            for p in patterns[:10]:
                print(f"  [{p.confidence:.2f}] {p.text[:60]}...")

        for pattern in patterns:
            self.heuristic.classify(pattern)
            if self.heuristic.filter_content_lines(pattern):
                pattern.confidence = 0.0
                pattern.pattern_type = 'content'

        if self.verbose:
            print("分类后:")
            for p in patterns[:10]:
                print(f"  [{p.pattern_type}:{p.confidence:.2f}] {p.text[:50]}...")

        # 过滤低置信度
        patterns = [p for p in patterns if p.confidence >= self.MEDIUM_CONFIDENCE]
        if self.verbose:
            print(f"置信度过滤 (>={self.MEDIUM_CONFIDENCE}) 后: {len(patterns)} 个模式")
            for p in patterns[:10]:
                print(f"  {p}")

        # --- Layer 3: LLM 验证 (仅中置信度) ---
        medium = [p for p in patterns
                  if self.MEDIUM_CONFIDENCE <= p.confidence < self.HIGH_CONFIDENCE]
        if self.llm_validator and self.llm_validator.available and medium:
            if self.verbose:
                print(f"\n=== Layer 3: LLM 验证 ({len(medium)} 个模式) ===")
            self.llm_validator.validate_patterns(medium)
            stats.llm_validations = len(medium)

        # 交互确认
        if self.interactive:
            patterns = self._interactive_confirm(patterns)

        stats.patterns_detected = len(patterns)

        # 筛选待移除模式
        to_remove = [p for p in patterns
                     if p.pattern_type != 'content' and p.confidence >= self.MEDIUM_CONFIDENCE]
        stats.patterns_removed = len(to_remove)

        # 执行移除
        if self.verbose:
            print(f"\n=== 移除 {len(to_remove)} 个模式 ===")
        content = self._remove_patterns(content, to_remove, stats.removed_lines)

        # --- Layer 3.5: 代码块内部清理 ---
        if self.verbose:
            print("\n=== Layer 3.5: 代码块清理 ===")
        content, cb_removed = self.code_block_cleaner.clean_code_blocks(content, stats.removed_lines)
        if self.verbose:
            print(f"从代码块移除 {cb_removed} 行")

        # --- Layer 4: 内容合并 ---
        if self.verbose:
            print("\n=== Layer 4: 内容合并 ===")
        content, code_merged, table_merged = self.merger.process(content, stats.cleanup_lines)
        stats.code_blocks_merged = code_merged
        stats.tables_merged = table_merged
        stats.final_lines = len(content.split('\n'))

        return content, stats

    # ------------------------------------------------------------------
    # 核心删除逻辑
    # ------------------------------------------------------------------

    def _remove_patterns(self, content: str, patterns: list[DetectedPattern],
                         removed_log: list) -> str:
        """
        多层匹配策略:
          1. 精确匹配 — 检测到的文本及其变体
          2. 灵活正则 — 允许数字和空格变化
          3. 相似度匹配 — OCR 变体 (>=80% 直接删, 50-80% 调 LLM)
          4. 内联页脚 — 行首页脚混入正文，仅移除页脚部分
        """
        lines = content.split('\n')
        result: list[str] = []

        # 精确匹配集合
        exact_matches: set[str] = set()
        for p in patterns:
            exact_matches.add(p.text.strip())
            for v in p.similar_variants:
                exact_matches.add(v.strip())

        # 页脚核心文本 (用于相似度匹配)
        footer_cores: list[str] = []
        for p in patterns:
            if p.pattern_type == 'footer':
                core = re.sub(r'^[ivxlcdmIVXLCDM\d]+\s*', '', p.text.strip())
                core = re.sub(r'\d+', '', core)
                if len(core) > 15:
                    footer_cores.append(core.lower())

        # 灵活正则 & 内联正则
        regex_patterns: list[re.Pattern] = []
        inline_patterns: list[re.Pattern] = []
        for p in patterns:
            text = p.text.strip()
            if p.pattern_type in ('footer', 'header', 'watermark'):
                escaped = re.escape(text)
                flexible = re.sub(r'\\\d+', r'\\d+', escaped)
                flexible = re.sub(r'\\ +', r'\\s+', flexible)
                regex_patterns.append(re.compile(r'^\s*' + flexible + r'\s*$', re.IGNORECASE))

                if p.pattern_type == 'footer' and len(text) > 20:
                    inline_patterns.append(re.compile(
                        r'^([ivxlcdmIVXLCDM]+|\d{1,4})?\s*' + flexible + r'\s*',
                        re.IGNORECASE,
                    ))

        # 独立页码
        page_number_re = re.compile(r'^[ivxlcdmIVXLCDM]+$|^\d{1,4}$')
        has_page_patterns = any(p.pattern_type == 'page_number' for p in patterns)

        for line_num, line in enumerate(lines, 1):
            stripped = line.strip()

            # 保留空行
            if not stripped:
                result.append(line)
                continue

            # 保护代码块栅栏
            if stripped.startswith('```'):
                result.append(line)
                continue

            # --- 1. 精确匹配 ---
            if stripped in exact_matches:
                removed_log.append((line_num, line, "exact_match"))
                continue

            # --- 2. 灵活正则 ---
            should_remove = False
            removal_reason = ""
            for rx in regex_patterns:
                if rx.match(stripped):
                    should_remove = True
                    removal_reason = "regex_pattern"
                    break

            if should_remove:
                removed_log.append((line_num, line, removal_reason))
                continue

            # --- 独立页码 ---
            if has_page_patterns and page_number_re.match(stripped) and len(stripped) <= 5:
                removed_log.append((line_num, line, "page_number"))
                continue

            # --- 3. 内联页脚 ---
            inline_cleaned = None
            for ipat in inline_patterns:
                if ipat.match(stripped):
                    cleaned = ipat.sub('', stripped).strip()
                    if cleaned and len(cleaned) > 10:
                        inline_cleaned = cleaned
                        break
                    elif not cleaned or len(cleaned) <= 10:
                        should_remove = True
                        removal_reason = "inline_footer_removed"
                        break

            if should_remove:
                removed_log.append((line_num, line, removal_reason))
                continue

            if inline_cleaned:
                indent = len(line) - len(line.lstrip())
                result.append(' ' * indent + inline_cleaned)
                removed_log.append((line_num, f"PARTIAL: '{line}' -> '{inline_cleaned}'",
                                    "inline_footer_partial"))
                continue

            # --- 4. 相似度匹配 ---
            if footer_cores and len(stripped) < 200:
                norm = re.sub(r'\d+', '', stripped).lower()
                norm = re.sub(r'\s+', ' ', norm)

                best_sim = 0.0
                for core in footer_cores:
                    sim = SequenceMatcher(None, core, norm).ratio()
                    if sim > best_sim:
                        best_sim = sim

                if best_sim >= 0.80:
                    # 长行尝试内联清理
                    if len(stripped) > 80:
                        for variant in exact_matches:
                            if variant in stripped:
                                cleaned = stripped.replace(variant, '').strip()
                                if cleaned and cleaned != stripped and len(cleaned) > 10:
                                    inline_cleaned = cleaned
                                    break

                    if not inline_cleaned:
                        should_remove = True
                        removal_reason = f"similarity_match({best_sim:.0%})"

                elif best_sim >= 0.50:
                    if self.llm_validator and self.llm_validator.available:
                        llm_result = self.llm_validator.classify_single_line(stripped)
                        if llm_result in ('footer', 'header', 'page_number', 'watermark'):
                            should_remove = True
                            removal_reason = f"similarity_match({best_sim:.0%})+llm_confirmed"

            if should_remove:
                removed_log.append((line_num, line, removal_reason))
                continue

            result.append(line)

        return '\n'.join(result)

    # ------------------------------------------------------------------
    # 交互确认
    # ------------------------------------------------------------------

    @staticmethod
    def _interactive_confirm(patterns: list[DetectedPattern]) -> list[DetectedPattern]:
        """逐个展示检测到的模式，询问用户是否删除。"""
        print("\n" + "=" * 60)
        print("检测到的模式 — 请确认是否移除")
        print("=" * 60)

        confirmed: list[DetectedPattern] = []
        for i, p in enumerate(patterns):
            print(f"\n[{i + 1}] {p}")
            print(f"    类型: {p.pattern_type}  置信度: {p.confidence:.0%}")
            if p.similar_variants:
                print(f"    变体数: {len(p.similar_variants)}")

            resp = input("    移除? [Y/n/q]: ").strip().lower()
            if resp == 'q':
                break
            if resp != 'n':
                confirmed.append(p)

        print(f"\n确认 {len(confirmed)} 个模式将被移除。")
        return confirmed


# =============================================================================
# CLI 入口
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description='通用页眉页脚清理工具 — 适用于 PDF 转 Markdown 文档',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument('input', help='输入 Markdown 文件')
    parser.add_argument('-o', '--output', help='输出文件 (默认: <input>_clean.md)')

    parser.add_argument('--interactive', '-i', action='store_true',
                        help='交互模式: 逐个确认每个模式')
    parser.add_argument('--dry-run', action='store_true',
                        help='仅预览，不写入文件')
    parser.add_argument('--verbose', '-v', action='store_true',
                        help='显示详细检测过程')

    # 注入 LLM 公共参数 (--api, --model, --api-url, --api-key, --no-llm)
    add_llm_args(parser)

    args = parser.parse_args()

    # 读取输入
    input_path = Path(args.input)
    if not input_path.exists():
        print(f"错误: 文件不存在: {input_path}")
        return 1

    content = input_path.read_text(encoding='utf-8')

    # 输出路径
    output_path = Path(args.output) if args.output else input_path.with_stem(input_path.stem + '_clean')

    # 创建 LLM 客户端 (--no-llm 时返回 None)
    llm_client = create_llm_client(args)

    # 横幅
    print_banner("aidoc_strip - 页眉页脚剥离工具")

    print(f"输入: {input_path}")
    if llm_client:
        print(f"LLM:  {llm_client}")
    else:
        print("LLM:  已禁用 (仅规则引擎)")

    # 处理
    cleaner = MarkdownCleaner(
        llm_client=llm_client,
        interactive=args.interactive,
        verbose=args.verbose,
    )

    cleaned, stats = cleaner.process(content)

    # 写入输出
    if not args.dry_run:
        output_path.write_text(cleaned, encoding='utf-8')
        print(f"\n输出: {output_path}")

    # 写入移除日志
    removed_log_path = input_path.with_suffix('.removed.txt')
    if stats.removed_lines:
        with open(removed_log_path, 'w', encoding='utf-8') as f:
            f.write(f"# 被移除的页眉/页脚行: {input_path}\n")
            f.write(f"# 由 aidoc_strip.py 生成\n")
            f.write(f"# 共移除: {len(stats.removed_lines)} 行\n")
            f.write(f"# 注: 本文件仅含页眉页脚移除记录，合并/空行清理见 .cleanup.txt\n")
            f.write("=" * 80 + "\n\n")
            for line_num, line_content, reason in stats.removed_lines:
                f.write(f"[Line {line_num}] ({reason})\n")
                f.write(f"  {line_content}\n\n")
        print(f"移除日志: {removed_log_path}")
    else:
        print("未发现需移除的页眉/页脚。")

    # 写入清理日志
    cleanup_log_path = input_path.with_suffix('.cleanup.txt')
    if stats.cleanup_lines:
        with open(cleanup_log_path, 'w', encoding='utf-8') as f:
            f.write(f"# 清理操作记录: {input_path}\n")
            f.write(f"# 由 aidoc_strip.py 生成\n")
            f.write(f"# 操作总数: {len(stats.cleanup_lines)}\n")
            f.write(f"# 分类:\n")
            f.write(f"#   excess_empty_line       - 多余空行\n")
            f.write(f"#   code_block_merge_fence   - 代码块合并 (``` 栅栏)\n")
            f.write(f"#   code_block_merge_empty   - 代码块合并 (空行)\n")
            f.write(f"#   table_merge_empty         - 表格合并 (空行)\n")
            f.write("=" * 80 + "\n\n")
            for line_num, line_content, reason in stats.cleanup_lines:
                f.write(f"[Line {line_num}] ({reason})\n")
                f.write(f"  {line_content}\n\n")
        print(f"清理日志: {cleanup_log_path}")

    # 统计
    removed_total = stats.original_lines - stats.final_lines
    reduction_pct = removed_total / stats.original_lines * 100 if stats.original_lines else 0

    stat_dict = {
        "原始行数": f"{stats.original_lines:,}",
        "最终行数": f"{stats.final_lines:,}",
        "删除行数": f"{removed_total:,}",
        "检测模式数": stats.patterns_detected,
        "移除模式数": stats.patterns_removed,
    }
    if stats.llm_validations:
        stat_dict["LLM 验证次数"] = stats.llm_validations
    if stats.code_blocks_merged:
        stat_dict["代码块合并"] = stats.code_blocks_merged
    if stats.tables_merged:
        stat_dict["表格合并"] = stats.tables_merged
    stat_dict["缩减比例"] = f"{reduction_pct:.1f}%"

    print_stats(stat_dict, title="处理统计")

    return 0


if __name__ == '__main__':
    exit(main())
