import os
from fastapi import APIRouter, Header, HTTPException, status
from pydantic import BaseModel

from app.workflow import reload_graph_from_extensions

router = APIRouter()


class ExtensionReloadResponse(BaseModel):
    version: int
    loaded_extensions: list[str]
    failed_extensions: list[dict[str, str]]
    tools: list[str]


def _assert_admin_token(x_admin_token: str | None) -> None:
    expected = os.getenv("EXTENSIONS_ADMIN_TOKEN", "").strip()
    if not expected:
        return

    if x_admin_token != expected:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="invalid admin token",
        )


@router.post("/extensions/reload", response_model=ExtensionReloadResponse)
async def reload_extensions(x_admin_token: str | None = Header(default=None)) -> ExtensionReloadResponse:
    _assert_admin_token(x_admin_token)

    snapshot = await reload_graph_from_extensions()
    return ExtensionReloadResponse(
        version=snapshot.version,
        loaded_extensions=snapshot.loaded_extensions,
        failed_extensions=snapshot.failed_extensions,
        tools=[tool.name for tool in snapshot.tools],
    )
