import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.channels.chat_service import (
    ChatResult,
    ensure_session_id,
    invoke_chat,
)

logger = logging.getLogger("uvicorn.error")
router = APIRouter()


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, description="User input message")
    session_id: Optional[str] = Field(
        default=None,
        description="Optional conversation id. If omitted, a new one is generated.",
    )


class ChatResponse(BaseModel):
    session_id: str
    response: str
    created_at: str


@router.post("/chat", response_model=ChatResponse)
async def chat(request: Request, payload: ChatRequest) -> ChatResponse:
    if not payload.message.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="message cannot be empty",
        )

    session_id = ensure_session_id(payload.session_id)

    try:
        result: ChatResult = await invoke_chat(
            payload.message,
            session_id=session_id,
            app_graph=getattr(request.app.state, "app_graph", None),
        )
    except Exception as exc:
        logger.exception("Chat invoke failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"chat invocation failed: {exc}",
        ) from exc

    return ChatResponse(
        session_id=result.session_id,
        response=result.response,
        created_at=result.created_at,
    )
