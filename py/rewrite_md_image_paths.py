#!/usr/bin/env python3
"""
将 Markdown 中 ![...](url) 的**本地**图片链接从绝对路径改为相对「该 .md 所在目录」。
跳过 http(s)://、data: 等。可选同步改写 *.images.json 中 image_path / artifact_path 等字段（与 MD 同一锚点）。

用法:
  python rewrite_md_image_paths.py -i e2e_10p.md --dry-run
  python rewrite_md_image_paths.py -i e2e_10p.md --in-place
  python rewrite_md_image_paths.py -i e2e_10p.md -o e2e_10p_rel.md
  python rewrite_md_image_paths.py -i e2e_10p.md --images-json e2e_10p.images.json --in-place
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

MD_IMAGE_LINK = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")

JSON_PATH_KEYS = frozenset(
    {
        "image_path",
        "artifact_path",
        "hires_image_path",
        "export_path",
    }
)


def _strip_wrappers(href: str) -> str:
    h = href.strip()
    if len(h) >= 2 and h[0] == "<" and h[-1] == ">":
        h = h[1:-1].strip()
    return h


def to_relative_path(href: str, md_dir: Path) -> str | None:
    """返回相对 md_dir 的 POSIX 路径；无需改则 None。"""
    h = _strip_wrappers(href)
    if not h or h.startswith(("#", "http://", "https://", "data:", "mailto:")):
        return None
    if h.startswith("javascript:"):
        return None

    p = Path(h)
    if not p.is_absolute():
        try:
            resolved = (md_dir / p).resolve()
        except (OSError, ValueError):
            return None
    else:
        try:
            resolved = p.resolve()
        except (OSError, ValueError):
            return None

    try:
        rel = os.path.relpath(resolved, md_dir)
    except ValueError:
        return None

    out = rel.replace("\\", "/")
    old_norm = href.strip()
    if out == _strip_wrappers(old_norm):
        return None
    return out


def rewrite_markdown_text(text: str, md_path: Path) -> tuple[str, int]:
    md_dir = md_path.parent

    def repl(m: re.Match[str]) -> str:
        alt, url = m.group(1), m.group(2)
        new_url = to_relative_path(url, md_dir)
        if new_url is None:
            return m.group(0)
        return f"![{alt}]({new_url})"

    new_text, n = MD_IMAGE_LINK.subn(repl, text)
    return new_text, n


def rewrite_json_obj(data: Any, md_dir: Path) -> tuple[Any, int]:
    changes = 0

    def walk(node: Any) -> Any:
        nonlocal changes
        if isinstance(node, dict):
            out: dict[str, Any] = {}
            for k, v in node.items():
                if k in JSON_PATH_KEYS and isinstance(v, str):
                    newv = to_relative_path(v, md_dir)
                    if newv is not None and newv != v:
                        out[k] = newv
                        changes += 1
                    else:
                        out[k] = v
                else:
                    out[k] = walk(v)
            return out
        if isinstance(node, list):
            return [walk(x) for x in node]
        return node

    return walk(data), changes


def preview_diff(old: str, new: str, limit: int = 24) -> None:
    o_lines = old.splitlines()
    n_lines = new.splitlines()
    shown = 0
    for i, (a, b) in enumerate(zip(o_lines, n_lines), 1):
        if a != b:
            print(f"  L{i} - {a}")
            print(f"  L{i} + {b}")
            shown += 1
            if shown >= limit:
                print(f"  ... 仅展示前 {limit} 处差异")
                return


def main() -> int:
    ap = argparse.ArgumentParser(description="MD/JSON 本地图片路径改为相对 .md 同目录")
    ap.add_argument("-i", "--input", required=True, help="输入 .md 路径（锚点为其父目录）")
    ap.add_argument("-o", "--output", help="输出 .md；未指定且非 --in-place 时须配合 --dry-run")
    ap.add_argument("--in-place", action="store_true", help="覆盖输入 .md")
    ap.add_argument(
        "--images-json",
        action="append",
        default=[],
        metavar="FILE",
        help="可多次；与 -i 同目录或绝对路径；仅改列出的路径键。",
    )
    ap.add_argument("--dry-run", action="store_true", help="不写文件，打印统计与部分 diff")
    args = ap.parse_args()

    md_in = Path(args.input).resolve()
    if not md_in.is_file():
        print(f"错误: 找不到 MD: {md_in}", file=sys.stderr)
        return 1
    if args.in_place and args.output:
        print("错误: 不能同时 --in-place 与 -o", file=sys.stderr)
        return 1
    if not args.dry_run and not args.in_place and not args.output:
        print("错误: 请 --in-place 或 -o，或仅 --dry-run", file=sys.stderr)
        return 1

    md_dir = md_in.parent
    text = md_in.read_text(encoding="utf-8")
    new_text, n_md = rewrite_markdown_text(text, md_in)

    json_jobs: list[tuple[Path, Any, int]] = []
    for jspec in args.images_json:
        jp = Path(jspec)
        if not jp.is_absolute():
            jp = (Path.cwd() / jp).resolve()
        else:
            jp = jp.resolve()
        if not jp.is_file():
            print(f"警告: 找不到 JSON，跳过: {jp}", file=sys.stderr)
            continue
        try:
            data = json.loads(jp.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            print(f"错误: JSON 解析失败 {jp}: {e}", file=sys.stderr)
            return 1
        new_data, nch = rewrite_json_obj(data, md_dir)
        json_jobs.append((jp, new_data, nch))

    if args.dry_run:
        print(f"[dry-run] 将替换 Markdown 中本地图链: {n_md} 处")
        if n_md:
            preview_diff(text, new_text)
        for jp, _nd, nch in json_jobs:
            print(f"[dry-run] {jp.name}: 路径字段将改写 {nch} 处（当前 image_path 多为 null 则可能为 0）")
        return 0

    if args.in_place:
        if n_md:
            md_in.write_text(new_text, encoding="utf-8")
        print(f"已写入 MD: {md_in}（{n_md} 处）")
    elif args.output:
        Path(args.output).resolve().write_text(new_text, encoding="utf-8")
        print(f"已写入 MD: {args.output}（{n_md} 处）")

    for jp, new_data, nch in json_jobs:
        jp.write_text(json.dumps(new_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"已写入 JSON: {jp}（路径字段 {nch} 处）")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
