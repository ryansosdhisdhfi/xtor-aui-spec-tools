"""
VLM 技术图 -> 结构化 JSON：共用 build_prompt、normalize_unknowns、单次 API 调用。
供 describe_image_wsl.py 与 batch_describe.py 使用。
"""

from __future__ import annotations

import base64
import json
import mimetypes
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openai import OpenAI


@dataclass
class FigureMeta:
    image_id: str
    doc_id: str
    page: int
    section: str
    image_type: str
    ocr_text: str
    image_path: str
    document_context: str = ""  # 额外 MD 上下文字段；空则不讲


def _json_line(s: str) -> str:
    """在 prompt 里安全嵌入多行/引号文字。"""
    return json.dumps(s, ensure_ascii=False)


def build_prompt(meta: FigureMeta) -> str:
    doc_block = ""
    if (meta.document_context or "").strip():
        doc_block = (
            "Document context from the Markdown (for disambiguation only; if it conflicts with "
            "the image, trust the image and OCR.):\n"
            f"{meta.document_context.strip()}\n\n"
        )
    return (
        "You are a technical diagram parser.\n"
        "Analyze the image and output ONLY one valid JSON object.\n"
        "No markdown, no explanation, no extra text.\n\n"
        "Rules:\n"
        "1) Follow the schema exactly.\n"
        '2) If any field is unclear, use "unknown" (English).\n'
        "3) Do not hallucinate.\n"
        "4) Keep output values in English unless copied from OCR text.\n"
        "5) confidence must be between 0.0 and 1.0.\n\n"
        "Schema:\n"
        "{\n"
        '  "image_id": "",\n'
        '  "doc_id": "",\n'
        '  "page": 0,\n'
        '  "section": "",\n'
        '  "image_type": "",\n'
        '  "title": "",\n'
        '  "summary": "",\n'
        '  "keywords": [],\n'
        '  "entities": [],\n'
        '  "retrieval_text": "",\n'
        '  "ocr_text": "",\n'
        '  "diagram_semantics": {\n'
        '    "participants": [],\n'
        '    "interactions": [\n'
        "      {\n"
        '        "order": 1,\n'
        '        "from": "",\n'
        '        "to": "",\n'
        '        "action": "",\n'
        '        "condition": ""\n'
        "      }\n"
        "    ],\n"
        '    "nodes": [],\n'
        '    "edges": [],\n'
        '    "components": [],\n'
        '    "relations": []\n'
        "  },\n"
        '  "uncertainties": [],\n'
        '  "confidence": 0.0,\n'
        '  "image_path": ""\n'
        "}\n\n"
        f"{doc_block}"
        "Input metadata:\n"
        f"- image_id: { _json_line(meta.image_id) }\n"
        f"- doc_id: { _json_line(meta.doc_id) }\n"
        f"- page: {meta.page}\n"
        f"- section: { _json_line(meta.section) }\n"
        f"- image_type: { _json_line(meta.image_type) }\n"
        f"- image_path: { _json_line(meta.image_path) }\n"
        f"- ocr_text: { _json_line(meta.ocr_text) }\n\n"
        "Output JSON now."
    )


def normalize_unknowns(data: dict) -> dict:
    defaults = {
        "image_id": "unknown",
        "doc_id": "unknown",
        "page": 0,
        "section": "unknown",
        "image_type": "unknown",
        "title": "unknown",
        "summary": "unknown",
        "keywords": [],
        "entities": [],
        "retrieval_text": "unknown",
        "ocr_text": "unknown",
        "diagram_semantics": {
            "participants": [],
            "interactions": [],
            "nodes": [],
            "edges": [],
            "components": [],
            "relations": [],
        },
        "uncertainties": [],
        "confidence": 0.0,
        "image_path": "unknown",
    }
    out = defaults.copy()
    out.update(data if isinstance(data, dict) else {})
    if not isinstance(out.get("keywords"), list):
        out["keywords"] = []
    if not isinstance(out.get("entities"), list):
        out["entities"] = []
    if not isinstance(out.get("uncertainties"), list):
        out["uncertainties"] = []
    if not isinstance(out.get("diagram_semantics"), dict):
        out["diagram_semantics"] = defaults["diagram_semantics"]  # type: ignore[assignment]
    for key in ("participants", "interactions", "nodes", "edges", "components", "relations"):
        if not isinstance(out["diagram_semantics"].get(key), list):
            out["diagram_semantics"][key] = []
    try:
        out["confidence"] = float(out.get("confidence", 0.0))
    except (TypeError, ValueError):
        out["confidence"] = 0.0
    out["confidence"] = max(0.0, min(1.0, out["confidence"]))
    return out


def _data_url_for_image(path: Path) -> str:
    b64 = base64.b64encode(path.read_bytes()).decode("utf-8")
    mime, _ = mimetypes.guess_type(str(path))
    if mime and mime.startswith("image/"):
        return f"data:{mime};base64,{b64}"
    if path.suffix.lower() in (".jpg", ".jpeg"):
        return f"data:image/jpeg;base64,{b64}"
    return f"data:image/png;base64,{b64}"


def run_figure_describe(
    image_path: Path,
    prompt: str,
    *,
    api_key: str,
    base_url: str,
    model: str,
) -> dict[str, Any]:
    client = OpenAI(api_key=api_key, base_url=base_url)
    data_url = _data_url_for_image(image_path)
    response = client.responses.create(
        model=model,
        input=[
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": prompt},
                    {"type": "input_image", "image_url": data_url},
                ],
            }
        ],
    )
    # 部分网关/代理直接返回 str；标准 Responses 为带 output_text 的对象
    if isinstance(response, str):
        raw = response.strip()
    else:
        raw = (getattr(response, "output_text", None) or "").strip()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Model output is not valid JSON: {exc}\nRaw output:\n{raw}"
        ) from exc
    return normalize_unknowns(parsed if isinstance(parsed, dict) else {})


def default_api_key(explicit: str) -> str:
    return explicit or os.environ.get("OPENAI_API_KEY", "")
