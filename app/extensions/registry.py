from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from langchain_core.tools import BaseTool

from app.extensions.loader import ExtensionLoadResult, load_extension_tools


@dataclass
class ExtensionSnapshot:
    version: int = 0
    tools: list[BaseTool] = field(default_factory=list)
    loaded_extensions: list[str] = field(default_factory=list)
    failed_extensions: list[dict[str, str]] = field(default_factory=list)


class ExtensionRegistry:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._snapshot = ExtensionSnapshot()

    def get_snapshot(self) -> ExtensionSnapshot:
        return self._snapshot

    async def reload(self) -> ExtensionSnapshot:
        async with self._lock:
            result: ExtensionLoadResult = load_extension_tools()
            self._snapshot = ExtensionSnapshot(
                version=self._snapshot.version + 1,
                tools=result.tools,
                loaded_extensions=result.loaded_extensions,
                failed_extensions=result.failed_extensions,
            )
            return self._snapshot


_extension_registry = ExtensionRegistry()


def get_extension_registry() -> ExtensionRegistry:
    return _extension_registry
