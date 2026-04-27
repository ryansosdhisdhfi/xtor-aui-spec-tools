#!/usr/bin/env python3
"""
批量 VLM：读 filter_images 的 kept + figure_context + ocr_by_figure，按 basename 对齐，
为每张图生成与 describe_image_wsl 相同 schema 的 JSON。

用法 (仓库根):
  export OPENAI_API_KEY=...
  python batch_describe.py \\
    --filtered-json user-run/e2e_10p_20260422/e2e_10p.images.filtered.json \\
    --figure-context user-run/e2e_10p_20260422/e2e_10p.figure_context.json \\
    --ocr-json user-run/e2e_10p_20260422/e2e_10p.ocr_by_figure.json \\
    --out-dir user-run/e2e_10p_20260422/figure_schemas \\
    --doc-id e2e_10p
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path
from typing import Any

# apitest 可导入
_ROOT = Path(__file__).resolve().parent
_APITEST = _ROOT / "apitest"
if str(_APITEST) not in sys.path:
    sys.path.insert(0, str(_APITEST))

from figure_describe_core import (  # noqa: E402
    FigureMeta,
    build_prompt,
    default_api_key,
    run_figure_describe,
)


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def build_document_context(
    fig: dict[str, Any] | None,
    max_chars: int,
) -> str:
    if not fig:
        return ""
    parts: list[str] = []
    chain = fig.get("heading_chain")
    if isinstance(chain, list) and chain:
        parts.append("Headings: " + " > ".join(str(x) for x in chain))
    before = (fig.get("context_before") or "").strip()
    after = (fig.get("context_after") or "").strip()
    if before:
        parts.append("[Before the figure in Markdown]\n" + before)
    if after:
        parts.append("[After the figure in Markdown]\n" + after)
    text = "\n\n".join(parts).strip()
    if not text:
        return ""
    if max_chars > 0 and len(text) > max_chars:
        return text[:max_chars] + "\n... [context truncated]"
    return text


def main() -> int:
    ap = argparse.ArgumentParser(description="Batch figure describe via VLM (same schema as apitest)")
    ap.add_argument("--filtered-json", required=True, help="filter_images 输出 *.images.filtered.json")
    ap.add_argument(
        "--figure-context",
        default="",
        help="extract_figure_context 输出 *.figure_context.json；空则仅用语义元数据",
    )
    ap.add_argument(
        "--ocr-json",
        default="",
        help="ocr_figure_artifacts 输出；空则 ocr_text=unknown",
    )
    ap.add_argument("--out-dir", required=True, help="每图 {image_id}.json + batch_report.json")
    ap.add_argument("--doc-id", default="doc", help="写入 FigureMeta.doc_id / JSON")
    ap.add_argument("--image-type", default="unknown", help="图类型提示")
    ap.add_argument("--api-key", default="", help="或环境变量 OPENAI_API_KEY")
    ap.add_argument("--model", default="gpt-5.4")
    ap.add_argument(
        "--base-url",
        default="https://backend.intelalloc.com",
        help="与 describe_image_wsl 一致，通常不带 /v1",
    )
    ap.add_argument(
        "--max-context-chars",
        type=int,
        default=6000,
        help="document_context 最大字符，0=不截断",
    )
    ap.add_argument("--skip-existing", action="store_true", help="若输出 JSON 已存在且可解析则跳过")
    ap.add_argument(
        "--delay",
        type=float,
        default=0.0,
        help="每张之间睡眠秒数（防限流）",
    )
    ap.add_argument(
        "--jitter",
        type=float,
        default=0.0,
        help="在 delay 上再加 0..jitter 秒随机",
    )
    ap.add_argument("--merge-out", default="", help="若指定，另写一份合并了所有条目的 JSON 数组")
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument(
        "--no-progress",
        action="store_true",
        help="不打印 [当前/总数] 进度行（默认可在 tee 日志里跟跑位）",
    )
    args = ap.parse_args()

    api_key = default_api_key(args.api_key)
    if not api_key:
        print("错误: 请设置 --api-key 或 OPENAI_API_KEY", file=sys.stderr)
        return 1

    filtered_path = Path(args.filtered_json).resolve()
    data = load_json(filtered_path)
    kept: list[dict[str, Any]] = data.get("kept") or []
    if not kept:
        print("错误: kept 为空", file=sys.stderr)
        return 1

    total_kept = len(kept)
    show_progress = not args.no_progress

    ctx_by_base: dict[str, Any] = {}
    if args.figure_context:
        ctx_path = Path(args.figure_context).resolve()
        cj = load_json(ctx_path)
        ctx_by_base = cj.get("by_basename")
        if not isinstance(ctx_by_base, dict):
            ctx_by_base = {}
        if not ctx_by_base and isinstance(cj.get("figures"), list):
            for f in cj["figures"]:
                if isinstance(f, dict) and f.get("image_basename"):
                    ctx_by_base[str(f["image_basename"])] = f

    ocr_by_base: dict[str, Any] = {}
    if args.ocr_json:
        oj = load_json(Path(args.ocr_json).resolve())
        ocr_by_base = oj.get("by_basename") or {}

    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    if show_progress:
        print(
            f"[batch_describe] 共 {total_kept} 张（kept），开始逐张处理…\n",
            flush=True,
        )

    report_items: list[dict[str, Any]] = []
    merged: list[dict[str, Any]] = []

    for idx, rec in enumerate(kept, 1):
        pct = min(100, (100 * idx) // total_kept) if total_kept else 100
        if not isinstance(rec, dict):
            if show_progress:
                print(f"[{idx}/{total_kept}] ({pct}%) 跳过: 非 dict 记录", flush=True)
            continue
        iid = str(rec.get("image_id") or "unknown")
        if show_progress:
            print(f"[{idx}/{total_kept}] ({pct}%) {iid}", flush=True)
        p = rec.get("_artifact_path")
        if not p:
            report_items.append(
                {
                    "image_id": iid,
                    "ok": False,
                    "error": "missing _artifact_path",
                    "output": None,
                }
            )
            if show_progress:
                print("    x 缺少 _artifact_path", flush=True)
            continue
        image_path = Path(str(p))
        if not image_path.is_file():
            report_items.append(
                {
                    "image_id": iid,
                    "ok": False,
                    "error": f"file_not_found: {image_path}",
                    "output": None,
                }
            )
            if show_progress:
                print(f"    x 文件不存在: {image_path}", flush=True)
            continue
        bn = image_path.name

        out_file = out_dir / f"{iid}.json"
        if args.skip_existing and out_file.is_file():
            try:
                prev = load_json(out_file)
                if isinstance(prev, dict) and prev:
                    if args.verbose:
                        print(f"skip existing {out_file.name}")
                    if show_progress:
                        print("    o 已存在，跳过 VLM", flush=True)
                    report_items.append(
                        {
                            "image_id": iid,
                            "ok": True,
                            "error": None,
                            "output": str(out_file),
                            "skipped": True,
                        }
                    )
                    merged.append(prev)
                    continue
            except (json.JSONDecodeError, OSError):
                pass

        fig_ctx: dict[str, Any] = {}
        if isinstance(ctx_by_base, dict) and bn in ctx_by_base:
            fc = ctx_by_base[bn]
            fig_ctx = fc if isinstance(fc, dict) else {}
        document_context = build_document_context(fig_ctx, args.max_context_chars)

        ocr_text = "unknown"
        oentry = ocr_by_base.get(bn) if isinstance(ocr_by_base, dict) else None
        if isinstance(oentry, dict):
            if oentry.get("ocr_error"):
                ocr_text = f"ocr_failed: {oentry.get('ocr_error')}"
            else:
                ot = oentry.get("ocr_text")
                if isinstance(ot, str) and ot.strip():
                    ocr_text = ot

        try:
            page = int(rec.get("page_no") or 0)
        except (TypeError, ValueError):
            page = 0
        section = "unknown"
        if fig_ctx.get("heading_chain"):
            h = fig_ctx["heading_chain"]
            if isinstance(h, list) and h:
                section = str(h[-1])

        meta = FigureMeta(
            image_id=iid,
            doc_id=args.doc_id,
            page=page,
            section=section,
            image_type=args.image_type,
            ocr_text=ocr_text,
            image_path=str(image_path),
            document_context=document_context,
        )
        prompt = build_prompt(meta)
        t0 = time.time()
        try:
            result_json = run_figure_describe(
                image_path,
                prompt,
                api_key=api_key,
                base_url=args.base_url,
                model=args.model,
            )
        except Exception as e:  # noqa: BLE001
            err = str(e)
            if len(err) > 2000:
                err = err[:2000] + "..."
            report_items.append(
                {
                    "image_id": iid,
                    "ok": False,
                    "error": err,
                    "output": None,
                }
            )
            if args.verbose:
                raise
            if show_progress:
                err_one = str(e)
                if len(err_one) > 300:
                    err_one = err_one[:300] + "..."
                print(f"    x FAIL: {err_one}", flush=True)
            else:
                print(f"FAIL {iid}: {e}", file=sys.stderr)
            continue
        dt = time.time() - t0
        out_file.write_text(
            json.dumps(result_json, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        report_items.append(
            {
                "image_id": iid,
                "ok": True,
                "error": None,
                "output": str(out_file),
                "seconds": round(dt, 2),
            }
        )
        merged.append(result_json)
        if show_progress:
            print(f"    OK -> {out_file.name} ({dt:.1f}s)", flush=True)
        else:
            print(f"OK {iid} -> {out_file.name} ({dt:.1f}s)")

        d = args.delay + (random.random() * args.jitter if args.jitter else 0.0)
        if d > 0:
            time.sleep(d)

    report_path = out_dir / "batch_report.json"
    report_path.write_text(
        json.dumps(
            {
                "filtered": str(filtered_path),
                "figure_context": args.figure_context or None,
                "ocr_json": args.ocr_json or None,
                "out_dir": str(out_dir),
                "items": report_items,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"Report: {report_path}", flush=True)
    n_ok = sum(1 for x in report_items if x.get("ok"))
    n_items = len(report_items)
    print(
        f"Done: 成功(含跳过) {n_ok}/{n_items} ；kept 输入 {total_kept} 条",
        flush=True,
    )

    if args.merge_out:
        mp = Path(args.merge_out).resolve()
        mp.parent.mkdir(parents=True, exist_ok=True)
        mp.write_text(
            json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"Merged: {mp}")

    return 0 if n_ok == len(report_items) else 1


if __name__ == "__main__":
    raise SystemExit(main())
