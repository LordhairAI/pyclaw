"""Bash exec/process extension tools.

Provides OpenClaw-style shell execution primitives:
- exec: run shell commands with background continuation
- process: manage background sessions (list/poll/log/write/kill/clear/remove)
"""

from __future__ import annotations

import os
import pty
import shlex
import signal
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import logging
logger = logging.getLogger("uvicorn.error")

DEFAULT_TIMEOUT_SECONDS = 1800
MAX_TIMEOUT_SECONDS = 7200
DEFAULT_MAX_OUTPUT_CHARS = 200000
MAX_OUTPUT_CHARS = 200000
DEFAULT_PENDING_MAX_OUTPUT_CHARS = 30000
DEFAULT_YIELD_MS = 10000
MIN_YIELD_MS = 10
MAX_YIELD_MS = 120000
DEFAULT_POLL_TIMEOUT_MS = 0
MAX_POLL_TIMEOUT_MS = 120000


@dataclass
class Session:
    id: str
    command: str
    cwd: str
    started_at_ms: int
    process: subprocess.Popen[Any]
    max_output_chars: int
    pending_max_output_chars: int
    pty_mode: bool = False
    master_fd: int | None = None
    stdin_stream: Any | None = None
    backgrounded: bool = False
    exited: bool = False
    exit_code: int | None = None
    ended_at_ms: int | None = None
    total_output_chars: int = 0
    truncated: bool = False
    aggregated: str = ""
    pending: str = ""
    tail: str = ""
    lock: threading.RLock = field(default_factory=threading.RLock)
    cond: threading.Condition | None = None
    timeout_timer: threading.Timer | None = None

    def __post_init__(self) -> None:
        self.cond = threading.Condition(self.lock)


@dataclass
class FinishedSession:
    id: str
    command: str
    cwd: str
    started_at_ms: int
    ended_at_ms: int
    exit_code: int | None
    aggregated: str
    tail: str
    truncated: bool


_REGISTRY_LOCK = threading.RLock()
_RUNNING: dict[str, Session] = {}
_FINISHED: dict[str, FinishedSession] = {}


def _resolve_workspace_root() -> Path:
    #raw = os.getenv("WORKSPACE_ROOT")
    raw = "/"
    if raw:
        return Path(raw).expanduser().resolve()
    return Path.cwd().resolve()


def _is_within(base: Path, target: Path) -> bool:
    try:
        target.relative_to(base)
        return True
    except ValueError:
        return False


def _resolve_cwd(workspace_root: Path, cwd: str | None) -> Path:
    if not cwd:
        return workspace_root

    candidate = Path(cwd).expanduser()
    if not candidate.is_absolute():
        candidate = workspace_root / candidate
    resolved = candidate.resolve()

    if not _is_within(workspace_root, resolved):
        raise ValueError(f"cwd must stay inside workspace root: {workspace_root}")
    if not resolved.exists() or not resolved.is_dir():
        raise ValueError(f"cwd does not exist or is not a directory: {resolved}")
    return resolved


def _clamp_int(value: int | None, default: int, minimum: int, maximum: int) -> int:
    if value is None:
        return default
    if value < minimum:
        return minimum
    if value > maximum:
        return maximum
    return value


def _now_ms() -> int:
    return int(time.time() * 1000)


def _append_output(session: Session, chunk: str) -> None:
    with session.lock:
        session.total_output_chars += len(chunk)
        prior_len = len(session.aggregated)
        combined = session.aggregated + chunk
        if len(combined) > session.max_output_chars:
            session.truncated = True
            session.aggregated = combined[-session.max_output_chars :]
        else:
            session.aggregated = combined

        pending_combined = session.pending + chunk
        if len(pending_combined) > session.pending_max_output_chars:
            session.pending = pending_combined[-session.pending_max_output_chars :]
        else:
            session.pending = pending_combined

        session.tail = session.aggregated[-2000:]
        if len(session.aggregated) < prior_len + len(chunk):
            session.truncated = True
        if session.cond:
            session.cond.notify_all()


def _mark_exited(session: Session, exit_code: int | None) -> None:
    with session.lock:
        session.exited = True
        session.exit_code = exit_code
        session.ended_at_ms = _now_ms()
        if session.timeout_timer:
            session.timeout_timer.cancel()
            session.timeout_timer = None
        if session.cond:
            session.cond.notify_all()

    with _REGISTRY_LOCK:
        _RUNNING.pop(session.id, None)
        if session.backgrounded:
            _FINISHED[session.id] = FinishedSession(
                id=session.id,
                command=session.command,
                cwd=session.cwd,
                started_at_ms=session.started_at_ms,
                ended_at_ms=session.ended_at_ms or _now_ms(),
                exit_code=session.exit_code,
                aggregated=session.aggregated,
                tail=session.tail,
                truncated=session.truncated,
            )

    if session.pty_mode and session.master_fd is not None:
        try:
            os.close(session.master_fd)
        except OSError:
            pass


def _safe_decode(data: bytes) -> str:
    return data.decode("utf-8", errors="replace")


def _spawn_pipe_readers(session: Session) -> None:
    assert session.process.stdout is not None
    assert session.process.stderr is not None

    def read_stream(stream: Any) -> None:
        try:
            while True:
                chunk = stream.read(4096)
                if not chunk:
                    return
                text = _safe_decode(chunk) if isinstance(chunk, (bytes, bytearray)) else str(chunk)
                _append_output(session, text)
        except Exception:
            return

    threading.Thread(target=read_stream, args=(session.process.stdout,), daemon=True).start()
    threading.Thread(target=read_stream, args=(session.process.stderr,), daemon=True).start()


def _spawn_pty_reader(session: Session) -> None:
    assert session.master_fd is not None

    def read_pty() -> None:
        while True:
            try:
                chunk = os.read(session.master_fd, 4096)
                if not chunk:
                    return
                _append_output(session, _safe_decode(chunk))
            except OSError:
                return

    threading.Thread(target=read_pty, daemon=True).start()


def _spawn_exit_watcher(session: Session) -> None:
    def wait_exit() -> None:
        try:
            code = session.process.wait()
        except Exception:
            code = None
        _mark_exited(session, code)

    threading.Thread(target=wait_exit, daemon=True).start()


def _kill_session_process(session: Session) -> None:
    proc = session.process
    if proc.poll() is not None:
        return

    try:
        if os.name == "posix":
            os.killpg(proc.pid, signal.SIGTERM)
        else:
            proc.terminate()
    except Exception:
        pass

    try:
        proc.wait(timeout=3)
    except Exception:
        try:
            if os.name == "posix":
                os.killpg(proc.pid, signal.SIGKILL)
            else:
                proc.kill()
        except Exception:
            pass


def _start_timeout_guard(session: Session, timeout_seconds: int) -> None:
    if timeout_seconds <= 0:
        return

    def on_timeout() -> None:
        _kill_session_process(session)

    timer = threading.Timer(timeout_seconds, on_timeout)
    timer.daemon = True
    timer.start()
    session.timeout_timer = timer


def _build_status_text(session: Session, output: str, status: str) -> str:
    exit_code = session.exit_code if session.exit_code is not None else "n/a"
    truncated = "yes" if session.truncated else "no"
    return (
        f"status: {status}\n"
        f"session_id: {session.id}\n"
        f"exit_code: {exit_code}\n"
        f"cwd: {session.cwd}\n"
        f"command: {session.command}\n"
        f"truncated: {truncated}\n"
        f"output:\n{output or '(no output)'}"
    )


def _launch_exec(
    command: str,
    run_cwd: Path,
    env: dict[str, str],
    pty_mode: bool,
) -> Session:
    session_id = uuid.uuid4().hex[:12]

    popen_kwargs: dict[str, Any] = {
        "cwd": str(run_cwd),
        "env": env,
        "shell": True,
        "executable": os.getenv("SHELL", "/bin/bash") if os.name == "posix" else None,
        "text": False,
    }

    if os.name == "posix":
        popen_kwargs["preexec_fn"] = os.setsid

    if pty_mode and os.name == "posix":
        master_fd, slave_fd = pty.openpty()
        proc = subprocess.Popen(
            command,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            close_fds=True,
            **popen_kwargs,
        )
        os.close(slave_fd)
        session = Session(
            id=session_id,
            command=command,
            cwd=str(run_cwd),
            started_at_ms=_now_ms(),
            process=proc,
            max_output_chars=DEFAULT_MAX_OUTPUT_CHARS,
            pending_max_output_chars=DEFAULT_PENDING_MAX_OUTPUT_CHARS,
            pty_mode=True,
            master_fd=master_fd,
        )
        _spawn_pty_reader(session)
        return session

    proc = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        close_fds=True,
        **popen_kwargs,
    )
    session = Session(
        id=session_id,
        command=command,
        cwd=str(run_cwd),
        started_at_ms=_now_ms(),
        process=proc,
        max_output_chars=DEFAULT_MAX_OUTPUT_CHARS,
        pending_max_output_chars=DEFAULT_PENDING_MAX_OUTPUT_CHARS,
        pty_mode=False,
        stdin_stream=proc.stdin,
    )
    _spawn_pipe_readers(session)
    return session


def exec_command(
    command: str,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    yieldMs: int | None = None,
    background: bool = False,
    timeout: int | None = None,
    pty: bool = False,
    max_output_chars: int = DEFAULT_MAX_OUTPUT_CHARS,
) -> str:
    logger.info(f"exec_command: {command}")
    normalized_command = (command or "").strip()
    
    if not normalized_command:
        return "Error: command must not be empty."

    workspace_root = _resolve_workspace_root()
    try:
        run_cwd = _resolve_cwd(workspace_root, cwd)
    except ValueError as exc:
        return f"Error: {exc}"

    timeout_seconds = _clamp_int(timeout, DEFAULT_TIMEOUT_SECONDS, 1, MAX_TIMEOUT_SECONDS)
    max_output_chars = _clamp_int(max_output_chars, DEFAULT_MAX_OUTPUT_CHARS, 1000, MAX_OUTPUT_CHARS)
    yield_ms = _clamp_int(yieldMs, DEFAULT_YIELD_MS, MIN_YIELD_MS, MAX_YIELD_MS)

    merged_env = dict(os.environ)
    if env:
        for key, value in env.items():
            merged_env[str(key)] = str(value)

    pty_mode = bool(pty and os.name == "posix")

    try:
        session = _launch_exec(
            command=normalized_command,
            run_cwd=run_cwd,
            env=merged_env,
            pty_mode=pty_mode,
        )
    except Exception as exc:  # noqa: BLE001
        return f"Error: failed to execute command: {exc}"

    session.max_output_chars = max_output_chars
    _start_timeout_guard(session, timeout_seconds)

    with _REGISTRY_LOCK:
        _RUNNING[session.id] = session

    _spawn_exit_watcher(session)

    if background:
        session.backgrounded = True
        return (
            f"status: running\n"
            f"session_id: {session.id}\n"
            f"pid: {session.process.pid}\n"
            f"cwd: {session.cwd}\n"
            f"command: {normalized_command}\n"
            f"message: Command started in background. Use process(list/poll/log/write/kill)."
        )

    deadline = time.time() + (yield_ms / 1000.0)
    with session.lock:
        while not session.exited:
            remain = deadline - time.time()
            if remain <= 0:
                break
            if session.cond:
                session.cond.wait(timeout=remain)
            else:
                break

        if session.exited:
            status = "completed" if (session.exit_code or 0) == 0 else "failed"
            text = _build_status_text(session, session.aggregated, status)
            with _REGISTRY_LOCK:
                _RUNNING.pop(session.id, None)
            return text

    session.backgrounded = True
    return (
        f"status: running\n"
        f"session_id: {session.id}\n"
        f"pid: {session.process.pid}\n"
        f"cwd: {session.cwd}\n"
        f"command: {normalized_command}\n"
        f"tail:\n{session.tail or '(no output yet)'}"
    )


def _list_sessions() -> str:
    with _REGISTRY_LOCK:
        running = [s for s in _RUNNING.values() if s.backgrounded]
        finished = list(_FINISHED.values())

    rows: list[str] = []
    now_ms = _now_ms()

    for s in sorted(running, key=lambda x: x.started_at_ms, reverse=True):
        runtime = max(0, now_ms - s.started_at_ms)
        rows.append(
            f"{s.id} running runtime_ms={runtime} cwd={s.cwd} cmd={shlex.quote(s.command)[:120]}"
        )

    for s in sorted(finished, key=lambda x: x.started_at_ms, reverse=True):
        runtime = max(0, s.ended_at_ms - s.started_at_ms)
        exit_code = s.exit_code if s.exit_code is not None else "n/a"
        status = "completed" if (s.exit_code or 0) == 0 else "failed"
        rows.append(
            f"{s.id} {status} runtime_ms={runtime} exit_code={exit_code} cwd={s.cwd} cmd={shlex.quote(s.command)[:120]}"
        )

    if not rows:
        return "No running or recent sessions."
    return "\n".join(rows)


def _find_session(session_id: str) -> tuple[Session | None, FinishedSession | None]:
    with _REGISTRY_LOCK:
        return _RUNNING.get(session_id), _FINISHED.get(session_id)


def _drain_pending(session: Session) -> str:
    with session.lock:
        chunk = session.pending
        session.pending = ""
        return chunk


def _poll_session(session: Session, timeout_ms: int) -> str:
    end_at = time.time() + (timeout_ms / 1000.0)

    with session.lock:
        while not session.pending and not session.exited:
            remain = end_at - time.time()
            if remain <= 0:
                break
            if session.cond:
                session.cond.wait(timeout=remain)
            else:
                break

    # Refresh process state to reduce race where output arrives just before exit.
    if not session.exited:
        polled = session.process.poll()
        if polled is not None:
            _mark_exited(session, polled)

    output = _drain_pending(session)
    with session.lock:
        if session.exited:
            status = "completed" if (session.exit_code or 0) == 0 else "failed"
            return _build_status_text(session, output or session.tail, status)
        return (
            f"status: running\n"
            f"session_id: {session.id}\n"
            f"pid: {session.process.pid}\n"
            f"cwd: {session.cwd}\n"
            f"command: {session.command}\n"
            f"output:\n{output or '(no new output)'}"
        )


def _log_session(session: Session, offset: int | None, limit: int | None) -> str:
    with session.lock:
        text = session.aggregated
    lines = text.splitlines()

    safe_offset = 0 if offset is None else max(0, offset)
    if limit is None or limit <= 0:
        selected = lines[safe_offset:]
    else:
        selected = lines[safe_offset : safe_offset + limit]

    payload = "\n".join(selected)
    if not payload:
        payload = "(no output)"

    return (
        f"status: ok\n"
        f"session_id: {session.id}\n"
        f"total_lines: {len(lines)}\n"
        f"offset: {safe_offset}\n"
        f"limit: {limit if limit is not None else 'all'}\n"
        f"output:\n{payload}"
    )


def _write_session(session: Session, data: str, eof: bool) -> str:
    payload = data or ""
    if session.exited:
        return f"Error: session {session.id} has already exited."

    try:
        if session.pty_mode and session.master_fd is not None:
            os.write(session.master_fd, payload.encode("utf-8"))
            if eof:
                try:
                    os.close(session.master_fd)
                except OSError:
                    pass
        else:
            if session.stdin_stream is None:
                return f"Error: stdin unavailable for session {session.id}."
            if payload:
                session.stdin_stream.write(payload)
                session.stdin_stream.flush()
            if eof:
                session.stdin_stream.close()
    except Exception as exc:  # noqa: BLE001
        return f"Error: failed to write to session {session.id}: {exc}"

    return (
        f"status: ok\n"
        f"session_id: {session.id}\n"
        f"bytes_written: {len(payload.encode('utf-8'))}\n"
        f"eof: {'yes' if eof else 'no'}"
    )


def _kill_session(session: Session) -> str:
    if session.exited:
        return f"status: ok\nsession_id: {session.id}\nmessage: already exited"
    _kill_session_process(session)
    return f"status: ok\nsession_id: {session.id}\nmessage: terminate signal sent"


def process_sessions(
    action: str,
    sessionId: str | None = None,
    data: str | None = None,
    eof: bool = False,
    offset: int | None = None,
    limit: int | None = None,
    timeout: int | None = None,
) -> str:
    logger.info(f"process_sessions: {action}")
    normalized_action = (action or "").strip().lower()
    if normalized_action == "list":
        return _list_sessions()

    if normalized_action == "clear":
        with _REGISTRY_LOCK:
            _FINISHED.clear()
        return "status: ok\nmessage: cleared finished sessions"

    if normalized_action == "remove":
        if not sessionId:
            return "Error: sessionId is required for remove."
        with _REGISTRY_LOCK:
            removed_running = _RUNNING.pop(sessionId, None)
            removed_finished = _FINISHED.pop(sessionId, None)
        if removed_running:
            return (
                f"status: ok\nsession_id: {sessionId}\n"
                "message: removed from running registry (process may still be alive if not killed)"
            )
        if removed_finished:
            return f"status: ok\nsession_id: {sessionId}\nmessage: removed"
        return f"Error: session {sessionId} not found."

    if not sessionId:
        return "Error: sessionId is required for this action."

    running, finished = _find_session(sessionId)

    if normalized_action == "poll":
        timeout_ms = _clamp_int(timeout, DEFAULT_POLL_TIMEOUT_MS, 0, MAX_POLL_TIMEOUT_MS)
        if running is not None:
            return _poll_session(running, timeout_ms)
        if finished is not None:
            status = "completed" if (finished.exit_code or 0) == 0 else "failed"
            return (
                f"status: {status}\n"
                f"session_id: {finished.id}\n"
                f"exit_code: {finished.exit_code if finished.exit_code is not None else 'n/a'}\n"
                f"cwd: {finished.cwd}\n"
                f"command: {finished.command}\n"
                f"output:\n{finished.tail or '(no output)'}"
            )
        return f"Error: session {sessionId} not found."

    if normalized_action == "log":
        if running is not None:
            return _log_session(running, offset, limit)
        if finished is not None:
            lines = finished.aggregated.splitlines()
            safe_offset = 0 if offset is None else max(0, offset)
            if limit is None or limit <= 0:
                selected = lines[safe_offset:]
            else:
                selected = lines[safe_offset : safe_offset + limit]
            payload = "\n".join(selected) or "(no output)"
            return (
                f"status: ok\n"
                f"session_id: {finished.id}\n"
                f"total_lines: {len(lines)}\n"
                f"output:\n{payload}"
            )
        return f"Error: session {sessionId} not found."

    if normalized_action == "write":
        if running is None:
            return f"Error: no active session found for {sessionId}."
        return _write_session(running, data or "", bool(eof))

    if normalized_action == "kill":
        if running is None:
            return f"Error: no active session found for {sessionId}."
        return _kill_session(running)

    return "Error: unsupported action. Use list|poll|log|write|kill|clear|remove."


TOOLS = [
    {
        "label": "bash",
        "name": "exec",
        "description": "Execute shell commands with background continuation. Supports pty/yieldMs/background.",
        "parameters": {
            "command": {
                "type": "string",
                "description": "Shell command to execute",
                "required": True,
            },
            "cwd": {
                "type": "string",
                "description": "Optional workspace-relative working directory",
            },
            "env": {
                "type": "object",
                "description": "Optional environment variable map",
            },
            "yieldMs": {
                "type": "integer",
                "description": "Milliseconds to wait before returning running status",
                "default": DEFAULT_YIELD_MS,
            },
            "background": {
                "type": "boolean",
                "description": "Run in background immediately",
                "default": False,
            },
            "timeout": {
                "type": "integer",
                "description": "Timeout in seconds",
                "default": DEFAULT_TIMEOUT_SECONDS,
            },
            "pty": {
                "type": "boolean",
                "description": "Run command in PTY mode (unix only)",
                "default": False,
            },
            "max_output_chars": {
                "type": "integer",
                "description": "Maximum buffered output characters",
                "default": DEFAULT_MAX_OUTPUT_CHARS,
            },
        },
        "execute": exec_command,
    },
    {
        "label": "bash",
        "name": "process",
        "description": "Manage exec sessions: list, poll, log, write, kill, clear, remove.",
        "parameters": {
            "action": {
                "type": "string",
                "description": "Action name: list|poll|log|write|kill|clear|remove",
                "required": True,
            },
            "sessionId": {
                "type": "string",
                "description": "Session id for actions other than list/clear",
            },
            "data": {
                "type": "string",
                "description": "Input payload for write action",
            },
            "eof": {
                "type": "boolean",
                "description": "Close stdin after write",
                "default": False,
            },
            "offset": {
                "type": "integer",
                "description": "Line offset for log action",
            },
            "limit": {
                "type": "integer",
                "description": "Line limit for log action",
            },
            "timeout": {
                "type": "integer",
                "description": "Poll wait timeout in milliseconds",
                "default": DEFAULT_POLL_TIMEOUT_MS,
            },
        },
        "execute": process_sessions,
    },
]
