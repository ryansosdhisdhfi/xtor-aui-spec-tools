#!/usr/bin/env python3
"""
按书签（或固定页块）拆分 PDF，生成 input/<base>_ptNNN.pdf + manifest。

默认策略（有书签时）：先按「章界」（顶层书签，过少时启发式改用一层子级）切段；
整章 ≤ max-pages 则整段；超长章仅在章内沿子书签原子区间打包，每段 ≤ max-pages（先最少段数，再尽量抬高最短段页数以减轻尾段过短）；
无章内书签可分时仍按页硬切。

依赖: pypdf、系统 qpdf（WSL: sudo apt-get install -y qpdf）。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from pypdf import PdfReader


def _reader_outline_pages(reader: PdfReader) -> tuple[list[tuple[str, int]], int]:
    """返回 (书签列表, 总页数)。书签为 DFS 顺序，含任意层级。"""
    total_pages = len(reader.pages)

    def walk(node) -> None:
        if isinstance(node, list):
            for x in node:
                walk(x)
            return
        try:
            page = reader.get_destination_page_number(node)
            title = getattr(node, "title", None)
            if title is None:
                title = str(node)
            out.append((str(title).strip() or "?", page))
        except Exception:
            return

    out: list[tuple[str, int]] = []
    if reader.outline:
        walk(reader.outline)
    return out, total_pages


def get_top_level_chapters(reader: PdfReader) -> list[tuple[str, int]]:
    """章界：顶层书签；若顶层过少则改用一层子级（与旧逻辑一致）。"""
    top_chapters: list[tuple[str, int]] = []
    child_chapters: list[tuple[str, int]] = []

    if reader.outline:
        for item in reader.outline:
            if isinstance(item, list):
                for sub_item in item:
                    if not isinstance(sub_item, list):
                        try:
                            page = reader.get_destination_page_number(sub_item)
                            child_chapters.append((sub_item.title, page))
                        except Exception:
                            pass
            else:
                try:
                    page = reader.get_destination_page_number(item)
                    top_chapters.append((item.title, page))
                except Exception:
                    pass

    if len(top_chapters) <= 3 and len(child_chapters) > 3:
        print(f"  顶级书签仅 {len(top_chapters)} 个，改用子级书签（{len(child_chapters)} 个）", file=sys.stderr)
        return child_chapters
    return top_chapters


def create_chapter_ranges(
    chapters: list[tuple[str, int]],
    total_pages: int,
    max_pages: int,
) -> list[tuple[str, int, int]]:
    """返回 (标题, start_page, end_page)；end 为半开 [start, end)，0-based 页索引。"""
    ranges: list[tuple[str, int, int]] = []
    for i, (title, start_page) in enumerate(chapters):
        if i + 1 < len(chapters):
            end_page = chapters[i + 1][1]
        else:
            end_page = total_pages
        page_count = end_page - start_page
        if page_count <= max_pages:
            ranges.append((title, start_page, end_page))
        else:
            chunk_start = start_page
            part = 1
            while chunk_start < end_page:
                chunk_end = min(chunk_start + max_pages, end_page)
                ranges.append((f"{title} (part {part})", chunk_start, chunk_end))
                chunk_start = chunk_end
                part += 1
    return ranges


def _subdivide_oversized(start: int, end: int, max_pages: int, base_title: str) -> list[tuple[str, int, int]]:
    """书签之间仍超过 max_pages 时，按固定页宽切开（尽量避免，但无法更细）。"""
    out: list[tuple[str, int, int]] = []
    part = 1
    chunk_start = start
    while chunk_start < end:
        chunk_end = min(chunk_start + max_pages, end)
        label = f"{base_title}（段 {part}）" if end - start > max_pages else base_title
        out.append((label, chunk_start, chunk_end))
        part += 1
        chunk_start = chunk_end
    return out


def _expand_atomics_to_max(
    atomics: list[tuple[int, int, str]],
    max_pages: int,
) -> list[tuple[int, int, str]]:
    """单块仍超过 max_pages 时先按页硬切成多条原子（无法再细的书签区间）。"""
    expanded: list[tuple[int, int, str]] = []
    for s, e, lab in atomics:
        if e - s > max_pages:
            for lab2, ss, ee in _subdivide_oversized(s, e, max_pages, lab):
                expanded.append((ss, ee, lab2))
        else:
            expanded.append((s, e, lab))
    return expanded


def _pack_atomics_optimal(
    atomics: list[tuple[int, int, str]],
    max_pages: int,
) -> list[tuple[str, int, int]]:
    """
    只在原子区间边界上合并：每段 ≤ max_pages。
    先最小化段数；段数相同时最大化「各段页数里的最小值」，减轻尾段只有几十页、前段顶满 max 的情况。
    """
    expanded = _expand_atomics_to_max(atomics, max_pages)
    if not expanded:
        return []

    sizes = [e - s for s, e, _ in expanded]
    n = len(sizes)
    INF_P = n + 100
    INF_M = 10**9
    best_parts: list[int] = [INF_P] * (n + 1)
    best_minseg: list[int] = [0] * (n + 1)
    prev_cut: list[int] = [-1] * (n + 1)
    best_parts[0] = 0
    best_minseg[0] = INF_M

    for j in range(1, n + 1):
        ssum = 0
        for i in range(j - 1, -1, -1):
            ssum += sizes[i]
            if ssum > max_pages:
                break
            if best_parts[i] >= INF_P:
                continue
            np = best_parts[i] + 1
            nmin = min(best_minseg[i], ssum)
            if best_parts[j] > np or (best_parts[j] == np and nmin > best_minseg[j]):
                best_parts[j] = np
                best_minseg[j] = nmin
                prev_cut[j] = i

    if best_parts[n] >= INF_P:
        return []

    groups: list[list[int]] = []
    j = n
    while j > 0:
        i = prev_cut[j]
        if i < 0:
            break
        groups.append(list(range(i, j)))
        j = i
    groups.reverse()

    out: list[tuple[str, int, int]] = []
    for idxs in groups:
        s0, _, lab0 = expanded[idxs[0]]
        _, e1, _ = expanded[idxs[-1]]
        labels = [expanded[k][2] for k in idxs]
        if len(labels) == 1:
            merged_title = labels[0]
        else:
            merged_title = f"{labels[0]} … {labels[-1]}（{len(labels)} 节）"
        out.append((merged_title, s0, e1))
    return out


def _atomics_in_region(
    all_items: list[tuple[str, int]],
    region_start: int,
    region_end: int,
    total_pages: int,
) -> list[tuple[int, int, str]]:
    """区域 [region_start, region_end) 内按书签页码切成连续原子区间（半开）。"""
    by_page: dict[int, str] = {}
    for title, raw_page in all_items:
        p = int(raw_page)
        if p < 0:
            p = 0
        if p >= total_pages:
            p = total_pages - 1
        if not (region_start <= p < region_end):
            continue
        t = str(title).strip() or "?"
        if p not in by_page:
            by_page[p] = t

    inner = sorted({p for p in by_page if region_start < p < region_end})
    breaks = [region_start] + inner + [region_end]
    atomics: list[tuple[int, int, str]] = []
    for k in range(len(breaks) - 1):
        s, e = breaks[k], breaks[k + 1]
        if s >= e:
            continue
        lab = by_page[s] if s in by_page else f"第 {s + 1} 页起"
        atomics.append((s, e, lab))
    return atomics


def _append_subdivided_chapter(
    out: list[tuple[str, int, int]],
    chapter_title: str,
    start_page: int,
    end_page: int,
    all_items: list[tuple[str, int]],
    total_pages: int,
    max_pages: int,
) -> None:
    """超长章：仅在章内用子书签切段并按上限打包，必要时页硬切。"""
    atomics = _atomics_in_region(all_items, start_page, end_page, total_pages)
    packed = _pack_atomics_optimal(atomics, max_pages)
    for subt, s, e in packed:
        out.append((f"{chapter_title} — {subt}", s, e))


def create_chapter_first_ranges(
    chapters: list[tuple[str, int]],
    all_outline_items: list[tuple[str, int]],
    total_pages: int,
    max_pages: int,
) -> list[tuple[str, int, int]]:
    """
    章优先：每章页数 ≤ max_pages 时整章一段；超长章在章内沿子书签打包至 ≤ max_pages。
    """
    if total_pages <= 0:
        return []
    out: list[tuple[str, int, int]] = []

    first_start = chapters[0][1] if chapters else 0
    if first_start > 0:
        if first_start <= max_pages:
            out.append(("文前", 0, first_start))
        else:
            _append_subdivided_chapter(out, "文前", 0, first_start, all_outline_items, total_pages, max_pages)

    for i, (ch_title, start_page) in enumerate(chapters):
        end_page = chapters[i + 1][1] if i + 1 < len(chapters) else total_pages
        ch_title = str(ch_title).strip() or "?"
        n = end_page - start_page
        if n <= max_pages:
            out.append((ch_title, start_page, end_page))
        else:
            _append_subdivided_chapter(out, ch_title, start_page, end_page, all_outline_items, total_pages, max_pages)

    return out


def create_outline_smart_ranges(
    items: list[tuple[str, int]],
    total_pages: int,
    max_pages: int,
) -> list[tuple[str, int, int]]:
    """
    在「全书书签」形成的相邻区间上切段，再合并使每份 ≤ max_pages。
    """
    if total_pages <= 0:
        return []

    by_page: dict[int, str] = {}
    for title, raw_page in items:
        p = int(raw_page)
        if p < 0:
            p = 0
        if p >= total_pages:
            p = total_pages - 1
        t = str(title).strip() or "?"
        if p not in by_page:
            by_page[p] = t

    page_starts = sorted(by_page.keys())
    breaks: list[int] = [0]
    for p in page_starts:
        if p > breaks[-1]:
            breaks.append(p)
    if breaks[-1] < total_pages:
        breaks.append(total_pages)

    atomics: list[tuple[int, int, str]] = []
    for i in range(len(breaks) - 1):
        s, e = breaks[i], breaks[i + 1]
        if s >= e:
            continue
        label = by_page[s] if s in by_page else ("开头" if s == 0 else f"第 {s + 1} 页起")
        atomics.append((s, e, label))

    return _pack_atomics_optimal(atomics, max_pages)


def fixed_page_ranges(total_pages: int, max_pages: int) -> list[tuple[str, int, int]]:
    out: list[tuple[str, int, int]] = []
    start = 0
    while start < total_pages:
        end = min(start + max_pages, total_pages)
        out.append((f"fixed pages {start + 1}-{end}", start, end))
        start = end
    return out


def _all_bookmark_pages(items: list[tuple[str, int]], total_pages: int) -> list[int]:
    pages: set[int] = set()
    for _, raw in items:
        p = int(raw)
        if p < 0:
            p = 0
        if p >= total_pages:
            p = total_pages - 1
        pages.add(p)
    return sorted(pages)


def _split_page_near_mid_with_bookmarks(start: int, end: int, bookmark_pages: list[int]) -> int:
    """半开 [start,end) 内选切分点（第二段从该 0-based 页开始）；至少留一页给每一段。"""
    if end - start <= 1:
        return start + 1
    mid = (start + end) // 2
    inner = [p for p in bookmark_pages if start < p < end]
    if not inner:
        return mid if start < mid < end else start + 1
    return min(inner, key=lambda p: abs(p - mid))


def _expand_one_range_by_file_size(
    pdf_path: Path,
    title: str,
    start: int,
    end: int,
    max_bytes: int,
    bookmark_pages: list[int],
    tmp_pdf: Path,
) -> list[tuple[str, int, int]]:
    """
    用 qpdf 抽页到临时文件量体积；超过 max_bytes 则沿书签或中点再切（单页无法再切也保留）。
    """
    if end <= start:
        return []
    run_qpdf_split(pdf_path, start, end, tmp_pdf)
    sz = tmp_pdf.stat().st_size
    if sz <= max_bytes or end - start <= 1:
        tmp_pdf.unlink(missing_ok=True)
        return [(title, start, end)]
    tmp_pdf.unlink(missing_ok=True)
    split_at = _split_page_near_mid_with_bookmarks(start, end, bookmark_pages)
    if split_at <= start or split_at >= end:
        split_at = start + 1
    left = _expand_one_range_by_file_size(
        pdf_path, f"{title}（子段·前）", start, split_at, max_bytes, bookmark_pages, tmp_pdf
    )
    right = _expand_one_range_by_file_size(
        pdf_path, f"{title}（子段·后）", split_at, end, max_bytes, bookmark_pages, tmp_pdf
    )
    return left + right


def expand_ranges_by_output_file_size(
    pdf_path: Path,
    ranges: list[tuple[str, int, int]],
    max_bytes: int,
    bookmark_pages: list[int],
    probe_dir: Path,
) -> list[tuple[str, int, int]]:
    probe_dir.mkdir(parents=True, exist_ok=True)
    tmp_pdf = probe_dir / ".qpdf_size_probe.pdf"
    flat: list[tuple[str, int, int]] = []
    for title, start, end in ranges:
        flat.extend(
            _expand_one_range_by_file_size(pdf_path, title, start, end, max_bytes, bookmark_pages, tmp_pdf)
        )
    tmp_pdf.unlink(missing_ok=True)
    return flat


def run_qpdf_split(src: Path, start: int, end: int, out_pdf: Path) -> None:
    """start/end 为 0-based 半开 [start, end)；qpdf 为 1-based 闭区间。"""
    page_range = f"{start + 1}-{end}"
    cmd = ["qpdf", str(src), "--pages", str(src), page_range, "--", str(out_pdf)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"qpdf 失败: {r.stderr or r.stdout}")


def main() -> None:
    ap = argparse.ArgumentParser(description="拆分 PDF 为 a1 逐段转换用的多份 input/<stem>_ptNNN.pdf")
    ap.add_argument("pdf", type=Path, help="输入 PDF（通常即 input/<STEM>.pdf）")
    ap.add_argument(
        "--base-stem",
        required=True,
        help="主 STEM（与 Makefile 中 STEM 一致，不含 .pdf）；分段文件名为 <base-stem>_pt001.pdf",
    )
    ap.add_argument(
        "--input-dir",
        type=Path,
        default=None,
        help="PDF 输出目录（默认与输入 PDF 同目录，即 input/）",
    )
    ap.add_argument(
        "--max-pages",
        type=int,
        default=200,
        help="单段最大页数；有书签时在子书签边界上合并/切开，仅超长块内按页硬切",
    )
    ap.add_argument(
        "--no-outline",
        action="store_true",
        help="忽略书签，仅按固定页块拆分",
    )
    ap.add_argument(
        "--top-level-only",
        action="store_true",
        help="仅用顶层（+一层子级启发）章界；超长章按固定页宽硬切（不用章内子书签）",
    )
    ap.add_argument(
        "--global-bookmark-merge",
        action="store_true",
        help="全书书签细粒度边界 + 贪心合并（忽略「先整章」）；默认是章优先",
    )
    ap.add_argument(
        "--max-part-kb",
        type=int,
        default=0,
        metavar="KB",
        help="qpdf 导出单段体积超过此值（KB）时再切分；0=关闭。用于图形多的章仍可能页数不多但文件很大",
    )
    args = ap.parse_args()
    if args.max_pages <= 0:
        print("错误: --max-pages 须为正整数（已撤销 max-pages=0 模式）", file=sys.stderr)
        sys.exit(2)

    pdf_path = args.pdf.resolve()
    if not pdf_path.is_file():
        print(f"文件不存在: {pdf_path}", file=sys.stderr)
        sys.exit(1)

    input_dir = (args.input_dir or pdf_path.parent).resolve()
    input_dir.mkdir(parents=True, exist_ok=True)
    base = args.base_stem.strip()

    try:
        subprocess.run(["qpdf", "--version"], capture_output=True, text=True, check=True)
    except Exception:
        print("未找到 qpdf。WSL 请执行: sudo apt-get install -y qpdf", file=sys.stderr)
        sys.exit(1)

    print(f"读取: {pdf_path}")
    reader = PdfReader(str(pdf_path))
    total_pages = len(reader.pages)
    print(f"总页数: {total_pages}")

    outline_items: list[tuple[str, int]] = []
    if args.no_outline:
        print("按固定页块拆分（--no-outline）…")
        ranges = fixed_page_ranges(total_pages, args.max_pages)
    else:
        outline_items, _tp = _reader_outline_pages(reader)
        if not outline_items:
            print("无书签，按固定页块拆分…")
            ranges = fixed_page_ranges(total_pages, args.max_pages)
        elif args.global_bookmark_merge:
            print(
                f"全书书签合并（--global-bookmark-merge），{len(outline_items)} 个锚点，每段 ≤ {args.max_pages} 页…"
            )
            ranges = create_outline_smart_ranges(outline_items, total_pages, args.max_pages)
        elif args.top_level_only:
            chapters = get_top_level_chapters(reader)
            if not chapters:
                print("顶层书签为空，按固定页块拆分…")
                ranges = fixed_page_ranges(total_pages, args.max_pages)
            else:
                print(f"按顶层章界 + 超长章固定页硬切: {len(chapters)} 章（--top-level-only）…")
                ranges = create_chapter_ranges(chapters, total_pages, args.max_pages)
        else:
            chapters = get_top_level_chapters(reader)
            if not chapters:
                print("无章界书签，按固定页块拆分…")
                ranges = fixed_page_ranges(total_pages, args.max_pages)
            else:
                print(
                    f"章优先：{len(chapters)} 章；整章 ≤ {args.max_pages} 页不拆，超长章内子书签并至 ≤ {args.max_pages} 页…"
                )
                ranges = create_chapter_first_ranges(
                    chapters, outline_items, total_pages, args.max_pages
                )

    bookmark_pages = _all_bookmark_pages(outline_items, total_pages) if outline_items else []
    if args.max_part_kb > 0:
        max_b = args.max_part_kb * 1024
        n0 = len(ranges)
        print(
            f"按输出体积二次划分: 任一段 qpdf 导出 > {args.max_part_kb} KB 则再切（优先靠书签页）…"
        )
        ranges = expand_ranges_by_output_file_size(
            pdf_path, ranges, max_b, bookmark_pages, input_dir / ".split_size_probe"
        )
        print(f"  体积切分: {n0} 段 → {len(ranges)} 段")

    parts: list[dict] = []
    for i, (title, start, end) in enumerate(ranges):
        idx = i + 1
        stem = f"{base}_pt{idx:03d}"
        out_pdf = input_dir / f"{stem}.pdf"
        print(f"  [{idx}/{len(ranges)}] {title}: 页 {start + 1}-{end} -> {out_pdf.name}")
        run_qpdf_split(pdf_path, start, end, out_pdf)
        try:
            pdf_rel = str(out_pdf.relative_to(input_dir))
        except ValueError:
            pdf_rel = str(out_pdf)
        parts.append(
            {
                "stem": stem,
                "pdf": pdf_rel,
                "title": title,
                "page_start_1based": start + 1,
                "page_end_1based_inclusive": end,
            }
        )

    manifest = {
        "base_stem": base,
        "source_pdf": str(pdf_path),
        "max_pages": args.max_pages,
        "max_part_kb": args.max_part_kb,
        "parts": parts,
    }
    man_path = input_dir / f"{base}_parts_manifest.json"
    man_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写 manifest: {man_path}")
    print("下一步（在仓库根、WSL bash 下，已 export REPO）：")
    print(f"  make a1-batch STEM={base}")
    print(f"合并: make merge-parts STEM={base}（图集中目录: make merge-assets STEM={base}）")


if __name__ == "__main__":
    main()
