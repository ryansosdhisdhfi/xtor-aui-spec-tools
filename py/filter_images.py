#!/usr/bin/env python3
"""
本地规则筛图（不调 VLM）：读 *.images.json + 与 clean.md 中图链对应的 PNG，
按文件大小 / 可选 bbox 面积 / 可选像素面积 过滤，输出 *.images.filtered.json。

典型：小图标、角标占满「图位」但文件极小，可 drop 以省 describe 费用。

用法:
  python filter_images.py \\
    --md user-run/e2e_10p_20260422/e2e_10p_clean.md \\
    --images-json user-run/e2e_10p_20260422/e2e_10p.images.json \\
    -o user-run/e2e_10p_20260422/e2e_10p.images.filtered.json
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path
from typing import Any

MD_IMG = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")


def parse_md_image_paths(md_text: str, md_path: Path) -> list[tuple[str, Path]]:
    """返回 (href, 解析后的绝对 Path)；只保留存在的 .png/.jpg 等。"""
    base = md_path.parent
    out: list[tuple[str, Path]] = []
    for m in MD_IMG.finditer(md_text):
        href = m.group(1).strip()
        if href.startswith(("http://", "https://", "data:")):
            continue
        p = Path(href)
        if not p.is_absolute():
            p = (base / p).resolve()
        else:
            p = p.resolve()
        if p.is_file() and p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
            out.append((href, p))
    return out


def bbox_area(bbox: dict[str, Any] | None) -> float | None:
    if not bbox:
        return None
    try:
        l, t, r, b = float(bbox["l"]), float(bbox["t"]), float(bbox["r"]), float(bbox["b"])
    except (KeyError, TypeError, ValueError):
        return None
    return abs(r - l) * abs(t - b)


def try_image_pixels(path: Path) -> tuple[int, int] | None:
    try:
        from PIL import Image  # type: ignore
    except ImportError:
        return None
    try:
        with Image.open(path) as im:
            w, h = im.size
            return (int(w), int(h))
    except OSError:
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description="本地规则过滤低信息图片，输出 filtered JSON")
    ap.add_argument("--md", required=True, help="含 ![...](...) 图链的 Markdown（一般为 *_clean.md）")
    ap.add_argument("--images-json", required=True, help="aidoc 导出的 *.images.json")
    ap.add_argument("-o", "--output", required=True, help="输出路径，如 *.images.filtered.json")
    ap.add_argument(
        "--min-bytes",
        type=int,
        default=3072,
        help="文件小于此字节数则丢弃（默认 3072=3KB，可压低以保留小图）",
    )
    ap.add_argument(
        "--min-bbox-area",
        type=float,
        default=0.0,
        help="Docling bbox 面积小于此则丢弃，0=不启用",
    )
    ap.add_argument(
        "--min-pixels",
        type=int,
        default=0,
        help="需安装 Pillow；宽*高小于此则丢弃，0=不启用",
    )
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    md_path = Path(args.md).resolve()
    j_path = Path(args.images_json).resolve()
    if not md_path.is_file():
        print(f"错误: 无 MD 文件 {md_path}", file=sys.stderr)
        return 1
    if not j_path.is_file():
        print(f"错误: 无 JSON {j_path}", file=sys.stderr)
        return 1

    md_text = md_path.read_text(encoding="utf-8")
    links = parse_md_image_paths(md_text, md_path)
    items: list[dict[str, Any]] = json.loads(j_path.read_text(encoding="utf-8"))
    if not isinstance(items, list):
        print("错误: images.json 应为数组", file=sys.stderr)
        return 1

    if len(links) != len(items):
        print(
            f"警告: MD 中图数 ({len(links)}) 与 images.json 项数 ({len(items)}) 不一致，将按**较短长度**对位 zip；请检查导出",
            file=sys.stderr,
        )
    n = min(len(links), len(items))
    if n == 0:
        print("错误: 无图可对位", file=sys.stderr)
        return 1

    kept: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    for i in range(n):
        href, fpath = links[i]
        rec = dict(items[i])
        rec["_artifact_href"] = href
        rec["_artifact_path"] = str(fpath)
        st = fpath.stat()
        size = st.st_size
        reasons: list[str] = []

        if size < args.min_bytes:
            reasons.append(f"file_too_small({size}B<{args.min_bytes}B)")

        ba = bbox_area(rec.get("bbox"))
        if args.min_bbox_area > 0 and ba is not None and ba < args.min_bbox_area:
            reasons.append(f"bbox_area_too_small({ba:.1f}<{args.min_bbox_area})")

        px = try_image_pixels(fpath)
        if args.min_pixels > 0 and px is not None:
            w, h = px
            if w * h < args.min_pixels:
                reasons.append(f"pixels_too_small({w}x{h}={w*h}<{args.min_pixels})")
        elif args.min_pixels > 0 and px is None:
            if args.verbose:
                print("提示: 未安装 Pillow 或读图失败，跳过 min-pixels 检查", file=sys.stderr)

        if reasons:
            dropped.append(
                {
                    "image_id": rec.get("image_id"),
                    "path": str(fpath),
                    "drop_reason": "; ".join(reasons),
                }
            )
        else:
            rec["file_size_bytes"] = size
            if px:
                rec["image_width"] = px[0]
                rec["image_height"] = px[1]
            kept.append(rec)

    out: dict[str, Any] = {
        "source_md": str(md_path),
        "source_images_json": str(j_path),
        "rules": {
            "min_bytes": args.min_bytes,
            "min_bbox_area": args.min_bbox_area,
            "min_pixels": args.min_pixels,
        },
        "kept": kept,
        "dropped": dropped,
        "summary": {
            "total_in_json": len(items),
            "matched_pairs": n,
            "kept_count": len(kept),
            "dropped_count": len(dropped),
        },
    }

    out_path = Path(args.output).resolve()
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"已写入 {out_path}  kept={len(kept)}  dropped={len(dropped)}  (对位 n={n})"
    )
    if args.verbose and dropped:
        for d in dropped:
            print("  drop:", d)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
