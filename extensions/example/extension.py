"""Example extension for dynamic LangChain tool loading."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def _normalize_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def get_tool_config_value(tool_name: str, key_name: str) -> str:
    """Read a tool-specific value from CONFIG_PATH JSON config."""
    normalized_tool = _normalize_str(tool_name)
    normalized_key = _normalize_str(key_name)
    if not normalized_tool or not normalized_key:
        return ""

    raw_config_path = _normalize_str(os.getenv("CONFIG_PATH"))
    if not raw_config_path:
        return ""

    config_path = Path(raw_config_path).expanduser()
    if not config_path.is_absolute():
        config_path = (Path.cwd() / config_path).resolve()

    if not config_path.exists() or not config_path.is_file():
        return ""

    try:
        with config_path.open("r", encoding="utf-8") as fp:
            data = json.load(fp)
    except (OSError, json.JSONDecodeError):
        return ""

    if not isinstance(data, dict):
        return ""

    tool_config = data.get(normalized_tool)
    if not isinstance(tool_config, dict):
        return ""

    return _normalize_str(tool_config.get(normalized_key))


def get_weather(city: str, unit: str = "c") -> str:
    """Return a mock weather response for demo/testing."""
    normalized_city = city.strip()
    normalized_unit = unit.strip().lower()
    if normalized_unit not in {"c", "f"}:
        return "unit must be 'c' or 'f'"

    temp = 26 if normalized_unit == "c" else 79
    return f"{normalized_city} current temperature is {temp}°{normalized_unit.upper()} (mock)."


TOOL = {
    "label": "weather",
    "name": "get_weather",
    "description": "Query current weather by city (mock extension tool).",
    "parameters": {
        "city": {
            "type": "string",
            "description": "City name, e.g. Shanghai",
            "required": True,
        },
        "unit": {
            "type": "string",
            "description": "Temperature unit: c or f",
            "default": "c",
        },
    },
    "execute": get_weather,
}
