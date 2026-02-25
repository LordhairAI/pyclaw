"""Shell command extension tool."""

from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path
import logging
logger = logging.getLogger("uvicorn.error")

DEFAULT_TIMEOUT_SECONDS = 20
MAX_TIMEOUT_SECONDS = 120
DEFAULT_MAX_OUTPUT_CHARS = 6000
MAX_OUTPUT_CHARS = 20000
SHELL_SYNTAX_MARKERS = ("<<", "\n")


def _resolve_workspace_root() -> Path:
    workspace_root = os.getenv("WORKSPACE_ROOT")
    if workspace_root:
        return Path(workspace_root).resolve()
    return Path.cwd().resolve()


def _is_within(base: Path, target: Path) -> bool:
    try:
        target.relative_to(base)
        return True
    except ValueError:
        return False


def _normalize_int(value: int | None, default: int, maximum: int) -> int:
    if value is None:
        return default
    if value <= 0:
        return default
    return min(value, maximum)


def _to_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def run_shell_command(
    command: str,
    cwd: str | None = None,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    max_output_chars: int = DEFAULT_MAX_OUTPUT_CHARS,
) -> str:
    """Run a shell-like command safely (without invoking a shell)."""
    logger.info(f"command: {command}")
    normalized_command = command.strip()
    #logger.info(f"command: {normalized_command}")
    if not normalized_command:
        return "Error: command must not be empty."
    has_shell_operators = any(
        op in normalized_command for op in (" && ", " || ", " | ")
    )
    if any(marker in normalized_command for marker in SHELL_SYNTAX_MARKERS) or has_shell_operators:
        return (
            "Error: shell syntax detected, but this tool runs without a shell. "
            "Use plain argv-style commands, or wrap explicitly with `bash -lc '...'`."
        )

    workspace_root = _resolve_workspace_root()
    run_cwd = workspace_root

    if cwd:
        candidate = (workspace_root / cwd).resolve()
        if not _is_within(workspace_root, candidate):
            return f"Error: cwd must stay inside workspace root: {workspace_root}"
        if not candidate.exists() or not candidate.is_dir():
            return f"Error: cwd does not exist or is not a directory: {candidate}"
        run_cwd = candidate

    timeout_seconds = _normalize_int(timeout_seconds, DEFAULT_TIMEOUT_SECONDS, MAX_TIMEOUT_SECONDS)
    max_output_chars = _normalize_int(max_output_chars, DEFAULT_MAX_OUTPUT_CHARS, MAX_OUTPUT_CHARS)

    try:
        args = shlex.split(normalized_command)
    except ValueError as exc:
        return f"Error: invalid command syntax: {exc}"
    logger.info(f"command_args: {args}")
    if not args:
        return "Error: command produced no executable arguments."

    try:
        completed = subprocess.run(
            args,
            cwd=str(run_cwd),
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            shell=False,
        )
        logger.info(f"command_exec: {completed}")
    except FileNotFoundError:
        return f"Error: command not found: {args[0]}"
    except subprocess.TimeoutExpired as exc:
        stdout = _to_text(exc.stdout)
        stderr = _to_text(exc.stderr)
        output = (stdout + "\n" + stderr).strip()
        if len(output) > max_output_chars:
            output = output[:max_output_chars] + "\n...<truncated>"
        return (
            f"Status: timeout after {timeout_seconds}s\n"
            f"cwd: {run_cwd}\n"
            f"command: {normalized_command}\n"
            f"output:\n{output}"
        )
    except Exception as exc:  # noqa: BLE001
        return f"Error: failed to execute command: {exc}"

    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    combined = stdout if not stderr else f"{stdout}\n[stderr]\n{stderr}"
    combined = combined.strip()

    truncated = False
    if len(combined) > max_output_chars:
        combined = combined[:max_output_chars].rstrip() + "\n...<truncated>"
        truncated = True

    status = "ok" if completed.returncode == 0 else "error"
    truncated_line = "yes" if truncated else "no"

    return (
        f"Status: {status}\n"
        f"exit_code: {completed.returncode}\n"
        f"cwd: {run_cwd}\n"
        f"command: {normalized_command}\n"
        f"truncated: {truncated_line}\n"
        f"output:\n{combined}"
    )


TOOL = {
    "label": "shell",
    "name": "run_shell_command",
    "description": "Run a shell command in workspace and return stdout/stderr with exit code.",
    "parameters": {
        "command": {
            "type": "string",
            "description": "Command string, for example: ls -la app",
            "required": True,
        },
        "cwd": {
            "type": "string",
            "description": "Optional workspace-relative working directory",
        },
        "timeout_seconds": {
            "type": "integer",
            "description": "Execution timeout in seconds (1-120)",
            "default": DEFAULT_TIMEOUT_SECONDS,
        },
        "max_output_chars": {
            "type": "integer",
            "description": "Maximum output characters in response (1-20000)",
            "default": DEFAULT_MAX_OUTPUT_CHARS,
        },
    },
    "execute": run_shell_command,
}
