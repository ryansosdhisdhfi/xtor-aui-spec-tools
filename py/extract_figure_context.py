#!/usr/bin/env python3
"""
从带图链的 Markdown 中，为每张图裁剪「标题链 + 图前/图后正文」，供 VLM describe 的 prompt 使用。

规则（可参数化）全是**本地、确定性**的：按行扫 MD，在每张 ![](...) 出现的位置向前累计标题
（# 层级栈），再按字符数从图行前后截取正文（不包扩图行本身、不含其它图行，避免串图）。

不联网；输入就是与筛图/convert 同一份 clean.md。

典型用法:
  python extract_figure_context.py \\
    --md user-run/e2e_10p_20260422/e2e_10p_clean.md \\
    -o user-run/e2e_10p_20260422/e2e_10p.figure_context.json

与 batch_describe 配合: 先跑本脚本出 JSON，describe 时把同一 basename 的 context 段拼进
「Context from document」即可。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

# ![alt](path) 或 ![alt](path "title")
MD_IMG = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
HEADING = re.compile(r"^(#{1,6})\s+(.+)$")


def _strip_md_link_punct(s: str) -> str:
    s = s.strip()
    if s and s[-1] in "#§":
        s = s[:-1].rstrip()
    return s


def heading_stack_at_line(lines: list[str], end_exclusive: int) -> list[str]:
    """扫描 lines[0:end_exclusive)，返回当前小节的标题链（从大到小，面包屑）。"""
    stack: list[tuple[int, str]] = []
    for i in range(end_exclusive):
        m = HEADING.match(lines[i].rstrip())
        if not m:
            continue
        level = len(m.group(1))
        title = _strip_md_link_punct(m.group(2))
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, title))
    return [t for _, t in stack]


def is_image_line(line: str) -> bool:
    return bool(MD_IMG.search(line))


def text_before(
    lines: list[str],
    image_line_index: int,
    max_chars: int,
) -> str:
    """从 image 行**上方**非图行起整段向上收齐，再只保留**末尾** max_chars 字（紧贴图的一截）。"""
    acc_lines: list[str] = []
    for j in range(image_line_index - 1, -1, -1):
        line = lines[j].rstrip()
        if is_image_line(line):
            continue
        acc_lines.append(line)
    # 自顶向下读顺序
    text = "\n".join(reversed(acc_lines))
    if len(text) <= max_chars:
        return text.strip()
    return text[-max_chars:].strip()


def text_after(
    lines: list[str],
    image_line_index: int,
    max_chars: int,
) -> str:
    """从 image 行**下方**到「下一张图」或文末，只保留**开头** max_chars 字（紧贴图的一截）。"""
    parts: list[str] = []
    n = len(lines)
    for j in range(image_line_index + 1, n):
        line = lines[j].rstrip()
        if is_image_line(line):
            break
        parts.append(line)
    text = "\n".join(parts)
    if len(text) <= max_chars:
        return text.strip()
    return text[:max_chars].strip()


def parse_figures(md_text: str, md_path: Path) -> list[dict[str, Any]]:
    lines = md_text.splitlines()
    base = md_path.parent
    figures: list[dict[str, Any]] = []
    for i, line in enumerate(lines):
        m = MD_IMG.search(line)
        if not m:
            continue
        href = m.group(2).strip().split()[0]  # 去 " title"
        if href.startswith(("http://", "https://", "data:")):
            continue
        p = Path(href)
        if not p.is_absolute():
            p = (base / p).resolve()
        else:
            p = p.resolve()
        basename = p.name
        chain = heading_stack_at_line(lines, i)
        figures.append(
            {
                "line_1based": i + 1,
                "image_href": href,
                "image_basename": basename,
                "heading_chain": chain,
            }
        )
    return figures


def main() -> int:
    ap = argparse.ArgumentParser(
        description="从 MD 中按图位裁剪标题链与前后正文，供 VLM context"
    )
    ap.add_argument("--md", required=True, help="clean markdown 路径")
    ap.add_argument("-o", "--output", required=True, help="输出 JSON 路径")
    ap.add_argument(
        "--before-chars",
        type=int,
        default=2000,
        help="图前正文最大字符数（默认 2000）",
    )
    ap.add_argument(
        "--after-chars",
        type=int,
        default=1500,
        help="图后正文最大字符数（默认 1500；遇下一张图会提前停）",
    )
    ap.add_argument(
        "--by-basename",
        action="store_true",
        help="额外输出 by_basename 索引，便于用文件名对齐",
    )
    args = ap.parse_args()

    md_path = Path(args.md).resolve()
    if not md_path.is_file():
        print(f"错误: 无文件 {md_path}", file=sys.stderr)
        return 1
    text = md_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    base_figs = parse_figures(text, md_path)

    figures: list[dict[str, Any]] = []
    for rec in base_figs:
        i = rec["line_1based"] - 1
        before = text_before(lines, i, args.before_chars)
        after = text_after(lines, i, args.after_chars)
        figures.append(
            {
                **rec,
                "context_before": before,
                "context_after": after,
            }
        )

    out: dict[str, Any] = {
        "source_md": str(md_path),
        "options": {
            "before_chars": args.before_chars,
            "after_chars": args.after_chars,
        },
        "figures": figures,
    }
    if args.by_basename:
        out["by_basename"] = {f["image_basename"]: f for f in figures}

    out_path = Path(args.output).resolve()
    out_path.write_text(
        json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"已写入 {out_path}  共 {len(figures)} 张图")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
