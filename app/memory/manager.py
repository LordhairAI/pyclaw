import argparse
import asyncio
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from app.workflow import close_async_components, get_app_graph


def _normalize_content(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and item.get("text"):
                parts.append(str(item["text"]))
            else:
                maybe_text = getattr(item, "text", None)
                if maybe_text:
                    parts.append(str(maybe_text))
        return "".join(parts).strip()
    return str(content).strip()


def _to_iso(ts: Any) -> str:
    if isinstance(ts, datetime):
        return ts.isoformat()
    return "-"


def _message_role(message: Any) -> str:
    msg_type = getattr(message, "type", None)
    if msg_type:
        return str(msg_type)
    role = getattr(message, "role", None)
    if role:
        return str(role)
    if isinstance(message, dict):
        return str(message.get("role") or message.get("type") or "unknown")
    return "unknown"


def _message_content(message: Any) -> str:
    if isinstance(message, dict):
        return _normalize_content(message.get("content"))
    return _normalize_content(getattr(message, "content", ""))


def _iter_messages(raw_messages: Any) -> Iterable[Any]:
    if isinstance(raw_messages, list) or isinstance(raw_messages, tuple):
        return raw_messages
    return []


async def get_session_history(session_id: str, limit: int = 20) -> list[dict[str, Any]]:
    """按 session_id 读取历史对话并返回结构化结果（时间正序）。"""
    app_graph = await get_app_graph()
    config = {"configurable": {"thread_id": session_id}}

    snapshots = []
    async for state in app_graph.aget_state_history(config, limit=limit):
        snapshots.append(state)

    history: list[dict[str, Any]] = []
    # aget_state_history 默认最新优先，这里反转为时间正序
    for state in reversed(snapshots):
        checkpoint_id = state.config.get("configurable", {}).get("checkpoint_id", "-")
        created_at = _to_iso(getattr(state, "created_at", None))
        next_nodes = list(getattr(state, "next", ()) or ())

        messages: list[dict[str, str]] = []
        for message in _iter_messages((state.values or {}).get("messages")):
            content = _message_content(message)
            if not content:
                continue
            messages.append({
                "role": _message_role(message),
                "content": content,
            })

        history.append(
            {
                "checkpoint_id": checkpoint_id,
                "created_at": created_at,
                "next": next_nodes,
                "messages": messages,
            }
        )

    return history