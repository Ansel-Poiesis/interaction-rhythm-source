"""统一 AI provider 配置：不再提供本地模型，默认云端 API，Key 可经设置页保存。"""
from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit


DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"
DEFAULT_DEEPSEEK_MODEL = "deepseek-chat"
DEFAULT_MIMO_BASE_URL = "https://token-plan-cn.xiaomimimo.com/v1"
DEFAULT_MIMO_MODEL = "mimo-v2.5-pro"
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
DEFAULT_PROVIDER = "deepseek"


def _first(*names: str) -> str:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return ""


def _saved_settings() -> dict:
    """读取非敏感的 AI 选项；API key 经设置页保存后也在此读取。"""
    try:
        from settings import get_settings

        value = get_settings()
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


@dataclass(frozen=True)
class AIConfig:
    provider: str
    base_url: str
    model: str
    api_key: str
    timeout: int
    cloud_enabled: bool
    thinking_enabled: bool


def _redact_url_fallback(raw: str) -> str:
    safe = str(raw or "").split("?", 1)[0].split("#", 1)[0]
    if "://" in safe:
        prefix, rest = safe.split("://", 1)
        authority, separator, path = rest.partition("/")
        authority = authority.rsplit("@", 1)[-1]
        return f"{prefix}://{authority}" + (f"/{path}" if separator else "")
    authority, separator, path = safe.partition("/")
    authority = authority.rsplit("@", 1)[-1]
    return authority + (f"/{path}" if separator else "")


def public_base_url(value: str) -> str:
    """用于 UI/API 摘要的地址，移除凭据、查询参数和 fragment。"""
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parts = urlsplit(raw)
        if not parts.scheme or not parts.hostname:
            return _redact_url_fallback(raw)
        hostname = parts.hostname
        if ":" in hostname and not hostname.startswith("["):
            hostname = f"[{hostname}]"
        netloc = hostname
        try:
            port = parts.port
        except ValueError:
            port = None
        if port:
            netloc += f":{port}"
        return urlunsplit((parts.scheme, netloc, parts.path.rstrip("/"), "", ""))
    except ValueError:
        return _redact_url_fallback(raw)


def load_ai_config() -> AIConfig:
    saved = _saved_settings()
    requested_env = _first("INTERACTION_RHYTHM_AI_PROVIDER", "KOUXIAN_AI_PROVIDER").lower()
    requested = requested_env or str(saved.get("ai_provider") or DEFAULT_PROVIDER).strip().lower()
    aliases = {"compatible": "openai"}
    provider = aliases.get(requested, requested) if requested else DEFAULT_PROVIDER
    # 本地模型已弃用：任何 ollama/本地选择都归一化为默认云端服务。
    if provider in {"ollama", "local", "gemma"}:
        provider = DEFAULT_PROVIDER
    if provider not in {"deepseek", "mimo", "openai"}:
        provider = DEFAULT_PROVIDER

    generic_key = (
        _first("INTERACTION_RHYTHM_AI_API_KEY", "KOUXIAN_AI_API_KEY")
        or str(saved.get("ai_api_key") or "").strip()
    )
    timeout_text = _first(
        "INTERACTION_RHYTHM_AI_TIMEOUT", "KOUXIAN_AI_TIMEOUT", "MIMO_TIMEOUT"
    ) or "60"
    try:
        timeout = max(5, min(int(timeout_text), 180))
    except ValueError:
        timeout = 60
    thinking_enabled = bool(saved.get("ai_thinking_enabled"))

    if provider == "deepseek":
        api_key = generic_key or _first("DEEPSEEK_API_KEY")
        base_url = _first(
            "INTERACTION_RHYTHM_AI_BASE_URL", "DEEPSEEK_BASE_URL"
        ) or str(saved.get("ai_base_url") or "").strip() or DEFAULT_DEEPSEEK_BASE_URL
        model = _first(
            "INTERACTION_RHYTHM_AI_MODEL", "DEEPSEEK_MODEL", "KOUXIAN_AI_MODEL"
        ) or str(saved.get("ai_model") or "").strip() or DEFAULT_DEEPSEEK_MODEL
    elif provider == "mimo":
        api_key = generic_key or _first("MIMO_API_KEY")
        base_url = _first(
            "INTERACTION_RHYTHM_AI_BASE_URL", "MIMO_BASE_URL"
        ) or str(saved.get("ai_base_url") or "").strip() or DEFAULT_MIMO_BASE_URL
        model = _first(
            "INTERACTION_RHYTHM_AI_MODEL", "MIMO_MODEL"
        ) or str(saved.get("ai_model") or "").strip() or DEFAULT_MIMO_MODEL
    else:
        api_key = generic_key or _first("OPENAI_API_KEY")
        base_url = (
            _first("INTERACTION_RHYTHM_AI_BASE_URL", "KOUXIAN_AI_BASE_URL")
            or str(saved.get("ai_base_url") or "").strip()
            or DEFAULT_OPENAI_BASE_URL
        )
        model = (
            _first("INTERACTION_RHYTHM_AI_MODEL", "KOUXIAN_AI_MODEL")
            or str(saved.get("ai_model") or "").strip()
            or DEFAULT_OPENAI_MODEL
        )

    # 填写 Key 即视为显式同意使用云端 AI（原“允许使用云端 AI”开关已并入此语义）。
    return AIConfig(
        provider=provider,
        base_url=base_url.rstrip("/"),
        model=model,
        api_key=api_key,
        timeout=timeout,
        cloud_enabled=bool(api_key),
        thinking_enabled=thinking_enabled,
    )
