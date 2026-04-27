#!/usr/bin/env python3
"""
最小验证：用与 secrets.sh 相同的环境变量测「带图」能否走 Chat Completions。

用法（在 xtor-aui-spec-tools 目录）:
  set -a && source ./secrets.sh && set +a && python py/smoke_vision_api.py

依赖: requests（主 requirements 已有）; 若已装 openai 会额外试官方 SDK。

读环境:
  API_URL   须含 /v1，如 https://backend.intelalloc.com/v1
  API_KEY
  MODEL     如 gpt-5.4 或其它支持 vision 的模型名

说明: B5 当前 batch_describe 默认传 --base-url 为 **去掉 /v1 的 host**（与 describe_image 历史一致）；
     而 aidoc 其它步用的是带 /v1 的 API_URL。本脚本可看出「/v1 形态」对 chat+图是否可用。
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any

# 1x1 透明 PNG
TINY_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def _chat_completions_url(api_url: str) -> str:
    base = (api_url or "").strip().rstrip("/")
    if not base:
        return ""
    return f"{base}/chat/completions"


def try_requests_chat(api_url: str, api_key: str, model: str) -> tuple[bool, str]:
    import requests

    url = _chat_completions_url(api_url)
    if not url:
        return False, "API_URL 为空"
    body: dict[str, Any] = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "Reply with exactly: OK_VISION (one line, no other words).",
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{TINY_PNG_B64}",
                        },
                    },
                ],
            }
        ],
        "max_tokens": 32,
    }
    r = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=body,
        timeout=120,
    )
    text = r.text
    if r.status_code != 200:
        return False, f"HTTP {r.status_code}\n{text[:2000]}"
    try:
        data = r.json()
    except json.JSONDecodeError:
        return False, f"非 JSON: {text[:2000]}"
    choice0 = (data.get("choices") or [{}])[0]
    content = (choice0.get("message") or {}).get("content")
    if content is None:
        return False, f"无 message.content: {json.dumps(data, ensure_ascii=False)[:2000]}"
    return True, f"message.content: {str(content)[:500]}"


def try_openai_sdk_chat(api_url: str, api_key: str, model: str) -> tuple[bool, str]:
    try:
        from openai import OpenAI
    except ImportError:
        return False, "未安装 openai 包，跳过 SDK 路径"

    client = OpenAI(api_key=api_key, base_url=api_url.rstrip("/"))
    try:
        resp = client.chat.completions.create(
            model=model,
            max_tokens=32,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "Reply with exactly: OK_VISION (one line).",
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{TINY_PNG_B64}",
                            },
                        },
                    ],
                }
            ],
        )
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e!s}"

    c0 = resp.choices[0] if resp.choices else None
    msg = getattr(c0, "message", None) if c0 else None
    content = getattr(msg, "content", None) if msg else None
    if not content:
        return False, f"无 content: {resp!r}"
    return True, f"SDK message.content: {str(content)[:500]}"


def main() -> int:
    api_url = (os.environ.get("API_URL") or "").strip()
    api_key = (os.environ.get("API_KEY") or "").strip()
    model = (os.environ.get("MODEL") or "gpt-4o").strip()

    if not api_key:
        print("未设置 API_KEY。请先: set -a && source ./secrets.sh && set +a", file=sys.stderr)
        return 1
    if not api_url:
        print("未设置 API_URL（应含 /v1）。", file=sys.stderr)
        return 1
    if "/v1" not in api_url:
        print("提示: API_URL 建议以 /v1 结尾，与 aidoc 其它步一致；当前: " + api_url, file=sys.stderr)

    print("== 1) requests POST {API_URL}/chat/completions（最小 vision）==")
    ok, msg = try_requests_chat(api_url, api_key, model)
    print(("OK" if ok else "FAIL") + ":", msg)
    if not ok:
        return 1

    print()
    print("== 2) openai 官方 SDK: OpenAI(base_url=API_URL).chat.completions.create ==")
    ok2, msg2 = try_openai_sdk_chat(api_url, api_key, model)
    print(("OK" if ok2 else "FAIL") + ":", msg2)

    return 0 if ok2 else (0 if ok else 1)


if __name__ == "__main__":
    raise SystemExit(main())
