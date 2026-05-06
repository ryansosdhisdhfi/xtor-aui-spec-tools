#!/usr/bin/env python3
"""按 split_pdf_for_a1.py 生成的 manifest 顺序拼接 output/<stem>.md -> <base>_merged.md"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser(description="合并分段 a1 产生的 Markdown")
    ap.add_argument("manifest", type=Path, help="input/<base>_parts_manifest.json")
    ap.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Markdown 所在目录（默认与 manifest 父目录的兄弟 output/）",
    )
    ap.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="合并输出路径（默认 output/<base_stem>_merged.md）",
    )
    args = ap.parse_args()

    man_path = args.manifest.resolve()
    if not man_path.is_file():
        print(f"找不到 manifest: {man_path}", file=sys.stderr)
        sys.exit(1)

    data = json.loads(man_path.read_text(encoding="utf-8"))
    base = data.get("base_stem") or ""
    parts = data.get("parts") or []
    if not base or not parts:
        print("manifest 缺少 base_stem 或 parts", file=sys.stderr)
        sys.exit(1)

    # 默认: .../input/foo_parts_manifest.json -> .../output
    if args.output_dir:
        out_dir = args.output_dir.resolve()
    else:
        out_dir = man_path.parent.parent / "output"
    out_dir.mkdir(parents=True, exist_ok=True)

    out_path = args.output or (out_dir / f"{base}_merged.md")
    out_path = out_path.resolve()

    chunks: list[str] = []
    for i, p in enumerate(parts):
        stem = p.get("stem")
        if not stem:
            continue
        md = out_dir / f"{stem}.md"
        if not md.is_file():
            print(f"缺少分段 MD（请先 a1）: {md}", file=sys.stderr)
            sys.exit(2)
        body = md.read_text(encoding="utf-8")
        title = p.get("title") or stem
        sep = f"\n\n<!-- split part {i + 1}/{len(parts)}: {title} -->\n\n"
        if i > 0:
            chunks.append(sep)
        else:
            chunks.append(f"<!-- merged from {len(parts)} parts; base={base} -->\n\n")
        chunks.append(body.rstrip())
        chunks.append("\n")

    out_path.write_text("".join(chunks).rstrip() + "\n", encoding="utf-8")
    print(f"已写入: {out_path} ({out_path.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
