#!/usr/bin/env python3
"""Lightweight checks for B5 digital IC schema v2 prompt/defaults."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
APITEST = ROOT / "apitest"
if str(APITEST) not in sys.path:
    sys.path.insert(0, str(APITEST))

from figure_describe_core import FigureMeta, build_prompt, normalize_unknowns  # noqa: E402


def main() -> int:
    meta = FigureMeta(
        image_id="fig_test",
        doc_id="doc",
        page=1,
        section="section",
        image_type="unknown",
        ocr_text="PCLK RxValid RxElecIdle",
        image_path="image.png",
        document_context="Receiver Active to Idle timing diagram",
    )
    prompt = build_prompt(meta)
    required_prompt_terms = [
        "digital_ic_semantics",
        '"signals"',
        '"interfaces"',
        '"transactions"',
        '"timing_constraints"',
        '"phases"',
        '"assumptions"',
        '"uncertain_items"',
        "do not invent timing values",
        "compressed time",
    ]
    missing = [term for term in required_prompt_terms if term not in prompt]
    if missing:
        print(f"prompt missing terms: {missing}", file=sys.stderr)
        return 1

    normalized = normalize_unknowns({})
    dic = normalized.get("digital_ic_semantics")
    if not isinstance(dic, dict):
        print("digital_ic_semantics missing/default not dict", file=sys.stderr)
        return 1
    for key in (
        "signals",
        "interfaces",
        "transactions",
        "timing_constraints",
        "phases",
        "assumptions",
        "uncertain_items",
    ):
        if not isinstance(dic.get(key), list):
            print(f"digital_ic_semantics.{key} default is not list", file=sys.stderr)
            return 1

    print("digital_ic_schema_v2 checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
