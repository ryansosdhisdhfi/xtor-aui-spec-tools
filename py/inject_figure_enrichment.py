#!/usr/bin/env python3
"""
将 VLM 产出的每图 JSON（短摘要、关键词等）批量插入 Markdown 中对应 ![]() 图链**下方**。

全量结构仍保留在侧车 figure_schemas/*.json；本脚本只回写 RAG/阅读友好的短块。

会识别并替换已有块：从 <!-- figure-enrich: 到 <!-- /figure-enrich -->。

用法:
  python inject_figure_enrichment.py \\
    --md user-run/e2e_10p_20260422/e2e_10p_clean.md \\
    --schemas-dir user-run/e2e_10p_20260422/figure_schemas \\
    -o user-run/e2e_10p_20260422/e2e_10p_enriched.md

或:
  --merged-json user-run/.../e2e_10p.descriptions_merged.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

MD_IMG = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")


def parse_image_href(line: str) -> str | None:
    m = MD_IMG.search(line)
    if not m:
        return None
    href = m.group(2).strip()
    if href.startswith(('"', "'")) or href.split()[0].startswith(("http://", "https://", "data:")):
        return None
    return href.split()[0].strip('"')


def load_dir(dir_path: Path) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    by_base: dict[str, dict[str, Any]] = {}
    by_id: dict[str, dict[str, Any]] = {}
    skip = {"batch_report.json", "merge_report.json"}
    for f in sorted(dir_path.glob("*.json")):
        if f.name in skip or f.name.startswith("."):
            continue
        data = json.loads(f.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or "image_id" not in data:
            continue
        iid = str(data.get("image_id") or "")
        if iid:
            by_id[iid] = data
        ip = data.get("image_path")
        if ip:
            by_base[Path(str(ip)).name] = data
    return by_base, by_id


def load_merged(path: Path) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    by_base: dict[str, dict[str, Any]] = {}
    by_id: dict[str, dict[str, Any]] = {}
    if not isinstance(raw, list):
        raise ValueError("merged JSON 应为数组")
    for data in raw:
        if not isinstance(data, dict) or "image_id" not in data:
            continue
        iid = str(data.get("image_id") or "")
        if iid:
            by_id[iid] = data
        ip = data.get("image_path")
        if ip:
            by_base[Path(str(ip)).name] = data
    return by_base, by_id


def _clean_text(value: Any, default: str = "unknown") -> str:
    text = str(value or "").strip()
    return text or default


def _truncate(text: str, limit: int) -> str:
    if limit > 0 and len(text) > limit:
        return text[:limit].rstrip() + "…"
    return text


def _render_signals(data: dict[str, Any], *, limit: int = 10) -> list[str]:
    items = data.get("signals")
    if not isinstance(items, list) or not items:
        return []
    lines = [f"### Signals ({len(items)})", ""]
    for sig in items[:limit]:
        if not isinstance(sig, dict):
            continue
        name = _clean_text(sig.get("name"))
        sig_type = _clean_text(sig.get("type"))
        producer = _clean_text(sig.get("producer"))
        consumer = _clean_text(sig.get("consumer"))
        active = _clean_text(sig.get("active_level"))
        direction = "-" if producer == "unknown" and consumer == "unknown" else f"{producer} -> {consumer}"
        lines.append(f"- `{name}`: {sig_type}; direction={direction}; active={active}")
    if len(items) > limit:
        lines.append(f"- ... and {len(items) - limit} more")
    lines.append("")
    return lines


def _render_transactions(data: dict[str, Any], *, limit: int = 8) -> list[str]:
    items = data.get("transactions")
    if not isinstance(items, list) or not items:
        return []
    lines = [f"### Transactions ({len(items)})", ""]
    for txn in items[:limit]:
        if not isinstance(txn, dict):
            continue
        order = txn.get("order", "?")
        bus = _clean_text(txn.get("bus"))
        operation = _clean_text(txn.get("operation"))
        producer = _clean_text(txn.get("producer"))
        consumer = _clean_text(txn.get("consumer"))
        payload = _clean_text(txn.get("payload_or_purpose"), default="")
        target = _clean_text(txn.get("target"), default="")
        detail = f"{producer} -> {consumer}: {operation}"
        if target and target != "unknown":
            detail += f" target={target}"
        if payload and payload != "unknown":
            detail += f" purpose={payload}"
        lines.append(f"{order}. [{bus}] {detail}")
    if len(items) > limit:
        lines.append(f"- ... and {len(items) - limit} more")
    lines.append("")
    return lines


def _render_timing_constraints(data: dict[str, Any]) -> list[str]:
    items = data.get("timing_constraints")
    if not isinstance(items, list) or not items:
        return []
    lines = [f"### Timing Constraints ({len(items)})", ""]
    for tc in items:
        if not isinstance(tc, dict):
            continue
        name = _clean_text(tc.get("name"))
        start = _clean_text(tc.get("start_event"))
        end = _clean_text(tc.get("end_event"))
        max_latency = _clean_text(tc.get("max_latency"), default="")
        evidence = _clean_text(tc.get("evidence_text"), default="")
        line = f"- **{name}**: {start} -> {end}"
        if max_latency and max_latency != "unknown":
            line += f"; max={max_latency}"
        if evidence and evidence != "unknown":
            line += f'; evidence="{evidence}"'
        lines.append(line)
    lines.append("")
    return lines


def _render_assumptions(data: dict[str, Any], *, limit: int = 6) -> list[str]:
    items = data.get("assumptions")
    if not isinstance(items, list) or not items:
        return []
    lines = [f"### Assumptions ({len(items)})", ""]
    for item in items[:limit]:
        if not isinstance(item, dict):
            continue
        assumption = _clean_text(item.get("assumption"))
        basis = _clean_text(item.get("basis"))
        try:
            confidence = float(item.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        lines.append(f"- {assumption} (basis={basis}, confidence={confidence:.2f})")
    if len(items) > limit:
        lines.append(f"- ... and {len(items) - limit} more")
    lines.append("")
    return lines


def render_digital_ic_semantics(data: dict[str, Any]) -> list[str]:
    dic = data.get("digital_ic_semantics")
    if not isinstance(dic, dict):
        return []
    lines: list[str] = []
    for section in (
        _render_signals(dic),
        _render_transactions(dic),
        _render_timing_constraints(dic),
        _render_assumptions(dic),
    ):
        if section:
            lines.extend(section)
    return lines


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="将 VLM 短摘要插入 MD 图链下方")
    ap.add_argument("--md", required=True, help="输入 Markdown（一般为 *_clean.md）")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--schemas-dir", help="每图一个 fig_xxxx.json 的目录")
    g.add_argument("--merged-json", help="descriptions_merged.json 等数组")
    ap.add_argument("-o", "--output", required=True, help="输出路径（不要覆盖时可写 *_enriched.md）")
    ap.add_argument("--source-tag", default="vlm-v2", help="写入 HTML 注释的 source= 值")
    ap.add_argument("--max-summary-chars", type=int, default=1200, help="summary 截断，0=不截")
    ap.add_argument("--max-keywords", type=int, default=10, help="0=全部")
    ap.add_argument(
        "--include-ocr",
        action="store_true",
        help="在块尾追加 ocr 摘录",
    )
    ap.add_argument("--max-ocr-chars", type=int, default=500)
    ap.add_argument(
        "--in-place",
        action="store_true",
        help="危险：直接覆盖 --md 指向的文件；默认关闭",
    )
    return ap


def render_block(
    data: dict[str, Any],
    source: str,
    *,
    max_summary: int,
    max_keywords: int,
    include_ocr: bool,
    max_ocr: int,
) -> str:
    iid = str(data.get("image_id") or "unknown")
    title = (data.get("title") or "unknown").strip() or "unknown"
    summary = (data.get("summary") or "").strip()
    summary = _truncate(summary, max_summary)

    kws: list[str] = []
    raw_k = data.get("keywords")
    if isinstance(raw_k, list):
        for x in raw_k[:max_keywords] if max_keywords > 0 else raw_k:
            s = str(x).strip()
            if s:
                kws.append(s)

    parts = [
        f"<!-- figure-enrich: source={source} image_id={iid} -->",
        f"**Figure (enriched):** {title}",
        "",
        summary,
        "",
        "*Keywords:* " + ", ".join(kws),
    ]
    ocr = (data.get("ocr_text") or "").strip()
    if include_ocr and ocr and ocr != "unknown":
        excerpt = ocr if max_ocr <= 0 else ocr[:max_ocr]
        if max_ocr > 0 and len(ocr) > max_ocr:
            excerpt = excerpt.rstrip() + "…"
        parts.extend(["", f"*OCR excerpt:* {excerpt}"])
    semantic_lines = render_digital_ic_semantics(data)
    if semantic_lines:
        parts.extend([""] + semantic_lines[:-1])
    parts.append("<!-- /figure-enrich -->")
    return "\n".join(parts) + "\n"


def process_md(
    lines: list[str],
    by_base: dict[str, dict[str, Any]],
) -> list[str]:
    out: list[str] = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        href = parse_image_href(line)
        out.append(line)
        i += 1
        if not href:
            continue
        basename = Path(href).name
        data = by_base.get(basename)
        # 去掉已存在的回写块
        if i < n and (lines[i].strip().startswith("<!-- figure-enrich:") or lines[i].strip() == "<!-- figure-enrich -->"):
            while i < n and lines[i].strip() != "<!-- /figure-enrich -->":
                i += 1
            if i < n:
                i += 1
        if not data:
            continue
        block_text = data.get("_injected_block")
        if not block_text:
            continue
        for bl in block_text.rstrip().split("\n"):
            out.append(bl + "\n")
    return out


def main() -> int:
    ap = build_arg_parser()
    args = ap.parse_args()

    md_path = Path(args.md).resolve()
    if not md_path.is_file():
        print(f"错误: 无文件 {md_path}", file=sys.stderr)
        return 1
    if args.schemas_dir:
        by_base, _by_id = load_dir(Path(args.schemas_dir).resolve())
    else:
        by_base, _by_id = load_merged(Path(args.merged_json).resolve())
    # 预生成块文本
    for key, data in list(by_base.items()):
        if not isinstance(data, dict):
            continue
        data["_injected_block"] = render_block(
            data,
            args.source_tag,
            max_summary=args.max_summary_chars,
            max_keywords=args.max_keywords,
            include_ocr=args.include_ocr,
            max_ocr=args.max_ocr_chars,
        )
    out_path = Path(args.output).resolve()
    text = md_path.read_text(encoding="utf-8")
    # 保留换行风格
    if text.endswith("\n"):
        nl = "\n"
    else:
        nl = ""
    lines = text.splitlines(keepends=True)
    for li, line in enumerate(lines):
        if not line.endswith("\n"):
            lines[li] = line + "\n"
    new_lines = process_md(lines, by_base)
    body = "".join(new_lines)
    if nl and not body.endswith("\n"):
        body += "\n"
    if args.in_place:
        out_path = md_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(body, encoding="utf-8")
    n_enrich = sum(1 for k, d in by_base.items() if isinstance(d, dict) and d.get("_injected_block"))
    print(f"已写入: {out_path}  侧车 schema 数(按 basename)={len(by_base)}  预生成块数={n_enrich}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
