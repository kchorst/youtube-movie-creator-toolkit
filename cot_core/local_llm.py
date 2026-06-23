"""Provider-neutral Local LLM discovery and OpenAI-compatible helpers.

Supports llama.cpp/llama-server, LM Studio, Ollama, and custom endpoints.
The toolkit treats local AI as optional: if no endpoint is available, metadata
workflows should fall back to manual editing rather than blocking the app.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Iterable, Optional

DEFAULT_CHAT_PATH = "/v1/chat/completions"
DEFAULT_MODELS_PATH = "/v1/models"

COMMON_ENDPOINTS = [
    ("llama-server", "http://127.0.0.1:8080/v1/chat/completions"),
    ("LM Studio", "http://127.0.0.1:1234/v1/chat/completions"),
    ("Ollama", "http://127.0.0.1:11434/v1/chat/completions"),
]


@dataclass(frozen=True)
class LocalLLMStatus:
    ok: bool
    provider: str = ""
    base_url: str = ""
    chat_url: str = ""
    models: tuple[str, ...] = ()
    error: str = ""


def normalize_base_url(url: str) -> str:
    """Return the base URL without /v1/chat/completions or /v1/models."""
    u = (url or "").strip().rstrip("/")
    for suffix in ("/v1/chat/completions", "/chat/completions", "/v1/models", "/models"):
        if u.endswith(suffix):
            u = u[: -len(suffix)]
            break
    return u.rstrip("/")


def chat_url_from_base(url: str) -> str:
    base = normalize_base_url(url)
    if not base:
        return ""
    return base + DEFAULT_CHAT_PATH


def models_url_from_base(url: str) -> str:
    base = normalize_base_url(url)
    if not base:
        return ""
    return base + DEFAULT_MODELS_PATH


def _get_json(url: str, *, timeout: float = 4.0) -> dict:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8", errors="replace")
    data = json.loads(body or "{}")
    return data if isinstance(data, dict) else {}


def get_models(endpoint: str, *, timeout: float = 4.0) -> list[str]:
    """Return OpenAI-compatible model IDs from a local endpoint."""
    models_url = models_url_from_base(endpoint)
    if not models_url:
        return []
    data = _get_json(models_url, timeout=timeout)
    items = data.get("data", [])
    models: list[str] = []
    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict) and item.get("id"):
                models.append(str(item["id"]))
            elif isinstance(item, str):
                models.append(item)
    return models


def check_endpoint(endpoint: str, *, provider: str = "Local LLM", timeout: float = 4.0) -> LocalLLMStatus:
    base = normalize_base_url(endpoint)
    chat_url = chat_url_from_base(endpoint)
    if not base:
        return LocalLLMStatus(False, provider=provider, error="No endpoint configured")
    try:
        models = tuple(get_models(base, timeout=timeout))
        return LocalLLMStatus(True, provider=provider, base_url=base, chat_url=chat_url, models=models)
    except Exception as exc:
        return LocalLLMStatus(False, provider=provider, base_url=base, chat_url=chat_url, error=str(exc))


def discover_local_llm(saved_endpoint: str = "", *, timeout: float = 2.0) -> LocalLLMStatus:
    """Try saved endpoint first, then common local providers."""
    candidates: list[tuple[str, str]] = []
    if (saved_endpoint or "").strip():
        candidates.append(("Custom/Saved Local LLM", saved_endpoint.strip()))
    candidates.extend(COMMON_ENDPOINTS)
    seen: set[str] = set()
    last_error = ""
    for provider, endpoint in candidates:
        base = normalize_base_url(endpoint)
        if not base or base in seen:
            continue
        seen.add(base)
        status = check_endpoint(base, provider=provider, timeout=timeout)
        if status.ok:
            return status
        if status.error:
            last_error = f"{provider} at {base}: {status.error}"
    return LocalLLMStatus(False, provider="Local LLM", error=last_error or "No local LLM endpoint found")


def post_chat(endpoint: str, payload: dict, *, timeout: float = 180.0) -> dict:
    """POST a JSON chat-completions payload to an OpenAI-compatible endpoint."""
    url = chat_url_from_base(endpoint)
    if not url:
        raise RuntimeError("Local LLM endpoint is not configured")
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8", errors="replace")
    parsed = json.loads(body or "{}")
    return parsed if isinstance(parsed, dict) else {"response": parsed}
