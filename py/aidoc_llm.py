#!/usr/bin/env python3
"""
aidoc_llm.py - 统一 LLM 客户端
================================

为 aidoc 工具链提供统一的 LLM 调用接口，支持两种后端：
  - Ollama（本地部署，默认）
  - OpenAI API（兼容所有 OpenAI 协议的服务）

配置优先级: CLI 参数 > aidoc.conf 配置文件 > 默认值

典型用法：
    # 从 CLI 参数创建客户端
    client = LLMClient.from_config(api="ollama", model="qwen3:8b")
    response = client.generate("你好")

    # 从配置文件 + argparse 创建
    add_llm_args(parser)
    args = parser.parse_args()
    client = create_llm_client(args)
"""

import configparser
import json
import os
import random
import re
import sys
import time
from pathlib import Path
from typing import Any, Optional

import requests


# =============================================================================
# 默认配置
# =============================================================================

DEFAULT_API = "ollama"
DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_OPENAI_URL = "https://api.openai.com/v1"
DEFAULT_MODEL_OLLAMA = "qwen3:8b"
DEFAULT_MODEL_OPENAI = "gpt-4o-mini"
DEFAULT_TEMPERATURE = 0.3
DEFAULT_TIMEOUT = 120
# OpenAI 兼容接口：遇 429 / 5xx / 524 等可重试错误时自动重试（见 AIDOC_LLM_MAX_RETRIES）
DEFAULT_LLM_MAX_RETRIES = 8

# 配置文件搜索路径
CONFIG_SEARCH_PATHS = [
    Path("./aidoc.conf"),
    Path.home() / ".config" / "aidoc" / "aidoc.conf",
]


# =============================================================================
# 配置文件加载
# =============================================================================

def load_config() -> dict:
    """
    加载 aidoc.conf 配置文件。

    搜索顺序：
      1. 当前目录 ./aidoc.conf
      2. 用户配置 ~/.config/aidoc/aidoc.conf

    Returns:
        配置字典，未找到配置文件时返回空字典
    """
    for path in CONFIG_SEARCH_PATHS:
        if path.exists():
            config = configparser.ConfigParser()
            config.read(path, encoding="utf-8")
            if "llm" in config:
                return dict(config["llm"])
    return {}


def _get_config_value(key: str, cli_value, config: dict, default):
    """三级优先级取值: CLI > 配置文件 > 默认值"""
    if cli_value is not None:
        return cli_value
    if key in config and config[key]:
        return config[key]
    return default


# =============================================================================
# LLM 客户端基类
# =============================================================================

class LLMClient:
    """
    LLM 客户端抽象基类。

    所有后端（Ollama、OpenAI）实现统一的 generate() 接口，
    上层工具无需关心具体后端差异。
    """

    def __init__(self, model: str, base_url: str, temperature: float, timeout: int):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.temperature = temperature
        self.timeout = timeout
        self.available = False

    def generate(self, prompt: str, system: str = "", temperature: Optional[float] = None,
                 max_tokens: int = 2048) -> str:
        """
        调用 LLM 生成文本。

        Args:
            prompt:      用户提示词
            system:      系统提示词（可选）
            temperature: 生成温度，None 则使用默认值
            max_tokens:  最大生成 token 数

        Returns:
            生成的文本，调用失败返回空字符串
        """
        raise NotImplementedError

    def check_connection(self) -> bool:
        """检查后端服务是否可用，更新 self.available 并返回结果"""
        raise NotImplementedError

    @property
    def backend_name(self) -> str:
        """后端名称，用于日志输出"""
        raise NotImplementedError

    def __repr__(self):
        status = "可用" if self.available else "不可用"
        return f"<{self.backend_name} model={self.model} {status}>"


# =============================================================================
# Ollama 后端
# =============================================================================

class OllamaClient(LLMClient):
    """
    Ollama 后端客户端。

    通过 Ollama REST API (/api/generate) 调用本地部署的模型。
    """

    def __init__(self, model: str = DEFAULT_MODEL_OLLAMA,
                 base_url: str = DEFAULT_OLLAMA_URL,
                 temperature: float = DEFAULT_TEMPERATURE,
                 timeout: int = DEFAULT_TIMEOUT):
        super().__init__(model, base_url, temperature, timeout)
        self.check_connection()

    @property
    def backend_name(self) -> str:
        return "Ollama"

    def check_connection(self) -> bool:
        """检查 Ollama 服务连接状态和模型可用性"""
        try:
            resp = requests.get(f"{self.base_url}/api/tags", timeout=5)
            resp.raise_for_status()
            models = [m["name"] for m in resp.json().get("models", [])]

            # 检查目标模型是否已拉取
            model_base = self.model.split(":")[0]
            self.available = any(model_base in m for m in models)

            if not self.available:
                print(f"警告: Ollama 模型 {self.model} 未找到")
                print(f"  可用模型: {', '.join(models[:5])}")
                print(f"  请运行: ollama pull {self.model}")
        except requests.RequestException as e:
            print(f"警告: 无法连接 Ollama ({self.base_url}): {e}")
            self.available = False

        return self.available

    def generate(self, prompt: str, system: str = "", temperature: Optional[float] = None,
                 max_tokens: int = 2048) -> str:
        if not self.available:
            return ""

        temp = temperature if temperature is not None else self.temperature
        try:
            resp = requests.post(
                f"{self.base_url}/api/generate",
                json={
                    "model": self.model,
                    "prompt": prompt,
                    "system": system,
                    "stream": False,
                    "options": {
                        "temperature": temp,
                        "num_predict": max_tokens,
                    },
                },
                timeout=self.timeout,
            )
            resp.raise_for_status()
            return resp.json().get("response", "").strip()
        except requests.RequestException as e:
            print(f"Ollama 调用失败: {e}")
            return ""


# =============================================================================
# OpenAI 后端
# =============================================================================


def _openai_max_retries() -> int:
    raw = os.environ.get("AIDOC_LLM_MAX_RETRIES", str(DEFAULT_LLM_MAX_RETRIES)).strip()
    try:
        n = int(raw)
    except ValueError:
        return DEFAULT_LLM_MAX_RETRIES
    return max(0, min(n, 30))


def _openai_transient_http_status(status: int) -> bool:
    """网关超时(524)、限流(429)与服务端5xx 常具瞬时性，适合重试。"""
    if status == 429 or status == 408:
        return True
    return 500 <= status < 600


def _openai_transient_request_error(exc: BaseException) -> bool:
    if isinstance(exc, (requests.Timeout, requests.ConnectTimeout, requests.ReadTimeout)):
        return True
    if isinstance(exc, requests.ConnectionError):
        return True
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        return _openai_transient_http_status(exc.response.status_code)
    return False


def _openai_extract_assistant_text(choice: dict[str, Any]) -> str:
    """
    从 chat/completions 单条 choice 中取出助手回复文本。
    兼容标准 OpenAI（message.content）、部分中转（message.text / content 为 list 等）。
    """
    if not isinstance(choice, dict):
        return ""
    msg = choice.get("message")
    if not isinstance(msg, dict):
        # 少数实现把文本放在 choice 顶层
        t = choice.get("text")
        return str(t).strip() if t is not None else ""

    raw = msg.get("content")
    if raw is not None:
        if isinstance(raw, str):
            return raw.strip()
        if isinstance(raw, list):
            parts: list[str] = []
            for p in raw:
                if isinstance(p, dict):
                    if p.get("type") == "text" and p.get("text"):
                        parts.append(str(p["text"]))
                    elif isinstance(p.get("text"), str):
                        parts.append(p["text"])
            return "\n".join(parts).strip()

    alt = msg.get("text")
    if alt is not None and str(alt).strip():
        return str(alt).strip()
    rsn = msg.get("reasoning_content")
    if rsn is not None and str(rsn).strip():
        return str(rsn).strip()
    return ""


class OpenAIClient(LLMClient):
    """
    OpenAI API 后端客户端。

    兼容所有遵循 OpenAI Chat Completions 协议的服务，包括：
      - OpenAI 官方 API
      - Azure OpenAI
      - 第三方兼容服务（如 vLLM、LiteLLM、OpenRouter 等）
    """

    def __init__(self, model: str = DEFAULT_MODEL_OPENAI,
                 base_url: str = DEFAULT_OPENAI_URL,
                 api_key: str = "",
                 temperature: float = DEFAULT_TEMPERATURE,
                 timeout: int = DEFAULT_TIMEOUT):
        super().__init__(model, base_url, temperature, timeout)
        # API Key: 参数 > 环境变量
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self.check_connection()

    @property
    def backend_name(self) -> str:
        return "OpenAI"

    def check_connection(self) -> bool:
        """检查 API Key 是否配置，并尝试验证连接"""
        if not self.api_key:
            print("警告: 未设置 OpenAI API Key")
            print("  请在 aidoc.conf 中设置 api_key，或设置环境变量 OPENAI_API_KEY")
            self.available = False
            return False

        # HTTP 头须 latin-1；中文占位/全角符会在 urllib 里以 UnicodeEncodeError 失败
        try:
            self.api_key.encode("latin-1")
        except UnicodeEncodeError:
            print(
                "错误: API_KEY 含非 ASCII 字符，无法作为 HTTP Authorization 发送。\n"
                "  请检查本仓 **secrets.sh**（不是仅改 secrets.sh.example）中 export API_KEY=…\n"
                "  须为英数字符的密钥，勿用中文占位（如「在此填写」）。"
            )
            self.available = False
            return False

        try:
            # 尝试列出模型来验证连接（轻量级请求）
            resp = requests.get(
                f"{self.base_url}/models",
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=10,
            )
            self.available = resp.status_code == 200
            if not self.available:
                print(f"警告: OpenAI API 连接失败 (HTTP {resp.status_code})")
        except requests.RequestException as e:
            print(f"警告: 无法连接 OpenAI API ({self.base_url}): {e}")
            self.available = False

        return self.available

    def generate(self, prompt: str, system: str = "", temperature: Optional[float] = None,
                 max_tokens: int = 2048) -> str:
        if not self.available:
            return ""

        temp = temperature if temperature is not None else self.temperature
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        max_retries = _openai_max_retries()
        attempt = 0
        while True:
            try:
                resp = requests.post(
                    f"{self.base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self.model,
                        "messages": messages,
                        "temperature": temp,
                        "max_tokens": max_tokens,
                    },
                    timeout=self.timeout,
                )
                sc = resp.status_code
                if sc != 200:
                    if _openai_transient_http_status(sc) and attempt < max_retries:
                        delay = min(
                            60.0,
                            (1.5**attempt) + random.uniform(0.0, 0.5),
                        )
                        print(
                            f"OpenAI API HTTP {sc}，{delay:.1f}s 后重试 ({attempt + 1}/{max_retries})...",
                            file=sys.stderr,
                        )
                        time.sleep(delay)
                        attempt += 1
                        continue
                    resp.raise_for_status()
                data = resp.json()
                choices = data.get("choices", []) if isinstance(data, dict) else []
                if not isinstance(data, dict):
                    return ""
                if data.get("error"):
                    print(f"OpenAI API 业务错误: {data.get('error')}", file=sys.stderr)
                    return ""
                if choices and isinstance(choices[0], dict):
                    c0 = choices[0]
                    text = _openai_extract_assistant_text(c0)
                    if not text:
                        msg = c0.get("message") if isinstance(c0, dict) else None
                        fr = c0.get("finish_reason") if isinstance(c0, dict) else None
                        if isinstance(msg, dict) and msg.get("refusal"):
                            print(f"OpenAI 模型拒答: {msg.get('refusal')[:800]}", file=sys.stderr)
                        elif os.environ.get("AIDOC_DEBUG_LLM"):
                            print(
                                "[aidoc_llm] 助手内容为空，完整 JSON（截断 8000 字）:\n"
                                + json.dumps(data, ensure_ascii=False)[:8000],
                                file=sys.stderr,
                            )
                        else:
                            print(
                                f"[aidoc_llm] 助手内容为空: finish_reason={fr!r}, model={self.model!r}。"
                                " 请核对该网关是否支持此模型/是否有额度。"
                                " 排障可: export AIDOC_DEBUG_LLM=1 后重试以打印完整响应。",
                                file=sys.stderr,
                            )
                    return text
                return ""
            except ValueError as e:
                # resp.json() 等对非预期 JSON
                print(f"OpenAI API 调用失败: {e}")
                return ""
            except requests.RequestException as e:
                if attempt < max_retries and _openai_transient_request_error(e):
                    delay = min(60.0, (1.5**attempt) + random.uniform(0.0, 0.5))
                    err_tag = str(e)[:120]
                    print(
                        f"OpenAI API 可重试: {err_tag}，{delay:.1f}s 后重试 ({attempt + 1}/{max_retries})...",
                        file=sys.stderr,
                    )
                    time.sleep(delay)
                    attempt += 1
                    continue
                print(f"OpenAI API 调用失败: {e}")
                return ""


# =============================================================================
# 工厂函数与 CLI 集成
# =============================================================================

def create_llm_client_from_config(
    api: Optional[str] = None,
    model: Optional[str] = None,
    api_url: Optional[str] = None,
    api_key: Optional[str] = None,
    temperature: Optional[float] = None,
    timeout: Optional[int] = None,
) -> LLMClient:
    """
    根据配置创建 LLM 客户端实例。

    三级优先级: 函数参数(CLI) > aidoc.conf > 默认值

    Args:
        api:         后端类型 ("ollama" / "openai")
        model:       模型名称
        api_url:     API 地址
        api_key:     API Key（仅 openai）
        temperature: 生成温度
        timeout:     请求超时（秒）

    Returns:
        LLMClient 实例（OllamaClient 或 OpenAIClient）
    """
    config = load_config()

    api = _get_config_value("api", api, config, DEFAULT_API)
    temperature_val = float(_get_config_value("temperature", temperature, config, DEFAULT_TEMPERATURE))
    timeout_val = int(_get_config_value("timeout", timeout, config, DEFAULT_TIMEOUT))

    if api == "openai":
        model_val = _get_config_value("model", model, config, DEFAULT_MODEL_OPENAI)
        url_val = _get_config_value("api_url", api_url, config, DEFAULT_OPENAI_URL)
        key_val = _get_config_value("api_key", api_key, config, "")
        return OpenAIClient(
            model=model_val, base_url=url_val, api_key=key_val,
            temperature=temperature_val, timeout=timeout_val,
        )
    else:
        model_val = _get_config_value("model", model, config, DEFAULT_MODEL_OLLAMA)
        url_val = _get_config_value("api_url", api_url, config, DEFAULT_OLLAMA_URL)
        return OllamaClient(
            model=model_val, base_url=url_val,
            temperature=temperature_val, timeout=timeout_val,
        )


def add_llm_args(parser, default_model: Optional[str] = None):
    """
    为 argparse.ArgumentParser 添加 LLM 相关公共参数。

    添加的参数：
      --api          后端类型 (ollama/openai)
      --model        模型名称
      --api-url      API 地址
      --api-key      API Key
      --no-llm       禁用 LLM

    Args:
        parser:        argparse.ArgumentParser 实例
        default_model: 该工具的默认模型（None 则从配置文件读取）
    """
    llm_group = parser.add_argument_group("LLM 配置（覆盖 aidoc.conf）")
    llm_group.add_argument(
        "--api", choices=["ollama", "openai"], default=None,
        help="LLM 后端 (默认: ollama)")
    llm_group.add_argument(
        "--model", default=default_model,
        help="模型名称 (Ollama: qwen3:8b / OpenAI: gpt-4o-mini)")
    llm_group.add_argument(
        "--api-url", default=None,
        help="API 地址 (Ollama: http://localhost:11434)")
    llm_group.add_argument(
        "--api-key", default=None,
        help="OpenAI API Key (也可设置 OPENAI_API_KEY 环境变量)")
    llm_group.add_argument(
        "--no-llm", action="store_true",
        help="禁用 LLM，仅使用规则引擎")


def create_llm_client(args) -> Optional[LLMClient]:
    """
    从 argparse 解析结果创建 LLM 客户端。

    如果 args.no_llm 为 True，返回 None。

    Args:
        args: argparse.parse_args() 的返回值（需包含 add_llm_args 添加的参数）

    Returns:
        LLMClient 实例，或 None（禁用 LLM 时）
    """
    if getattr(args, "no_llm", False):
        return None

    return create_llm_client_from_config(
        api=getattr(args, "api", None),
        model=getattr(args, "model", None),
        api_url=getattr(args, "api_url", None),
        api_key=getattr(args, "api_key", None),
    )


# =============================================================================
# 便捷函数
# =============================================================================

def _strip_markdown_fences(text: str) -> str:
    t = text.strip()
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", t, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return t


def _first_balanced_brace_object(s: str) -> Optional[str]:
    """自第一个 { 起扫描到成对 }，双引号字符串内忽略 { }（含转义）。"""
    i = s.find("{")
    if i < 0:
        return None
    depth = 0
    in_str = False
    esc = False
    for j in range(i, len(s)):
        c = s[j]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return s[i : j + 1]
    return None


def extract_json(text: str) -> Optional[dict]:
    """
    从 LLM 响应文本中提取第一个 JSON 对象。

    兼容：整段 JSON、```json 围栏、JSON 前/后带说明、含嵌套与 keywords 数组。
    """
    if not text or not str(text).strip():
        return None
    t = _strip_markdown_fences(str(text))
    t = t.strip()
    for candidate in (t, _first_balanced_brace_object(t) or ""):
        if not candidate:
            continue
        try:
            out = json.loads(candidate)
            if isinstance(out, dict):
                return out
        except (json.JSONDecodeError, ValueError):
            pass
    return None
