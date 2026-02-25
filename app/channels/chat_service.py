import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.workflow import get_app_graph, get_fallback_app_graph

SERDE_ERROR_MARKERS = (
    "cannot pickle '_thread.lock' object",
    "Type is not msgpack serializable: Send",
)


@dataclass
class ChatResult:
    session_id: str
    response: str
    created_at: str


def ensure_session_id(session_id: str | None = None) -> str:
    return session_id or str(uuid.uuid4())


def _normalize_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if text:
                    parts.append(str(text))
            elif isinstance(item, str):
                parts.append(item)
            else:
                maybe_text = getattr(item, "text", None)
                if maybe_text:
                    parts.append(str(maybe_text))
        return "".join(parts).strip()
    return str(content)


def extract_reply(result: Any) -> str:
    if isinstance(result, dict):
        messages = result.get("messages")
        if messages:
            last_msg = messages[-1]
            content = getattr(last_msg, "content", None)
            if content is None and isinstance(last_msg, dict):
                content = last_msg.get("content")
            if content:
                return _normalize_content(content)

        output = result.get("output")
        if output:
            return _normalize_content(output)

    content = getattr(result, "content", None)
    if content:
        return _normalize_content(content)
    return str(result)


async def invoke_chat(
    message: str,
    *,
    session_id: str | None = None,
    app_graph: Any = None,
) -> ChatResult:
    text = message.strip()
    if not text:
        raise ValueError("message cannot be empty")

    current_session_id = ensure_session_id(session_id)
    invoke_input = {"messages": [{"role": "user", "content": text}]}
    invoke_config = {"configurable": {"thread_id": current_session_id}}

    try:
        graph = app_graph or await get_app_graph()
        result = await graph.ainvoke(invoke_input, config=invoke_config)
        reply = extract_reply(result).strip()
        if not reply:
            reply = "抱歉，我暂时没有可返回的内容。"
    except Exception as exc:
        if any(marker in str(exc) for marker in SERDE_ERROR_MARKERS):
            fallback_graph = await get_fallback_app_graph()
            result = await fallback_graph.ainvoke(invoke_input, config=invoke_config)
            reply = extract_reply(result).strip()
            if not reply:
                reply = "抱歉，我暂时没有可返回的内容。"
        else:
            raise

    return ChatResult(
        session_id=current_session_id,
        response=reply,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
