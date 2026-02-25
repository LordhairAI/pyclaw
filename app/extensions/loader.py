from __future__ import annotations

import importlib.util
import inspect
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, Field, create_model
from langchain_core.tools import StructuredTool

logger = logging.getLogger("uvicorn.error")

REQUIRED_SPEC_FIELDS = {"label", "name", "description", "parameters", "execute"}
DEFAULT_EXCLUDED_TOOLS: set[str] = set()
DEFAULT_EXCLUDED_EXTENSIONS = {"example","shell"}


@dataclass
class ExtensionLoadResult:
    tools: list[StructuredTool]
    loaded_extensions: list[str]
    failed_extensions: list[dict[str, str]]


def _resolve_extensions_root() -> Path:
    return Path(__file__).resolve().parents[2] / "extensions"


def _discover_extension_files(extensions_root: Path) -> list[Path]:
    if not extensions_root.exists():
        return []
    return sorted(
        file
        for file in extensions_root.glob("*/extension.py")
        if file.is_file()
    )


def _load_module(module_name: str, module_path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load spec for {module_path}")

    module = importlib.util.module_from_spec(spec)
    # Ensure decorators/type resolution that rely on sys.modules (e.g. dataclass)
    # can access the module namespace during execution.
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    return module


def _get_specs(module: Any) -> list[dict[str, Any]]:
    if hasattr(module, "TOOL"):
        return [getattr(module, "TOOL")]

    if hasattr(module, "TOOLS"):
        value = getattr(module, "TOOLS")
        if not isinstance(value, list):
            raise ValueError("TOOLS must be a list of tool specs")
        return value

    raise ValueError("extension.py must define TOOL or TOOLS")


def _python_type_from_name(type_name: str) -> type[Any]:
    normalized = type_name.strip().lower()
    if normalized == "string":
        return str
    if normalized in {"integer", "int"}:
        return int
    if normalized in {"number", "float"}:
        return float
    if normalized in {"boolean", "bool"}:
        return bool
    if normalized == "array":
        return list[Any]
    if normalized == "object":
        return dict[str, Any]
    return str


def _iter_param_specs(parameters: dict[str, Any]) -> tuple[dict[str, Any], set[str]]:
    if parameters.get("type") == "object" and isinstance(parameters.get("properties"), dict):
        props = parameters["properties"]
        required = set(parameters.get("required", []))
        return props, required

    required = {
        key
        for key, value in parameters.items()
        if isinstance(value, dict) and bool(value.get("required"))
    }
    return parameters, required


def _build_args_schema(tool_name: str, parameters: dict[str, Any]) -> type[BaseModel]:
    if not isinstance(parameters, dict):
        raise ValueError("parameters must be a dict")

    fields: dict[str, tuple[type[Any], Any]] = {}
    param_specs, required = _iter_param_specs(parameters)

    for param_name, raw_config in param_specs.items():
        if not isinstance(raw_config, dict):
            raise ValueError(f"parameter '{param_name}' must be a dict")

        declared_type = raw_config.get("type", "string")
        annotation = _python_type_from_name(str(declared_type))
        description = str(raw_config.get("description", "")).strip() or None

        default = raw_config.get("default", ... if param_name in required else None)
        field = Field(default=default, description=description)
        fields[param_name] = (annotation, field)

    model_name = "".join(part.capitalize() for part in tool_name.replace("-", "_").split("_")) or "ExtensionTool"
    return create_model(f"{model_name}Args", **fields)


def _call_execute(execute: Callable[..., Any], kwargs: dict[str, Any]) -> Any:
    try:
        inspect.signature(execute).bind(**kwargs)
    except TypeError as exc:
        if "unexpected keyword argument" not in str(exc):
            raise
        return execute(kwargs)
    return execute(**kwargs)


async def _acall_execute(execute: Callable[..., Any], kwargs: dict[str, Any]) -> Any:
    try:
        inspect.signature(execute).bind(**kwargs)
    except TypeError as exc:
        if "unexpected keyword argument" not in str(exc):
            raise
        return await execute(kwargs)
    return await execute(**kwargs)


def _build_structured_tool(spec: dict[str, Any]) -> StructuredTool:
    missing = REQUIRED_SPEC_FIELDS - set(spec)
    if missing:
        missing_fields = ", ".join(sorted(missing))
        raise ValueError(f"missing required fields: {missing_fields}")

    label = str(spec["label"]).strip()
    name = str(spec["name"]).strip()
    description = str(spec["description"]).strip()
    parameters = spec["parameters"]
    execute = spec["execute"]

    if not label:
        raise ValueError("label must be non-empty")
    if not name:
        raise ValueError("name must be non-empty")
    if not description:
        raise ValueError("description must be non-empty")
    if not callable(execute):
        raise ValueError("execute must be callable")

    args_schema = _build_args_schema(name, parameters)

    if inspect.iscoroutinefunction(execute):
        tool = StructuredTool.from_function(
            coroutine=lambda **kwargs: _acall_execute(execute, kwargs),
            name=name,
            description=description,
            args_schema=args_schema,
        )
    else:
        tool = StructuredTool.from_function(
            func=lambda **kwargs: _call_execute(execute, kwargs),
            name=name,
            description=description,
            args_schema=args_schema,
        )

    if hasattr(tool, "tags"):
        tool.tags = [label]

    return tool


def _get_excluded_tool_names() -> set[str]:
    raw = os.getenv("EXTENSION_EXCLUDED_TOOLS", "").strip()
    extra = {
        name.strip().lower()
        for name in raw.split(",")
        if name.strip()
    }
    return {name.lower() for name in DEFAULT_EXCLUDED_TOOLS} | extra


def _get_excluded_extension_names() -> set[str]:
    raw = os.getenv("EXTENSION_EXCLUDED_EXTENSIONS", "").strip()
    extra = {
        name.strip().lower()
        for name in raw.split(",")
        if name.strip()
    }
    return {name.lower() for name in DEFAULT_EXCLUDED_EXTENSIONS} | extra


def load_extension_tools() -> ExtensionLoadResult:
    extensions_root = _resolve_extensions_root()
    tools: list[StructuredTool] = []
    loaded_extensions: list[str] = []
    failed_extensions: list[dict[str, str]] = []
    seen_tool_names: set[str] = set()
    excluded_tool_names = _get_excluded_tool_names()
    excluded_extension_names = _get_excluded_extension_names()

    for module_path in _discover_extension_files(extensions_root):
        extension_name = module_path.parent.name
        if extension_name.lower() in excluded_extension_names:
            logger.info("Skip excluded extension folder '%s'", extension_name)
            continue
        module_name = f"pyclaw_extension_{extension_name}"

        try:
            module = _load_module(module_name, module_path)
            specs = _get_specs(module)
            extension_tools: list[StructuredTool] = []

            for spec in specs:
                if not isinstance(spec, dict):
                    raise ValueError("tool spec must be a dict")

                tool = _build_structured_tool(spec)
                if tool.name.lower() in excluded_tool_names:
                    logger.info(
                        "Skip excluded extension tool '%s' from extension '%s'",
                        tool.name,
                        extension_name,
                    )
                    continue
                if tool.name in seen_tool_names:
                    raise ValueError(f"duplicate tool name: {tool.name}")

                seen_tool_names.add(tool.name)
                extension_tools.append(tool)

            tools.extend(extension_tools)
            loaded_extensions.append(extension_name)
            logger.info(
                "Loaded extension '%s' with tools: %s",
                extension_name,
                [tool.name for tool in extension_tools],
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to load extension '%s': %s", extension_name, exc)
            failed_extensions.append({"extension": extension_name, "error": str(exc)})

    return ExtensionLoadResult(
        tools=tools,
        loaded_extensions=loaded_extensions,
        failed_extensions=failed_extensions,
    )
