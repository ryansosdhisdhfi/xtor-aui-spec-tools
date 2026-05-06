#!/usr/bin/env python3
"""
将分段 a1 的图片目录与 *.images.json 合并到统一目录与单文件，
并重写 output/<base>_merged.md 中的图片路径（与 merge_split_md.py 配套）。

支持 Docling 两种落盘：`<stem>_images/`（fig_*.png）与 `<stem>_artifacts/`（image_*_*.png 等）；
每段自动选用其中**图片文件更多**的一侧。`*_artifacts` 内同一序号常有多份哈希副本，**仅拷贝各段
`<stem>.md` 里实际出现的文件名**，与 `*.images.json` 条数对齐；无 MD 引用时按 `image_NNNNNN`
序号去重，每序号保留一份。

命名：output/<base>_merged_images/<part_stem>_<原文件名>

用法:
  python3 py/merge_split_assets.py input/<base>_parts_manifest.json --output-dir output --clean
须已存在 output/<base>_merged.md（先 make merge-parts）。
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path


def _count_image_files(d: Path, img_ext: set[str]) -> int:
    if not d.is_dir():
        return 0
    return sum(1 for f in d.iterdir() if f.is_file() and f.suffix.lower() in img_ext)


def _pick_image_source(
    out_dir: Path, stem: str, img_ext: set[str]
) -> tuple[Path, str] | None:
    """
    返回 (源目录, 相对 out_dir 的路径前缀，如 stem_artifacts)。
    优先使用含更多图片文件的目录（常见：仅 _artifacts 有图、_images 为空）。
    """
    art = out_dir / f"{stem}_artifacts"
    img = out_dir / f"{stem}_images"
    na, ni = _count_image_files(art, img_ext), _count_image_files(img, img_ext)
    if na > 0 and na >= ni:
        return art, f"{stem}_artifacts"
    if ni > 0:
        return img, f"{stem}_images"
    if art.is_dir():
        return art, f"{stem}_artifacts"
    if img.is_dir():
        return img, f"{stem}_images"
    return None


def _norm_abs(p: Path) -> str:
    return str(p.resolve()).replace("\\", "/")


def _artifact_basenames_from_md_ordered(md_text: str, stem: str) -> list[str]:
    """从分段 MD 中按出现顺序提取 stem_artifacts/ 下的图片文件名（去重）。"""
    rel = f"{stem}_artifacts/"
    out: list[str] = []
    seen: set[str] = set()
    pat = re.compile(
        re.escape(rel) + r"([^)\s\]\"']+\.(?:png|jpg|jpeg|gif|webp|svg))",
        re.I,
    )
    for m in pat.finditer(md_text):
        b = m.group(1)
        if b not in seen:
            seen.add(b)
            out.append(b)
    return out


def _dedupe_artifact_indices(files: list[Path]) -> list[Path]:
    """同一 image_NNNNNN_* 只保留字典序第一份；其余无序号规则的单独排在后。"""
    by_idx: dict[int, Path] = {}
    orphans: list[Path] = []
    for f in sorted(files, key=lambda x: x.name):
        m = re.match(r"(?i)image_(\d+)_", f.name)
        if m:
            idx = int(m.group(1))
            if idx not in by_idx:
                by_idx[idx] = f
        else:
            orphans.append(f)
    ordered = [by_idx[k] for k in sorted(by_idx)]
    ordered.extend(sorted(orphans, key=lambda x: x.name))
    return ordered


def _list_files_for_merge(
    src_dir: Path,
    rel_prefix: str,
    stem: str,
    out_dir: Path,
    img_ext: set[str],
) -> list[Path]:
    all_f = [f for f in src_dir.iterdir() if f.is_file() and f.suffix.lower() in img_ext]
    if not all_f:
        return []
    if rel_prefix.endswith("_artifacts"):
        md_path = out_dir / f"{stem}.md"
        if md_path.is_file():
            names = _artifact_basenames_from_md_ordered(
                md_path.read_text(encoding="utf-8"), stem
            )
            picked = [src_dir / n for n in names if (src_dir / n).is_file()]
            if picked:
                return picked
        return _dedupe_artifact_indices(all_f)
    return sorted(all_f, key=lambda x: x.name)


def _load_manifest(man_path: Path) -> tuple[str, list[dict]]:
    data = json.loads(man_path.read_text(encoding="utf-8"))
    base = (data.get("base_stem") or "").strip()
    parts = data.get("parts") or []
    if not base or not parts:
        print("manifest 缺少 base_stem 或 parts", file=sys.stderr)
        sys.exit(1)
    return base, parts


def _path_variants(rel: str) -> list[str]:
    """合并 MD 中可能出现的同一路径的几种写法。"""
    rel = rel.replace("\\", "/")
    v = {rel, f"./{rel}"}
    if rel.startswith("./"):
        v.add(rel[2:])
    return sorted(v, key=len, reverse=True)


def _replace_paths(text: str, replacements: list[tuple[str, str]]) -> str:
    """按旧串长度降序替换，减少子串误伤。"""
    for old, new in replacements:
        if old and old in text:
            text = text.replace(old, new)
    return text


def _rewrite_md_images(text: str, path_map: dict[str, str]) -> str:
    """path_map: 规范化旧路径（无 ./、正斜杠） -> 新路径（同上，写入 MD 时用此统一形式）。"""
    pairs: list[tuple[str, str]] = []
    for old_norm, new_norm in path_map.items():
        for o in _path_variants(old_norm):
            pairs.append((o, new_norm))
    pairs.sort(key=lambda x: len(x[0]), reverse=True)
    return _replace_paths(text, pairs)


def _simple_img_tag_replace(text: str, path_map: dict[str, str]) -> str:
    out = text
    for m in re.finditer(r'(<img[^>]+src=)(["\'])([^"\']+)(\2)', text, flags=re.IGNORECASE):
        src = m.group(3)
        norm = src.replace("\\", "/").lstrip("./")
        if norm in path_map:
            new_src = path_map[norm]
            out = out.replace(m.group(0), f"{m.group(1)}{m.group(2)}{new_src}{m.group(4)}", 1)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="合并分段图片目录与 images.json，并重写 _merged.md 内图片路径")
    ap.add_argument("manifest", type=Path, help="input/<base>_parts_manifest.json")
    ap.add_argument("--output-dir", type=Path, default=None, help="默认 manifest 的兄弟目录 output/")
    ap.add_argument(
        "--merged-md",
        type=Path,
        default=None,
        help="已合并的 Markdown（默认 output/<base>_merged.md）",
    )
    ap.add_argument(
        "--clean",
        action="store_true",
        help="开始前删除已有的 <base>_merged_images/ 与 <base>_merged.images.json",
    )
    args = ap.parse_args()

    man_path = args.manifest.resolve()
    if not man_path.is_file():
        print(f"找不到 manifest: {man_path}", file=sys.stderr)
        sys.exit(1)

    base, parts = _load_manifest(man_path)
    if args.output_dir:
        out_dir = args.output_dir.resolve()
    else:
        out_dir = (man_path.parent.parent / "output").resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    merged_md = args.merged_md or (out_dir / f"{base}_merged.md")
    merged_md = merged_md.resolve()
    if not merged_md.is_file():
        print(f"缺少合并稿，请先 merge-parts: {merged_md}", file=sys.stderr)
        sys.exit(2)

    merged_img_dir = out_dir / f"{base}_merged_images"
    merged_json_path = out_dir / f"{base}_merged.images.json"

    if args.clean:
        if merged_img_dir.is_dir():
            shutil.rmtree(merged_img_dir)
        merged_json_path.unlink(missing_ok=True)

    merged_img_dir.mkdir(parents=True, exist_ok=True)

    # 旧路径(相对 out_dir，正斜杠) 与绝对路径 -> 新相对路径；每文件常写 2 个键
    path_map: dict[str, str] = {}
    merged_items: list[dict] = []
    img_ext = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}
    n_files_copied = 0

    for p in parts:
        stem = (p.get("stem") or "").strip()
        if not stem:
            continue
        picked = _pick_image_source(out_dir, stem, img_ext)
        if picked is None:
            print(f"警告: 无 {stem}_images / {stem}_artifacts 图片目录，跳过", file=sys.stderr)
            continue
        src_dir, rel_prefix = picked
        sorted_files = _list_files_for_merge(src_dir, rel_prefix, stem, out_dir, img_ext)
        for f in sorted_files:
            old_rel = f"{rel_prefix}/{f.name}"
            dest_name = f"{stem}_{f.name}"
            dest = merged_img_dir / dest_name
            shutil.copy2(f, dest)
            n_files_copied += 1
            new_rel = f"{base}_merged_images/{dest_name}"
            path_map[old_rel] = new_rel
            path_map[_norm_abs(src_dir / f.name)] = new_rel

        jpath = out_dir / f"{stem}.images.json"
        if not jpath.is_file():
            print(f"警告: 无 {jpath.name}，跳过 JSON 合并", file=sys.stderr)
            continue
        try:
            arr = json.loads(jpath.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            print(f"警告: 无法解析 {jpath}: {e}", file=sys.stderr)
            continue
        if not isinstance(arr, list):
            continue
        for idx, item in enumerate(arr):
            if not isinstance(item, dict):
                continue
            cp = dict(item)
            oid = str(cp.get("image_id", "") or "")
            if oid:
                cp["image_id"] = f"{stem}__{oid}"
            old_ip = cp.get("image_path")
            if isinstance(old_ip, str) and old_ip.strip():
                key = old_ip.replace("\\", "/").lstrip("./")
                candidates = [key, _norm_abs(Path(old_ip))]
                if "/" not in key:
                    candidates.extend([f"{stem}_images/{key}", f"{stem}_artifacts/{key}"])
                for c in candidates:
                    if c and c in path_map:
                        cp["image_path"] = path_map[c]
                        break
                else:
                    for o, n in path_map.items():
                        if o.endswith("/" + Path(old_ip).name):
                            cp["image_path"] = n
                            break
            elif idx < len(sorted_files):
                syn = f"{rel_prefix}/{sorted_files[idx].name}"
                if syn in path_map:
                    cp["image_path"] = path_map[syn]
            cp["source_part_stem"] = stem
            merged_items.append(cp)

    md_text = merged_md.read_text(encoding="utf-8")
    md_new = _rewrite_md_images(md_text, path_map)
    md_new = _simple_img_tag_replace(md_new, path_map)
    merged_md.write_text(md_new, encoding="utf-8")

    merged_json_path.write_text(
        json.dumps(merged_items, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"图片目录: {merged_img_dir}（已拷贝 {n_files_copied} 个文件；path 映射键 {len(path_map)} 个）")
    print(f"已更新: {merged_md}")
    print(f"已写入: {merged_json_path} ({len(merged_items)} 条)")


if __name__ == "__main__":
    main()
