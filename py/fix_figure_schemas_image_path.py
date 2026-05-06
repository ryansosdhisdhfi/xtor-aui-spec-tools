#!/usr/bin/env python3
"""
将 figure_schemas 下各 JSON 里的 image_path 从本机绝对路径改为相对 output/ 的 POSIX 路径
（与 merge 后 *_merged_images/ 布局一致）。

例（仅按路径中 /output/ 截断，不必传 output-dir）:
  python3 py/fix_figure_schemas_image_path.py --schemas-dir archive/.../figure_schemas --dry-run

传 --output-dir 时优先用相对该目录的 pathlib 结果（与当时跑 B 一致时最准）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _rel_after_output_marker(abs_s: str) -> str | None:
    """从绝对路径中截取 output/ 之后部分（换机、只拷 archive 时也稳定）。"""
    s = abs_s.replace("\\", "/")
    needle = "/output/"
    i = s.find(needle)
    if i >= 0:
        return s[i + len(needle) :].lstrip("/")
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description="Normalize figure_schemas *.json image_path to relative output/")
    ap.add_argument("--schemas-dir", required=True, type=Path, help="figure_schemas 目录")
    ap.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="可选：当时 output 根目录；若缺省则仅用路径里 /output/ 之后一段",
    )
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    sd = args.schemas_dir.resolve()
    anchor = args.output_dir.resolve() if args.output_dir else None
    if not sd.is_dir():
        print(f"错误: 不是目录 {sd}", file=sys.stderr)
        return 1

    n_fix = 0
    for jp in sorted(sd.glob("*.json")):
        if jp.name == "batch_report.json":
            continue
        try:
            data = json.loads(jp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            print(f"跳过 {jp.name}: {e}", file=sys.stderr)
            continue
        if not isinstance(data, dict):
            continue
        raw = data.get("image_path")
        if not isinstance(raw, str) or not raw.strip():
            continue
        rel: str | None = None
        if anchor is not None:
            try:
                rel = Path(raw).resolve().relative_to(anchor).as_posix()
            except (ValueError, OSError):
                rel = None
        if rel is None:
            rel = _rel_after_output_marker(raw)
        if rel is None or rel == raw.strip():
            continue
        if rel == raw:
            continue
        data["image_path"] = rel
        n_fix += 1
        if args.dry_run:
            print(f"{jp.name}: {raw[:72]}… -> {rel}")
        else:
            jp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(f"更新 {jp.name}")

    print(f"完成: 改写 {n_fix} 个文件" + (" (dry-run)" if args.dry_run else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
