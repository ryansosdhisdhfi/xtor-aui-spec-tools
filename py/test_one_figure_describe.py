#!/usr/bin/env python3
"""单张图 VLM 冒烟：验证 figure_describe_core requests 路径。"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_ROOT / "apitest"))
from figure_describe_core import FigureMeta, build_prompt, run_figure_describe  # noqa: E402


def main() -> int:
    stem = os.environ.get("STEM", "HDMI_2_2_Standard")
    filtered_path = _ROOT.parent / "output" / f"{stem}.images.filtered.json"
    if not filtered_path.is_file():
        print(f"缺少 {filtered_path}", file=sys.stderr)
        return 1

    api_key = os.environ.get("API_KEY", "").strip()
    base_url = os.environ.get("API_URL", "").strip()
    model = os.environ.get("MODEL", "").strip()
    if not api_key or not base_url or not model:
        print("请先 source secrets.sh（API_KEY / API_URL / MODEL）", file=sys.stderr)
        return 1

    data = json.loads(filtered_path.read_text(encoding="utf-8"))
    kept = data.get("kept") or []
    if not kept:
        print("kept 为空", file=sys.stderr)
        return 1

    rec = kept[0]
    img = Path(str(rec["_artifact_path"]))
    print(f"image_id: {rec.get('image_id')}")
    print(f"path: {img}")
    print(f"exists: {img.is_file()}")
    if not img.is_file():
        return 1

    meta = FigureMeta(
        image_id=str(rec.get("image_id", img.stem)),
        doc_id=stem,
        page=int(rec.get("page") or 0),
        section=str(rec.get("section") or ""),
        image_type=str(rec.get("image_type") or "unknown"),
        ocr_text="",
        image_path=str(img),
    )
    result = run_figure_describe(
        img,
        build_prompt(meta),
        api_key=api_key,
        base_url=base_url,
        model=model,
    )
    required = [
        "image_id",
        "doc_id",
        "page",
        "section",
        "image_type",
        "title",
        "summary",
        "keywords",
        "entities",
        "retrieval_text",
        "ocr_text",
        "diagram_semantics",
        "uncertainties",
        "confidence",
        "image_path",
    ]
    missing = [k for k in required if k not in result]
    if missing:
        print("schema 缺字段:", missing, file=sys.stderr)
        return 1
    ds = result.get("diagram_semantics")
    if not isinstance(ds, dict):
        print("diagram_semantics 非 dict", file=sys.stderr)
        return 1
    for k in ("participants", "interactions", "nodes", "edges", "components", "relations"):
        if k not in ds:
            print(f"diagram_semantics 缺 {k}", file=sys.stderr)
            return 1

    out_dir = _ROOT.parent / "output" / "figure_schemas"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"{meta.image_id}.json"
    result["image_path"] = str(rec.get("_artifact_path_rel") or meta.image_path)
    out_file.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print("OK — 完整 schema JSON 已写入:", out_file)
    print("字段数:", len(result), "diagram_semantics 子键:", list(ds.keys()))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
