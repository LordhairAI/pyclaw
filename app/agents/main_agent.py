import os
from pathlib import Path
from typing import Any, Sequence
from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain.agents.middleware import (
    FilesystemFileSearchMiddleware,
    HostExecutionPolicy,
    ShellToolMiddleware,
)
from langchain.chat_models import init_chat_model

from app.agents.prompt import buildAgentSystemPrompt
from app.state.agent_state import AgentState
import logging
from langchain_core.tools import BaseTool
from app.agents.tools.load_skill import load_skill
from app.agents.tools.fetch_url import fetch_url
from app.agents.tools.cron_tools import cron
load_dotenv()
logger = logging.getLogger("uvicorn.error")

def load_workspace_context_files(workspace_root: str) -> list[dict[str, str]]:
    workspace_dir = Path(workspace_root) / "workspace"
    if not workspace_dir.exists():
        logger.warning(f"Workspace directory not found: {workspace_dir}")
        return []

    context_files: list[dict[str, str]] = []
    for md_file in sorted(workspace_dir.glob("*.md")):
        if not md_file.is_file():
            continue
        try:
            context_files.append(
                {
                    "path": md_file.name,
                    "content": md_file.read_text(encoding="utf-8"),
                }
            )
        except OSError as exc:
            logger.warning(f"Failed to read context file {md_file}: {exc}")

    return context_files


def _normalize_history_role(role: str) -> str | None:
    role_map = {
        "human": "user",
        "user": "user",
        "ai": "assistant",
        "assistant": "assistant",
        "system": "system",
    }
    return role_map.get(role.strip().lower())


def _extract_messages(invoke_input: Any) -> list[dict[str, Any]]:
    if not isinstance(invoke_input, dict):
        return []
    messages = invoke_input.get("messages")
    if isinstance(messages, list):
        return messages
    return []


def _should_load_history(messages: list[dict[str, Any]]) -> bool:
    if not messages:
        return False
    if len(messages) != 1:
        return False
    first_role = str(messages[0].get("role", "")).strip().lower()
    return first_role in {"user", "human"}


class _HistoryLoadingGraph:
    def __init__(self, graph: Any):
        self._graph = graph

    def __getattr__(self, item: str) -> Any:
        return getattr(self._graph, item)

    async def ainvoke(self, input: Any, config: Any = None, **kwargs: Any) -> Any:
        messages = _extract_messages(input)
        thread_id = None
        if isinstance(config, dict):
            thread_id = (config.get("configurable") or {}).get("thread_id")

        if thread_id and _should_load_history(messages):
            try:
                from app.memory.manager import get_session_history
                history_items = await get_session_history(str(thread_id), limit=5)
                if history_items:
                    persisted_messages = history_items[-1].get("messages") or []
                    history_messages: list[dict[str, Any]] = []
                    for msg in persisted_messages:
                        if not isinstance(msg, dict):
                            continue
                        role = _normalize_history_role(str(msg.get("role", "")))
                        content = str(msg.get("content", "")).strip()
                        if role and content:
                            history_messages.append({"role": role, "content": content})

                    if history_messages:
                        input = {**input, "messages": [*history_messages, *messages]}
            except Exception as exc:
                logger.warning(
                    "Failed to load history for session %s, fallback to current input only: %s",
                    thread_id,
                    exc,
                )

        return await self._graph.ainvoke(input, config=config, **kwargs)


def create_main_agent(
    checkpointer=None,
    store=None,
    extension_tools: Sequence[BaseTool] | None = None,
):
    workspace_root = os.getenv("WORKSPACE_ROOT") or str(Path.cwd())
    builtin_tools: list[BaseTool] = [load_skill, fetch_url, cron]
    dynamic_tools = list(extension_tools or [])
    tools: list[BaseTool] = [*builtin_tools, *dynamic_tools]

    chat_model = init_chat_model(
        model=os.getenv("MODEL"),
        model_provider=os.getenv("MODEL_PROVIDER", "openai"),
        base_url=os.getenv("BASE_URL"),
        api_key=os.getenv("API_KEY"),
    )
    context_files = load_workspace_context_files(workspace_root)
    system_prompt = buildAgentSystemPrompt({
        "promptMode":"full",
        "workspaceDir": workspace_root,
        "toolNames": [tool.name for tool in tools],
        "contextFiles": context_files
    })
    #logger.info(f"SystemPrompt: {system_prompt}")
    result = create_agent(
        model=chat_model,
        tools=tools,
        state_schema=AgentState,
        system_prompt=system_prompt,
        checkpointer=checkpointer,
        store=store,
        middleware=[
            ShellToolMiddleware(
                workspace_root="/",
                execution_policy=HostExecutionPolicy(),
            ),
            # LLMToolSelectorMiddleware(
            #     model=chat_model,
            #     max_tools=3,
            #     always_include=["query_weather"],
            # ),
            FilesystemFileSearchMiddleware(
                root_path=workspace_root,
                use_ripgrep=True,
            ),
        ],
    )
    #logger.info(f"result: {result}")
    return _HistoryLoadingGraph(result)
