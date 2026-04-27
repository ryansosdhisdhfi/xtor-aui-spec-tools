#!/usr/bin/env python3
"""
aidoc_utils.py - 共享工具函数
==============================

为 aidoc 工具链提供公共的文本处理、Markdown 解析和 CLI 辅助功能。

主要功能：
  - Markdown 代码块边界检测
  - 标题模式匹配
  - 通用文本处理工具
  - CLI 输出格式化
"""

import re
import time
from typing import Optional


# =============================================================================
# Markdown 解析常量
# =============================================================================

# 标题匹配: # ~ ###### 后跟空格和文本
HEADING_PATTERN = re.compile(r'^(#{1,6})\s+(.+)$')

# 代码块栅栏: 0-3 空格 + 至少 3 个 ` 或 ~ + 可选语言标识
CODE_FENCE_PATTERN = re.compile(r'^( {0,3})(`{3,}|~{3,})(\w*)(.*)$')

# 畸形代码块栅栏: 4+ 空格缩进（会被 Markdown 当作代码内容）
INDENTED_FENCE_PATTERN = re.compile(r'^( {4,}|\t+)(`{3,}|~{3,})(.*)$')

# 简单代码块开关检测（不区分语言标识）
SIMPLE_FENCE_PATTERN = re.compile(r'^```')


# =============================================================================
# 代码块检测
# =============================================================================

def find_code_block_lines(lines: list[str]) -> set[int]:
    """
    找出 Markdown 中所有位于代码块内部的行号（0-based）。

    通过追踪 ``` 标记的开关状态，准确识别代码块区域。
    标记行本身也被视为代码块的一部分。

    Args:
        lines: 文件内容按行分割的列表

    Returns:
        代码块内行号的集合（0-based index）
    """
    in_code_block = False
    code_lines = set()

    for i, line in enumerate(lines):
        stripped = line.strip()
        if SIMPLE_FENCE_PATTERN.match(stripped):
            code_lines.add(i)
            in_code_block = not in_code_block
        elif in_code_block:
            code_lines.add(i)

    return code_lines


def extract_headings(lines: list[str], max_level: int = 6) -> list[tuple[int, int, str]]:
    """
    提取 Markdown 标题（自动跳过代码块内的伪标题）。

    Args:
        lines:     文件内容按行分割的列表
        max_level: 最大标题层级（1-6）

    Returns:
        [(行号(1-based), 层级, 标题文本), ...]
    """
    code_block_lines = find_code_block_lines(lines)
    headings = []

    for i, line in enumerate(lines):
        if i in code_block_lines:
            continue
        match = HEADING_PATTERN.match(line.strip())
        if match:
            level = len(match.group(1))
            if level <= max_level:
                headings.append((i + 1, level, match.group(2).strip()))

    return headings


# =============================================================================
# 文本处理工具
# =============================================================================

def normalize_whitespace(text: str, max_empty: int = 2) -> str:
    """
    压缩连续空行，最多保留 max_empty 个。

    Args:
        text:      输入文本
        max_empty: 最大连续空行数

    Returns:
        处理后的文本
    """
    lines = text.split('\n')
    result = []
    empty_count = 0

    for line in lines:
        if line.strip() == '':
            empty_count += 1
            if empty_count <= max_empty:
                result.append(line)
        else:
            empty_count = 0
            result.append(line)

    return '\n'.join(result)


def truncate(text: str, max_len: int = 60, suffix: str = "...") -> str:
    """截断文本到指定长度，超长部分用 suffix 替代"""
    if len(text) <= max_len:
        return text
    return text[:max_len - len(suffix)] + suffix


# =============================================================================
# CLI 输出格式化
# =============================================================================

class ProgressPrinter:
    """
    简易进度打印器，用于长时间批处理任务的进度显示。

    用法：
        progress = ProgressPrinter(total=100, prefix="处理章节")
        for i in range(100):
            progress.update(i + 1, detail="Section Title")
        progress.finish()
    """

    def __init__(self, total: int, prefix: str = "进度"):
        self.total = total
        self.prefix = prefix
        self.start_time = time.time()

    def update(self, current: int, detail: str = ""):
        """打印当前进度"""
        detail_text = f" {truncate(detail, 40)}" if detail else ""
        print(f"  [{current}/{self.total}]{detail_text}", end=" ", flush=True)

    def item_done(self, success: bool = True):
        """标记当前项完成"""
        print("✓" if success else "(跳过)")

    def finish(self):
        """打印完成摘要"""
        elapsed = time.time() - self.start_time
        print(f"\n{self.prefix}完成，耗时 {elapsed:.1f}s")


def print_banner(title: str, width: int = 60):
    """打印带分隔线的标题横幅"""
    print("=" * width)
    print(title)
    print("=" * width)


def print_stats(stats: dict, title: str = "处理统计"):
    """
    打印统计信息表格。

    Args:
        stats: {标签: 值} 字典
        title: 表格标题
    """
    print(f"\n{'=' * 40}")
    print(title)
    print(f"{'=' * 40}")
    # 对齐标签
    max_label_len = max(len(str(k)) for k in stats.keys()) if stats else 0
    for label, value in stats.items():
        print(f"  {str(label):<{max_label_len + 2}}{value}")
