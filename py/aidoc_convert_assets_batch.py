#!/usr/bin/env python3
"""
多份 PDF 在同一进程内依次转换：Docling 管线只初始化一次，避免每段重复加载权重。

典型用法（配合 split_pdf_for_a1.py 的 manifest）:
  cd /path/to/xtor-aui-spec-tools
  source .venv/bin/activate
  python3 py/aidoc_convert_assets_batch.py \\
    --manifest input/MYBOOK_parts_manifest.json \\
    --output-dir output \\
    --device cuda --stats -v

也可不用 manifest，直接列出 PDF:
  python3 py/aidoc_convert_assets_batch.py -o output a.pdf b.pdf c.pdf
"""
from __future__ import annotations

import argparse
import gc
import json
import sys
import time
from pathlib import Path

from aidoc_utils import print_banner

from aidoc_convert_assets import (
    add_standard_convert_options,
    convert_one_pdf,
    docling_converter_from_args,
)


def build_batch_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="批量 PDF→MD：单进程单次加载 Docling，适合分段拆分后连转",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help="split_pdf_for_a1 生成的 *_parts_manifest.json",
    )
    p.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Markdown 输出目录（默认: 仓库 output/，即 manifest 父目录的 ../output）",
    )
    p.add_argument(
        "pdfs",
        nargs="*",
        type=Path,
        help="若未指定 --manifest，则按顺序转换这些 PDF；输出为 <output-dir>/<stem>.md",
    )
    p.add_argument(
        "--reload-every",
        type=int,
        default=0,
        metavar="N",
        help="每成功转换 N 个 PDF 后销毁并重建 Docling 转换器，并尝试 torch.cuda.empty_cache()（0=全程单实例）",
    )
    p.add_argument(
        "--start-index",
        type=int,
        default=1,
        metavar="N",
        help="仅与 --manifest 连用：从第 N 个分段开始（1-based，含 N）；用于中断后续跑",
    )
    p.add_argument(
        "--skip-existing",
        action="store_true",
        help="输出 .md 已存在且非空则跳过（适合与 --start-index 续跑）",
    )
    add_standard_convert_options(p)
    return p


def _jobs_from_manifest(manifest_path: Path, output_dir: Path) -> list[tuple[Path, Path]]:
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    parts = data.get("parts") or []
    if not parts:
        print("manifest 中无 parts", file=sys.stderr)
        sys.exit(1)
    base_dir = manifest_path.parent
    jobs: list[tuple[Path, Path]] = []
    for part in parts:
        stem = part.get("stem")
        if not stem:
            continue
        rel = part.get("pdf")
        pdf_path = (base_dir / rel) if rel else (base_dir / f"{stem}.pdf")
        pdf_path = pdf_path.resolve()
        if not pdf_path.is_file():
            print(f"缺少分段 PDF: {pdf_path}", file=sys.stderr)
            sys.exit(2)
        out_md = (output_dir / f"{stem}.md").resolve()
        jobs.append((pdf_path, out_md))
    return jobs


def _jobs_from_pdfs(pdfs: list[Path], output_dir: Path) -> list[tuple[Path, Path]]:
    jobs: list[tuple[Path, Path]] = []
    for pdf in pdfs:
        pdf = pdf.resolve()
        if not pdf.is_file():
            print(f"文件不存在: {pdf}", file=sys.stderr)
            sys.exit(2)
        stem = pdf.stem
        jobs.append((pdf, (output_dir / f"{stem}.md").resolve()))
    return jobs


def main() -> None:
    parser = build_batch_parser()
    args = parser.parse_args()

    if args.manifest and args.pdfs:
        print("请勿同时使用 --manifest 与位置参数 PDF", file=sys.stderr)
        sys.exit(1)
    if not args.manifest and not args.pdfs:
        parser.print_help()
        sys.exit(1)

    if args.manifest:
        man = args.manifest.resolve()
        if not man.is_file():
            print(f"找不到 manifest: {man}", file=sys.stderr)
            sys.exit(1)
        out_dir = args.output_dir
        if out_dir is None:
            out_dir = (man.parent.parent / "output").resolve()
        else:
            out_dir = out_dir.resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        jobs = _jobs_from_manifest(man, out_dir)
        total_in_manifest = len(jobs)
        start_idx = max(1, int(getattr(args, "start_index", 1) or 1))
        if start_idx > total_in_manifest:
            print(f"--start-index {start_idx} 超过 manifest 段数 {total_in_manifest}", file=sys.stderr)
            sys.exit(2)
        if start_idx > 1:
            jobs = jobs[start_idx - 1 :]
        re = getattr(args, "reload_every", 0) or 0
        if start_idx > 1:
            title = (
                f"aidoc_convert 续跑 (manifest {start_idx}-{total_in_manifest}，共 {len(jobs)} 段"
                + (f"，每 {re} 个重建管线)" if re > 0 else "，单次加载模型)")
            )
        else:
            title = (
                f"aidoc_convert 批量 ({len(jobs)} 段，每 {re} 个重建管线)"
                if re > 0
                else f"aidoc_convert 批量 ({len(jobs)} 段，单次加载模型)"
            )
    else:
        out_dir = args.output_dir
        if out_dir is None:
            print("未指定 --manifest 时必须使用 --output-dir", file=sys.stderr)
            sys.exit(1)
        out_dir = out_dir.resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        jobs = _jobs_from_pdfs(list(args.pdfs), out_dir)
        re = getattr(args, "reload_every", 0) or 0
        title = (
            f"aidoc_convert 批量 ({len(jobs)} 个文件，每 {re} 个重建管线)"
            if re > 0
            else f"aidoc_convert 批量 ({len(jobs)} 个文件，单次加载模型)"
        )

    print_banner(title)
    print(f"输出目录: {out_dir}\n")

    try:
        converter = docling_converter_from_args(args)
    except Exception as e:
        print(f"初始化 Docling 失败: {e}", file=sys.stderr)
        sys.exit(1)

    total_start = time.time()
    ok = 0
    reload_every = max(0, int(getattr(args, "reload_every", 0) or 0))
    skip_existing = bool(getattr(args, "skip_existing", False))

    def _maybe_reload_converter() -> None:
        nonlocal converter
        if not reload_every or ok % reload_every != 0 or ok >= len(jobs):
            return
        print(
            f"\n>>> 已完成 {ok}/{len(jobs)}，按 --reload-every {reload_every} 重建 Docling 管线（释放显存/缓存）<<<\n",
            flush=True,
        )
        del converter
        gc.collect()
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass
        converter = docling_converter_from_args(args)

    for i, (pdf_path, out_path) in enumerate(jobs, start=1):
        print(f"========== [{i}/{len(jobs)}] {pdf_path.name} -> {out_path.name} ==========")
        if skip_existing and out_path.is_file() and out_path.stat().st_size > 0:
            print(f"  跳过（已有非空）: {out_path}")
            ok += 1
            _maybe_reload_converter()
            continue
        try:
            convert_one_pdf(
                converter,
                pdf_path,
                out_path,
                args,
                print_header=bool(args.stats or args.verbose),
            )
            if not (args.stats or args.verbose):
                print(f"  完成: {out_path} ({out_path.stat().st_size // 1024} KB)")
            ok += 1
            _maybe_reload_converter()
        except Exception as e:
            print(f"\n错误: {pdf_path} 转换失败 - {e}", file=sys.stderr)
            if args.verbose:
                import traceback
                traceback.print_exc()
            sys.exit(1)

    elapsed = time.time() - total_start
    print(f"\n全部完成: {ok}/{len(jobs)} 份，总耗时 {elapsed:.1f}s ({elapsed/60:.1f} min)")


if __name__ == "__main__":
    main()
