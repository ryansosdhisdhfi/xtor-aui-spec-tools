"""
VLM 技术图 -> 结构化 JSON：共用 build_prompt、normalize_unknowns、单次 API 调用。
供 describe_image_wsl.py 与 batch_describe.py 使用。
"""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import random
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

# 与 aidoc_llm.OpenAIClient 一致：requests POST + 429/5xx/连接类可重试
# （IntelAlloc 等网关常拦 OpenAI SDK 自带头，见 py/smoke_vision_api.py 路径 1 OK / 路径 2 blocked）
from aidoc_llm import (
    _openai_extract_assistant_text,
    _openai_max_retries,
    _openai_transient_http_status,
    _openai_transient_request_error,
)


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
        '  "figure_kind": "unknown",\n'
        '  "figure_kind_confidence": 0.0,\n'
        '  "extraction_profile": "default_v1",\n'
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
        '  "digital_ic_semantics": {\n'
        '    "signals": [\n'
        "      {\n"
        '        "name": "",\n'
        '        "type": "",\n'
        '        "producer": "unknown",\n'
        '        "consumer": "unknown",\n'
        '        "driver": "unknown",\n'
        '        "receiver": "unknown",\n'
        '        "active_level": "unknown",\n'
        '        "active_level_source": "unknown",\n'
        '        "active_level_confidence": 0.0,\n'
        '        "carried_by": [],\n'
        '        "source": "unknown"\n'
        "      }\n"
        "    ],\n"
        '    "interfaces": [\n'
        "      {\n"
        '        "name": "",\n'
        '        "from": "unknown",\n'
        '        "to": "unknown",\n'
        '        "signals": [],\n'
        '        "source": "unknown"\n'
        "      }\n"
        "    ],\n"
        '    "transactions": [\n'
        "      {\n"
        '        "order": 1,\n'
        '        "bus": "unknown",\n'
        '        "producer": "unknown",\n'
        '        "consumer": "unknown",\n'
        '        "operation": "unknown",\n'
        '        "target": "unknown",\n'
        '        "payload_or_purpose": "unknown",\n'
        '        "commit": "unknown",\n'
        '        "source": "unknown",\n'
        '        "evidence_text": "unknown"\n'
        "      }\n"
        "    ],\n"
        '    "timing_constraints": [\n'
        "      {\n"
        '        "name": "",\n'
        '        "start_event": "unknown",\n'
        '        "end_event": "unknown",\n'
        '        "min_latency": "unknown",\n'
        '        "max_latency": "unknown",\n'
        '        "cycles": "unknown",\n'
        '        "relation": "unknown",\n'
        '        "source": "unknown",\n'
        '        "evidence_text": "unknown"\n'
        "      }\n"
        "    ],\n"
        '    "phases": [\n'
        "      {\n"
        '        "order": 1,\n'
        '        "name": "",\n'
        '        "description": "",\n'
        '        "signals_involved": [],\n'
        '        "source": "unknown"\n'
        "      }\n"
        "    ],\n"
        '    "assumptions": [\n'
        "      {\n"
        '        "assumption": "",\n'
        '        "basis": "unknown",\n'
        '        "confidence": 0.0\n'
        "      }\n"
        "    ],\n"
        '    "uncertain_items": []\n'
        "  },\n"
        '  "uncertainties": [],\n'
        '  "confidence": 0.0,\n'
        '  "image_path": ""\n'
        "}\n\n"
        "Digital IC / protocol diagram extraction rules:\n"
        "- Always keep the legacy top-level fields useful; digital_ic_semantics is additive.\n"
        "- Use digital_ic_semantics for waveform, timing, message-bus, register, PHY/MAC, "
        "SerDes, clock/reset, protocol sequence, and hardware block/interface diagrams.\n"
        "- signals: list hardware signals, buses, ports, payloads, register fields, clocks, "
        "resets, valid/ready/status/control lines. Use producer/consumer or driver/receiver "
        "only when visible or strongly supported; otherwise use \"unknown\".\n"
        "- interfaces: list block-to-block or endpoint-to-endpoint interfaces and their signals.\n"
        "- transactions: list request/response/read/write/ack/update/message-bus operations in "
        "time order. Keep generic; do not create protocol-specific fields.\n"
        "- timing_constraints: extract explicit before/after/within/min/max/cycle/ns/us timing "
        "requirements only when visible in OCR/image/context.\n"
        "- phases: list visible phases, windows, states, intervals, or regions in order.\n"
        "- assumptions: record any inferred direction, active level, ownership, or protocol meaning.\n"
        "- For static block/interface diagrams: prioritize signals and interfaces; leave transactions "
        "empty unless the image explicitly shows an operation flow, message exchange, or ordered "
        "procedure.\n"
        "- For waveform/timing diagrams: prioritize signals, phases, and timing_constraints; only use "
        "transactions when the image shows a real causal exchange such as request/ack, write/response, "
        "or another explicit ordered operation.\n"
        "- For pure observational waveforms that show only signal ordering or state evolution (for example "
        "active-to-idle, idle-to-active, valid/idle transitions, alignment timing, or signal assertion/"
        "de-assertion timing), transactions should usually be empty. Express the behavior using phases and "
        "timing_constraints instead.\n"
        "- For waveform/timing diagrams, use timing_constraints for explicit visible ordering relations even "
        "when no numeric latency is shown, such as A before B, A aligns with B, A occurs after B, or A may "
        "remain asserted until B changes.\n"
        "- For waveform/timing diagrams: do NOT treat a signal simply asserting, deasserting, going idle, "
        "going active, toggling, or carrying data as a transaction by itself. Those belong in phases and/or "
        "timing_constraints unless a true protocol operation is explicitly shown.\n"
        "- For message-bus or framed timing diagrams: do NOT split one logical read/write/request/response "
        "transaction into multiple transactions just because command, address, and data occupy separate beats "
        "or cycles. Represent the overall operation as one transaction and use phases for the per-cycle framing.\n"
        "- For protocol/sequence/message-bus diagrams: prioritize ordered transactions and "
        "timing_constraints; use phases only for higher-level grouping when it helps summarize the "
        "sequence.\n"
        "- Each object should include source using one of: visible_image, ocr, caption, "
        "nearby_context, protocol_knowledge, inferred, unknown.\n"
        "- For names ending in #, _n, _b, or bar, infer active low only as a convention and record "
        "active_level_source plus an assumption unless the image explicitly states it.\n"
        "- do not invent timing values, cycle counts, signal directions, protocol ownership, or "
        "register names.\n"
        "- Do not treat compressed time markers (for example ~~ or broken axes) as clock stopped.\n"
        "- Do not treat a flat line as disabled unless the label/context says disabled, stopped, "
        "invalid, idle, or equivalent.\n"
        "- Do not put protocol knowledge into visible facts; put it in assumptions or source it as "
        "protocol_knowledge/inferred.\n\n"
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
        "figure_kind": "unknown",
        "figure_kind_confidence": 0.0,
        "extraction_profile": "default_v1",
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
        "digital_ic_semantics": {
            "signals": [],
            "interfaces": [],
            "transactions": [],
            "timing_constraints": [],
            "phases": [],
            "assumptions": [],
            "uncertain_items": [],
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
    if not isinstance(out.get("digital_ic_semantics"), dict):
        out["digital_ic_semantics"] = defaults["digital_ic_semantics"]  # type: ignore[assignment]
    for key in (
        "signals",
        "interfaces",
        "transactions",
        "timing_constraints",
        "phases",
        "assumptions",
        "uncertain_items",
    ):
        if not isinstance(out["digital_ic_semantics"].get(key), list):
            out["digital_ic_semantics"][key] = []
    try:
        out["confidence"] = float(out.get("confidence", 0.0))
    except (TypeError, ValueError):
        out["confidence"] = 0.0
    out["confidence"] = max(0.0, min(1.0, out["confidence"]))
    try:
        out["figure_kind_confidence"] = float(out.get("figure_kind_confidence", 0.0))
    except (TypeError, ValueError):
        out["figure_kind_confidence"] = 0.0
    out["figure_kind_confidence"] = max(0.0, min(1.0, out["figure_kind_confidence"]))
    out["figure_kind"] = str(out.get("figure_kind") or "unknown").strip() or "unknown"
    out["extraction_profile"] = str(out.get("extraction_profile") or "default_v1").strip() or "default_v1"
    out = _normalize_digital_ic_semantics(out)
    return out


def _looks_like_timing_diagram(image_type: Any) -> bool:
    text = str(image_type or "").strip().lower()
    return "timing" in text or "waveform" in text


def _is_waveform_pseudo_transaction(txn: Any) -> bool:
    if not isinstance(txn, dict):
        return False
    bus = str(txn.get("bus") or "").strip().lower()
    operation = str(txn.get("operation") or "").strip().lower()
    producer = str(txn.get("producer") or "").strip().lower()
    payload = str(txn.get("payload_or_purpose") or "").strip().lower()
    commit = str(txn.get("commit") or "").strip().lower()
    evidence = str(txn.get("evidence_text") or "").strip().lower()

    generic_waveform_buses = {
        "",
        "unknown",
        "receive interface",
        "transmit interface",
        "rx interface",
        "tx interface",
    }

    if commit not in ("", "unknown"):
        return False

    waveform_ops = {
        "assert",
        "deassert",
        "transition to electrical idle",
        "data reception ends",
        "active receive data ends",
        "receive data active",
        "receive data then enter idle",
        "enter idle",
        "exit idle",
        "transition to idle",
    }
    waveform_markers = (
        "waveform",
        "goes flat",
        "rises",
        "falls",
        "stays high",
        "stops showing data",
    )
    waveform_signal_prefixes = ("rx", "tx", "clk", "pclk")

    if bus in generic_waveform_buses and operation in waveform_ops:
        return True
    if bus in generic_waveform_buses and producer.startswith(waveform_signal_prefixes) and any(marker in evidence for marker in waveform_markers):
        return True
    if bus in generic_waveform_buses and payload in (
        "transition to idle",
        "transition from received data to electrical idle",
        "indicate idle",
        "indicate received data no longer valid",
        "end of received data activity",
        "data",
    ):
        return True
    return False


def _is_framing_substep_transaction(txn: Any) -> bool:
    if not isinstance(txn, dict):
        return False
    operation = str(txn.get("operation") or "").strip().lower()
    payload = str(txn.get("payload_or_purpose") or "").strip().lower()
    evidence = str(txn.get("evidence_text") or "").strip().lower()
    commit = str(txn.get("commit") or "").strip().lower()

    if commit not in ("", "unknown"):
        return False

    framing_markers = (
        "phase 1",
        "phase 2",
        "phase 3",
        "cmd[3:0]",
        "addr[11:8]",
        "addr[7:0]",
        "data[7:0]",
        "beat",
        "cycle",
    )
    framing_ops = {"write", "read", "request", "response"}

    framing_text = " ".join(x for x in (payload, evidence) if x)
    if operation in framing_ops and any(marker in framing_text for marker in framing_markers):
        return True
    return False


def _prune_framing_substeps(txns: list[Any]) -> list[Any]:
    if len(txns) < 2:
        return txns
    kept: list[Any] = []
    for txn in txns:
        if _is_framing_substep_transaction(txn):
            continue
        kept.append(txn)
    return kept


def _normalize_digital_ic_semantics(out: dict[str, Any]) -> dict[str, Any]:
    dic = out.get("digital_ic_semantics")
    if not isinstance(dic, dict):
        return out
    txns = dic.get("transactions")
    if isinstance(txns, list) and _looks_like_timing_diagram(out.get("image_type")):
        txns = [txn for txn in txns if not _is_waveform_pseudo_transaction(txn)]
        txns = _prune_framing_substeps(txns)
        dic["transactions"] = txns
    return out


def _data_url_for_image(path: Path) -> str:
    b64 = base64.b64encode(path.read_bytes()).decode("utf-8")
    mime, _ = mimetypes.guess_type(str(path))
    if mime and mime.startswith("image/"):
        return f"data:{mime};base64,{b64}"
    if path.suffix.lower() in (".jpg", ".jpeg"):
        return f"data:image/jpeg;base64,{b64}"
    return f"data:image/png;base64,{b64}"


def _vlm_timeout_sec() -> float:
    """单次 VLM 请求读超时（秒）；避免网关无响应时一直挂住。可调 AIDOC_VLM_TIMEOUT_SEC。"""
    raw = (os.environ.get("AIDOC_VLM_TIMEOUT_SEC") or "600").strip()
    try:
        v = float(raw)
    except ValueError:
        return 600.0
    return max(30.0, min(v, 7200.0))


def _normalize_openai_base_url(base_url: str) -> str:
    """OpenAI 兼容客户端的 base 须为 …/v1，与 aidoc 其它步一致。"""
    bu = (base_url or "").strip().rstrip("/")
    if not bu:
        return bu
    if bu.endswith("/v1"):
        return bu
    return f"{bu}/v1"


def _strip_json_fences(text: str) -> str:
    t = (text or "").strip()
    if not t.startswith("```"):
        return t
    lines = t.split("\n")
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() in ("```", "```json"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _vlm_should_retry(exc: BaseException) -> bool:
    """与 aidoc_llm 瞬态错误判定一致；仅对可恢复错误重试。"""
    return _openai_transient_request_error(exc)


def _vlm_retry_delay_s(attempt: int, exc: BaseException | requests.Response) -> float:
    """指数退避；502/网关说明里常建议至少 60s；尊重 Retry-After 头（秒）。"""
    base = min(120.0, (1.5**attempt) + random.uniform(0.0, 0.5))
    resp = exc if isinstance(exc, requests.Response) else getattr(exc, "response", None)
    if resp is not None:
        ra = resp.headers.get("retry-after") or resp.headers.get("Retry-After")
        if ra:
            try:
                return min(300.0, float(ra))
            except ValueError:
                pass
        if getattr(resp, "status_code", None) == 502:
            base = max(base, 60.0)
    return base


def _parse_model_json_text(raw: str) -> dict[str, Any]:
    """从 assistant 正文中解出 JSON 对象；容忍 ``` 围栏与前后说明。"""
    t = _strip_json_fences(raw)
    try:
        parsed: Any = json.loads(t)
    except json.JSONDecodeError:
        a = t.find("{")
        b = t.rfind("}")
        if a < 0 or b <= a:
            raise
        parsed = json.loads(t[a : b + 1])
    if not isinstance(parsed, dict):
        raise ValueError("JSON root is not an object")
    return parsed


def run_figure_describe(
    image_path: Path,
    prompt: str,
    *,
    api_key: str,
    base_url: str,
    model: str,
) -> dict[str, Any]:
    # 与 a2–a4 / aidoc_llm 一致：requests POST chat/completions + vision
    bu = _normalize_openai_base_url(base_url)
    if not bu:
        raise ValueError("base_url 为空；请传与 API_URL 相同的根地址（含 /v1）")
    url = f"{bu}/chat/completions"
    data_url = _data_url_for_image(image_path)
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {
                    "type": "image_url",
                    "image_url": {"url": data_url},
                },
            ],
        }
    ]
    body = {"model": model, "max_tokens": 4096, "messages": messages}

    max_retries = _openai_max_retries()
    attempt = 0
    raw = ""
    while True:
        try:
            resp = requests.post(
                url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
                timeout=_vlm_timeout_sec(),
            )
            if resp.status_code != 200:
                if _openai_transient_http_status(resp.status_code) and attempt < max_retries:
                    delay = _vlm_retry_delay_s(attempt, resp)
                    print(
                        f"[batch_describe] VLM 可重试 HTTP {resp.status_code}，{delay:.1f}s 后重试 "
                        f"({attempt + 1}/{max_retries})...",
                        file=sys.stderr,
                    )
                    time.sleep(delay)
                    attempt += 1
                    continue
                resp.raise_for_status()
            data = resp.json()
            if isinstance(data, dict) and data.get("error"):
                err = data["error"]
                msg = err.get("message") if isinstance(err, dict) else str(err)
                raise RuntimeError(f"OpenAI API 业务错误: {msg}")
            choices = data.get("choices") if isinstance(data, dict) else None
            if not choices or not isinstance(choices[0], dict):
                raise RuntimeError("Model returned no choices\nRaw: " + resp.text[:8000])
            raw = _openai_extract_assistant_text(choices[0]).strip()
            if not raw:
                raise RuntimeError("Model returned empty message.content\nRaw: " + resp.text[:8000])
            break
        except requests.RequestException as e:
            if _vlm_should_retry(e) and attempt < max_retries:
                delay = _vlm_retry_delay_s(attempt, e)
                sc = getattr(getattr(e, "response", None), "status_code", None) or type(e).__name__
                print(
                    f"[batch_describe] VLM 可重试错误 ({sc})，{delay:.1f}s 后重试 "
                    f"({attempt + 1}/{max_retries})...",
                    file=sys.stderr,
                )
                time.sleep(delay)
                attempt += 1
                continue
            raise
    try:
        parsed = _parse_model_json_text(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        raise RuntimeError(
            f"Model output is not valid JSON: {exc}\nRaw output:\n{raw[:8000]}"
        ) from exc
    return normalize_unknowns(parsed)


def default_api_key(explicit: str) -> str:
    return explicit or os.environ.get("OPENAI_API_KEY", "")
