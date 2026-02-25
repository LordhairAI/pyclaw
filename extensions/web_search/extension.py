"""Web search extension tool with Brave, Perplexity, and Grok providers."""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin
from urllib.request import Request, urlopen

DEFAULT_PROVIDER = "brave"
DEFAULT_TIMEOUT_SECONDS = 20
DEFAULT_CACHE_TTL_MINUTES = 10
DEFAULT_SEARCH_COUNT = 5
MAX_SEARCH_COUNT = 10

BRAVE_SEARCH_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
DEFAULT_PERPLEXITY_BASE_URL = "https://openrouter.ai/api/v1"
PERPLEXITY_DIRECT_BASE_URL = "https://api.perplexity.ai"
DEFAULT_PERPLEXITY_MODEL = "perplexity/sonar-pro"
XAI_API_ENDPOINT = "https://api.x.ai/v1/responses"
DEFAULT_GROK_MODEL = "grok-4-1-fast"

PERPLEXITY_KEY_PREFIXES = ("pplx-",)
OPENROUTER_KEY_PREFIXES = ("sk-or-",)
FRESHNESS_SHORTCUTS = {"pd", "pw", "pm", "py"}
FRESHNESS_RANGE = re.compile(r"^(\d{4}-\d{2}-\d{2})to(\d{4}-\d{2}-\d{2})$")


_CACHE: dict[str, dict[str, Any]] = {}
_OPENXBOT_CONFIG_CACHE: dict[str, Any] | None = None


def _load_openxbot_config() -> dict[str, Any]:
    global _OPENXBOT_CONFIG_CACHE
    if _OPENXBOT_CONFIG_CACHE is not None:
        return _OPENXBOT_CONFIG_CACHE

    raw_config_path = _normalize_str(os.getenv("CONFIG_PATH"))
    if not raw_config_path:
        _OPENXBOT_CONFIG_CACHE = {}
        return _OPENXBOT_CONFIG_CACHE

    config_path = Path(raw_config_path).expanduser()
    if not config_path.is_absolute():
        config_path = (Path.cwd() / config_path).resolve()

    if not config_path.exists() or not config_path.is_file():
        _OPENXBOT_CONFIG_CACHE = {}
        return _OPENXBOT_CONFIG_CACHE

    try:
        with config_path.open("r", encoding="utf-8") as fp:
            data = json.load(fp)
    except (OSError, json.JSONDecodeError):
        _OPENXBOT_CONFIG_CACHE = {}
        return _OPENXBOT_CONFIG_CACHE

    _OPENXBOT_CONFIG_CACHE = data if isinstance(data, dict) else {}
    return _OPENXBOT_CONFIG_CACHE


def _get_tool_config_value(tool_name: str, key_name: str) -> str:
    normalized_tool = _normalize_str(tool_name)
    normalized_key = _normalize_str(key_name)
    if not normalized_tool or not normalized_key:
        return ""

    config = _load_openxbot_config()
    tool_config = config.get(normalized_tool)
    if not isinstance(tool_config, dict):
        return ""
    return _normalize_str(tool_config.get(normalized_key))


def _normalize_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _normalize_int(value: Any, default: int) -> int:
    try:
        if value is None:
            return default
        parsed = int(value)
        return parsed
    except (TypeError, ValueError):
        return default


def _resolve_provider() -> str:
    provider = _normalize_str(os.getenv("WEB_SEARCH_PROVIDER", DEFAULT_PROVIDER)).lower()
    if provider in {"brave", "perplexity", "grok"}:
        return provider
    return DEFAULT_PROVIDER


def _resolve_timeout_seconds() -> int:
    timeout = _normalize_int(os.getenv("WEB_SEARCH_TIMEOUT_SECONDS"), DEFAULT_TIMEOUT_SECONDS)
    if timeout <= 0:
        return DEFAULT_TIMEOUT_SECONDS
    return timeout


def _resolve_cache_ttl_seconds() -> int:
    ttl_minutes = _normalize_int(os.getenv("WEB_SEARCH_CACHE_TTL_MINUTES"), DEFAULT_CACHE_TTL_MINUTES)
    if ttl_minutes <= 0:
        ttl_minutes = DEFAULT_CACHE_TTL_MINUTES
    return ttl_minutes * 60


def _resolve_search_count(value: Any) -> int:
    count = _normalize_int(value, DEFAULT_SEARCH_COUNT)
    if count < 1:
        return 1
    if count > MAX_SEARCH_COUNT:
        return MAX_SEARCH_COUNT
    return count


def _is_valid_iso_date(value: str) -> bool:
    try:
        datetime.strptime(value, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def _normalize_freshness(value: str | None) -> str | None:
    normalized = _normalize_str(value).lower()
    if not normalized:
        return None
    if normalized in FRESHNESS_SHORTCUTS:
        return normalized

    match = FRESHNESS_RANGE.match(normalized)
    if not match:
        return None

    start, end = match.group(1), match.group(2)
    if not _is_valid_iso_date(start) or not _is_valid_iso_date(end):
        return None
    if start > end:
        return None
    return f"{start}to{end}"


def _freshness_to_perplexity_recency(freshness: str | None) -> str | None:
    mapping = {
        "pd": "day",
        "pw": "week",
        "pm": "month",
        "py": "year",
    }
    if not freshness:
        return None
    return mapping.get(freshness)


def _cache_get(key: str) -> dict[str, Any] | None:
    entry = _CACHE.get(key)
    if not entry:
        return None
    if float(entry.get("expires_at", 0)) <= time.time():
        _CACHE.pop(key, None)
        return None
    payload = dict(entry.get("value") or {})
    payload["cached"] = True
    return payload


def _cache_set(key: str, value: dict[str, Any]) -> None:
    ttl_seconds = _resolve_cache_ttl_seconds()
    _CACHE[key] = {
        "expires_at": time.time() + ttl_seconds,
        "value": dict(value),
    }


def _json_request(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    payload: dict[str, Any] | None = None,
    timeout_seconds: int | None = None,
) -> dict[str, Any]:
    body_bytes = None
    req_headers = dict(headers or {})
    if payload is not None:
        body_bytes = json.dumps(payload).encode("utf-8")
        req_headers.setdefault("Content-Type", "application/json")

    req = Request(url=url, method=method.upper(), data=body_bytes, headers=req_headers)

    try:
        with urlopen(req, timeout=timeout_seconds or _resolve_timeout_seconds()) as response:
            raw = response.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw else {}
    except HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")
        except Exception:
            detail = ""
        raise RuntimeError(f"HTTP {exc.code} from {url}: {detail or exc.reason}") from exc
    except URLError as exc:
        raise RuntimeError(f"Network error for {url}: {exc}") from exc


def _infer_perplexity_base_url_from_key(api_key: str) -> str:
    key = api_key.lower()
    if any(key.startswith(prefix) for prefix in PERPLEXITY_KEY_PREFIXES):
        return PERPLEXITY_DIRECT_BASE_URL
    if any(key.startswith(prefix) for prefix in OPENROUTER_KEY_PREFIXES):
        return DEFAULT_PERPLEXITY_BASE_URL
    return DEFAULT_PERPLEXITY_BASE_URL


def _resolve_brave_api_key() -> str:
    from_config = _get_tool_config_value("web_search", "BRAVE_API_KEY")
    if from_config:
        return from_config
    return _normalize_str(os.getenv("BRAVE_API_KEY"))


def _resolve_perplexity_api_key() -> str:
    direct = _normalize_str(os.getenv("PERPLEXITY_API_KEY"))
    if direct:
        return direct
    return _normalize_str(os.getenv("OPENROUTER_API_KEY"))


def _resolve_grok_api_key() -> str:
    return _normalize_str(os.getenv("XAI_API_KEY"))


def _run_brave_search(
    query: str,
    count: int,
    country: str | None,
    search_lang: str | None,
    ui_lang: str | None,
    freshness: str | None,
    timeout_seconds: int,
) -> dict[str, Any]:
    api_key = _resolve_brave_api_key()
    if not api_key:
        return {
            "error": "missing_brave_api_key",
            "message": (
                "Set web_search.BRAVE_API_KEY in CONFIG_PATH "
                "or BRAVE_API_KEY env before using web_search with brave provider."
            ),
        }

    params = {"q": query, "count": str(count)}
    if country:
        params["country"] = country
    if search_lang:
        params["search_lang"] = search_lang
    if ui_lang:
        params["ui_lang"] = ui_lang
    if freshness:
        params["freshness"] = freshness

    url = f"{BRAVE_SEARCH_ENDPOINT}?{urlencode(params)}"
    started = time.time()
    data = _json_request(
        "GET",
        url,
        headers={
            "Accept": "application/json",
            "X-Subscription-Token": api_key,
        },
        timeout_seconds=timeout_seconds,
    )

    results: list[dict[str, Any]] = []
    for item in data.get("web", {}).get("results", []) or []:
        if not isinstance(item, dict):
            continue
        url_value = _normalize_str(item.get("url"))
        if not url_value:
            continue
        results.append(
            {
                "title": _normalize_str(item.get("title")),
                "url": url_value,
                "description": _normalize_str(item.get("description")),
                "published": _normalize_str(item.get("age")) or None,
            }
        )

    return {
        "query": query,
        "provider": "brave",
        "count": len(results),
        "tookMs": int((time.time() - started) * 1000),
        "results": results,
    }


def _run_perplexity_search(query: str, freshness: str | None, timeout_seconds: int) -> dict[str, Any]:
    api_key = _resolve_perplexity_api_key()
    if not api_key:
        return {
            "error": "missing_perplexity_api_key",
            "message": "Set PERPLEXITY_API_KEY or OPENROUTER_API_KEY before using web_search with perplexity provider.",
        }

    base_url = _normalize_str(os.getenv("PERPLEXITY_BASE_URL")) or _infer_perplexity_base_url_from_key(api_key)
    model = _normalize_str(os.getenv("PERPLEXITY_MODEL")) or DEFAULT_PERPLEXITY_MODEL
    if base_url == PERPLEXITY_DIRECT_BASE_URL and model.startswith("perplexity/"):
        model = model.split("/", 1)[1]

    endpoint = urljoin(base_url.rstrip("/") + "/", "chat/completions")
    body: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": query}],
    }
    recency = _freshness_to_perplexity_recency(freshness)
    if recency:
        body["search_recency_filter"] = recency

    started = time.time()
    data = _json_request(
        "POST",
        endpoint,
        headers={
            "Authorization": f"Bearer {api_key}",
            "HTTP-Referer": "https://openxbot.local",
            "X-Title": "OpenXBot Web Search",
        },
        payload=body,
        timeout_seconds=timeout_seconds,
    )

    choices = data.get("choices") or []
    content = ""
    if choices and isinstance(choices[0], dict):
        content = _normalize_str((choices[0].get("message") or {}).get("content"))

    citations = data.get("citations") if isinstance(data.get("citations"), list) else []
    return {
        "query": query,
        "provider": "perplexity",
        "model": model,
        "tookMs": int((time.time() - started) * 1000),
        "content": content or "No response",
        "citations": citations,
    }


def _extract_grok_content(data: dict[str, Any]) -> tuple[str, list[str], list[dict[str, Any]] | None]:
    output = data.get("output")
    if isinstance(output, list):
        for block in output:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "message":
                content_list = block.get("content")
                if isinstance(content_list, list):
                    for content_block in content_list:
                        if not isinstance(content_block, dict):
                            continue
                        if content_block.get("type") == "output_text":
                            text = _normalize_str(content_block.get("text"))
                            annotations = content_block.get("annotations") or []
                            cites = []
                            if isinstance(annotations, list):
                                for ann in annotations:
                                    if isinstance(ann, dict) and ann.get("type") == "url_citation":
                                        cite_url = _normalize_str(ann.get("url"))
                                        if cite_url:
                                            cites.append(cite_url)
                            if text:
                                return text, sorted(set(cites)), data.get("inline_citations")
            if block.get("type") == "output_text":
                text = _normalize_str(block.get("text"))
                if text:
                    return text, [], data.get("inline_citations")

    fallback = _normalize_str(data.get("output_text"))
    return (fallback or "No response", [], data.get("inline_citations"))


def _run_grok_search(query: str, timeout_seconds: int) -> dict[str, Any]:
    api_key = _resolve_grok_api_key()
    if not api_key:
        return {
            "error": "missing_xai_api_key",
            "message": "Set XAI_API_KEY before using web_search with grok provider.",
        }

    model = _normalize_str(os.getenv("GROK_MODEL")) or DEFAULT_GROK_MODEL
    body = {
        "model": model,
        "input": [{"role": "user", "content": query}],
        "tools": [{"type": "web_search"}],
    }

    started = time.time()
    data = _json_request(
        "POST",
        XAI_API_ENDPOINT,
        headers={"Authorization": f"Bearer {api_key}"},
        payload=body,
        timeout_seconds=timeout_seconds,
    )

    content, annotation_citations, inline_citations = _extract_grok_content(data)
    citations = data.get("citations") if isinstance(data.get("citations"), list) else []
    if not citations:
        citations = annotation_citations

    return {
        "query": query,
        "provider": "grok",
        "model": model,
        "tookMs": int((time.time() - started) * 1000),
        "content": content,
        "citations": citations,
        "inlineCitations": inline_citations,
    }


def web_search(
    query: str,
    count: int = DEFAULT_SEARCH_COUNT,
    country: str | None = None,
    search_lang: str | None = None,
    ui_lang: str | None = None,
    freshness: str | None = None,
) -> str:
    """Search the web using provider selected by WEB_SEARCH_PROVIDER env."""
    query = _normalize_str(query)
    if not query:
        return json.dumps(
            {
                "error": "invalid_query",
                "message": "query is required",
            },
            ensure_ascii=False,
        )

    provider = _resolve_provider()
    timeout_seconds = _resolve_timeout_seconds()
    count = _resolve_search_count(count)

    normalized_freshness = _normalize_freshness(freshness)
    if freshness and not normalized_freshness:
        return json.dumps(
            {
                "error": "invalid_freshness",
                "message": "freshness must be pd/pw/pm/py or YYYY-MM-DDtoYYYY-MM-DD",
            },
            ensure_ascii=False,
        )

    if normalized_freshness and provider not in {"brave", "perplexity"}:
        return json.dumps(
            {
                "error": "unsupported_freshness",
                "message": "freshness is only supported by brave and perplexity providers",
            },
            ensure_ascii=False,
        )

    cache_key = "|".join(
        [
            provider,
            query,
            str(count),
            _normalize_str(country) or "-",
            _normalize_str(search_lang) or "-",
            _normalize_str(ui_lang) or "-",
            normalized_freshness or "-",
        ]
    )
    cached = _cache_get(cache_key)
    if cached:
        return json.dumps(cached, ensure_ascii=False)

    try:
        if provider == "brave":
            payload = _run_brave_search(
                query=query,
                count=count,
                country=_normalize_str(country) or None,
                search_lang=_normalize_str(search_lang) or None,
                ui_lang=_normalize_str(ui_lang) or None,
                freshness=normalized_freshness,
                timeout_seconds=timeout_seconds,
            )
        elif provider == "perplexity":
            payload = _run_perplexity_search(
                query=query,
                freshness=normalized_freshness,
                timeout_seconds=timeout_seconds,
            )
        elif provider == "grok":
            payload = _run_grok_search(query=query, timeout_seconds=timeout_seconds)
        else:
            payload = {
                "error": "unsupported_provider",
                "message": f"unsupported provider: {provider}",
            }
    except Exception as exc:  # noqa: BLE001
        payload = {
            "error": "web_search_failed",
            "message": str(exc),
            "provider": provider,
        }

    _cache_set(cache_key, payload)
    return json.dumps(payload, ensure_ascii=False)


TOOL = {
    "label": "web",
    "name": "web_search",
    "description": (
        "Search the web via Brave/Perplexity/Grok. "
        "Provider comes from WEB_SEARCH_PROVIDER env (brave|perplexity|grok)."
    ),
    "parameters": {
        "query": {
            "type": "string",
            "description": "Search query string",
            "required": True,
        },
        "count": {
            "type": "integer",
            "description": "Number of results for brave provider (1-10)",
            "default": DEFAULT_SEARCH_COUNT,
        },
        "country": {
            "type": "string",
            "description": "2-letter country code for brave provider, e.g. US",
        },
        "search_lang": {
            "type": "string",
            "description": "Search language for brave provider, e.g. en",
        },
        "ui_lang": {
            "type": "string",
            "description": "UI language for brave provider, e.g. en-US",
        },
        "freshness": {
            "type": "string",
            "description": "pd/pw/pm/py or YYYY-MM-DDtoYYYY-MM-DD (brave/perplexity only)",
        },
    },
    "execute": web_search,
}
