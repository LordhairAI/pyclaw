import asyncio
import hashlib
import inspect
import json
import logging
import os
import re
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger
from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

logger = logging.getLogger("uvicorn.error")

TaskFunc = Callable[..., Any]

_DURATION_TOKEN = re.compile(r"(\d+)\s*([dhms])", re.IGNORECASE)
_ABSOLUTE_TIME_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M",
)


class _JobsFileEventHandler(FileSystemEventHandler):
    def __init__(self, manager: "CronManager"):
        self.manager = manager

    def on_modified(self, event: FileSystemEvent) -> None:
        self._maybe_reload(event)

    def on_created(self, event: FileSystemEvent) -> None:
        self._maybe_reload(event)

    def on_moved(self, event: FileSystemEvent) -> None:
        self._maybe_reload(event)

    def _maybe_reload(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return

        src = Path(getattr(event, "src_path", "")).resolve()
        dst_raw = getattr(event, "dest_path", None)
        dst = Path(dst_raw).resolve() if dst_raw else None

        target = self.manager.jobs_path.resolve()
        if src != target and dst != target:
            return
        self.manager.schedule_reload()


class CronManager:
    def __init__(self, jobs_path: Path):
        self.jobs_path = jobs_path
        self._lock = threading.RLock()
        self._scheduler = BackgroundScheduler()
        self._observer: Observer | None = None
        self._debounce_timer: threading.Timer | None = None
        self._last_hash: str | None = None
        self._started = False
        self._registry: dict[str, TaskFunc] = {}

    def start(self) -> None:
        with self._lock:
            if self._started:
                return

            self.jobs_path.parent.mkdir(parents=True, exist_ok=True)
            if not self.jobs_path.exists():
                self._write_jobs_doc({"version": 1, "jobs": []})

            self._scheduler.start()
            self._start_observer()
            self.reload_from_disk(force=True)

            self._started = True
            logger.info("Cron manager started. jobs file: %s", self.jobs_path)

    def stop(self) -> None:
        with self._lock:
            if self._debounce_timer:
                self._debounce_timer.cancel()
                self._debounce_timer = None

            if self._observer is not None:
                self._observer.stop()
                self._observer.join(timeout=2.0)
                self._observer = None

            if self._scheduler.running:
                self._scheduler.shutdown(wait=False)

            self._started = False
            logger.info("Cron manager stopped")

    def register_task(self, name: str, func: TaskFunc) -> None:
        key = name.strip()
        if not key:
            raise ValueError("task name cannot be empty")
        with self._lock:
            self._registry[key] = func

    def list_tasks(self) -> list[str]:
        with self._lock:
            return sorted(self._registry.keys())

    def list_jobs(self) -> dict[str, Any]:
        with self._lock:
            return self._read_jobs_doc()

    def create_job(self, job: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            doc = self._read_jobs_doc()
            jobs = doc.setdefault("jobs", [])
            if not isinstance(jobs, list):
                raise ValueError("jobs must be a list")

            candidate = dict(job)
            candidate.setdefault("id", str(uuid.uuid4()))
            candidate = self._normalize_job(candidate)

            if any((isinstance(item, dict) and item.get("id") == candidate["id"]) for item in jobs):
                raise ValueError(f"job already exists: {candidate['id']}")

            jobs.append(candidate)
            self._write_jobs_doc(doc)
            self.reload_from_disk(force=True)
            return candidate

    def update_job(self, job_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            doc = self._read_jobs_doc()
            jobs = doc.setdefault("jobs", [])
            if not isinstance(jobs, list):
                raise ValueError("jobs must be a list")

            for idx, item in enumerate(jobs):
                if not isinstance(item, dict) or item.get("id") != job_id:
                    continue

                merged = self._deep_merge(item, patch)
                merged["id"] = job_id
                merged = self._normalize_job(merged)
                jobs[idx] = merged
                self._write_jobs_doc(doc)
                self.reload_from_disk(force=True)
                return merged

            raise ValueError(f"job not found: {job_id}")

    def delete_job(self, job_id: str) -> bool:
        with self._lock:
            doc = self._read_jobs_doc()
            jobs = doc.setdefault("jobs", [])
            if not isinstance(jobs, list):
                raise ValueError("jobs must be a list")

            kept: list[dict[str, Any]] = []
            removed = False
            for item in jobs:
                if isinstance(item, dict) and item.get("id") == job_id:
                    removed = True
                    continue
                kept.append(item)

            if not removed:
                return False

            doc["jobs"] = kept
            self._write_jobs_doc(doc)
            self.reload_from_disk(force=True)
            return True

    def run_job_now(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            doc = self._read_jobs_doc()
            jobs = doc.setdefault("jobs", [])
            if not isinstance(jobs, list):
                raise ValueError("jobs must be a list")

            target: dict[str, Any] | None = None
            for item in jobs:
                if isinstance(item, dict) and item.get("id") == job_id:
                    target = self._normalize_job(item)
                    break

            if target is None:
                raise ValueError(f"job not found: {job_id}")

        task_name = str(target["task"]["name"])
        task_kwargs = dict(target["task"].get("kwargs") or {})
        self._execute_task(job_id=job_id, task_name=task_name, task_kwargs=task_kwargs)
        return {"ok": True, "job_id": job_id, "task": task_name}

    def schedule_reload(self, delay_seconds: float = 0.6) -> None:
        with self._lock:
            if self._debounce_timer:
                self._debounce_timer.cancel()
            self._debounce_timer = threading.Timer(delay_seconds, self._reload_safe)
            self._debounce_timer.daemon = True
            self._debounce_timer.start()

    def reload_from_disk(self, force: bool = False) -> None:
        with self._lock:
            doc = self._read_jobs_doc(raw_text=False)
            jobs = doc.get("jobs", [])
            if not isinstance(jobs, list):
                raise ValueError("jobs must be a list")

            digest = self._hash_doc(doc)
            if not force and digest == self._last_hash:
                return

            self._sync_scheduler(jobs)
            self._last_hash = digest
            logger.info("Cron jobs reloaded: %s active jobs", len(self._scheduler.get_jobs()))

    def _reload_safe(self) -> None:
        try:
            self.reload_from_disk(force=False)
        except Exception as exc:
            logger.error("Failed to reload jobs from %s: %s", self.jobs_path, exc)

    def _start_observer(self) -> None:
        if self._observer is not None:
            return
        handler = _JobsFileEventHandler(self)
        observer = Observer()
        observer.schedule(handler, str(self.jobs_path.parent), recursive=False)
        observer.daemon = True
        observer.start()
        self._observer = observer

    def _sync_scheduler(self, jobs: list[Any]) -> None:
        normalized_jobs: list[dict[str, Any]] = []
        for item in jobs:
            if not isinstance(item, dict):
                logger.warning("Ignore invalid job record (not object): %s", item)
                continue
            try:
                normalized_jobs.append(self._normalize_job(item))
            except Exception as exc:
                logger.error("Ignore invalid job '%s': %s", item.get("id", "<unknown>"), exc)

        wanted_ids = {job["id"] for job in normalized_jobs}
        for existing in self._scheduler.get_jobs():
            if existing.id not in wanted_ids:
                self._scheduler.remove_job(existing.id)

        for job in normalized_jobs:
            if not job.get("enabled", True):
                if self._scheduler.get_job(job["id"]):
                    self._scheduler.remove_job(job["id"])
                continue

            task_name = str(job["task"]["name"])
            kwargs = dict(job["task"].get("kwargs") or {})
            trigger = self._build_trigger(job["trigger"])

            opts = {
                "id": job["id"],
                "replace_existing": True,
                "coalesce": bool(job.get("coalesce", True)),
                "max_instances": int(job.get("max_instances", 1)),
                "misfire_grace_time": int(job.get("misfire_grace_time", 60)),
            }

            self._scheduler.add_job(
                self._execute_task,
                trigger=trigger,
                kwargs={"job_id": job["id"], "task_name": task_name, "task_kwargs": kwargs},
                **opts,
            )

    def _execute_task(self, job_id: str, task_name: str, task_kwargs: dict[str, Any]) -> None:
        with self._lock:
            func = self._registry.get(task_name)

        if func is None:
            logger.error("Cron job '%s' skipped. task not registered: %s", job_id, task_name)
            return

        try:
            if hasattr(func, "invoke") and callable(getattr(func, "invoke")):
                result = func.invoke(task_kwargs)
            else:
                result = func(**task_kwargs)
            if inspect.isawaitable(result):
                asyncio.run(result)
            logger.info("Cron job '%s' executed task '%s'", job_id, task_name)
        except Exception as exc:
            logger.exception("Cron job '%s' failed on task '%s': %s", job_id, task_name, exc)

    def _build_trigger(self, trigger_spec: dict[str, Any]):
        trigger_type = str(trigger_spec.get("type", "")).strip().lower()
        if trigger_type == "cron":
            expr = trigger_spec.get("expression")
            if expr:
                return CronTrigger.from_crontab(str(expr))

            kwargs = dict(trigger_spec)
            kwargs.pop("type", None)
            return CronTrigger(**kwargs)

        if trigger_type == "interval":
            kwargs = dict(trigger_spec)
            kwargs.pop("type", None)
            return IntervalTrigger(**kwargs)

        if trigger_type == "date":
            run_date = trigger_spec.get("run_date")
            if not run_date:
                raise ValueError("date trigger requires run_date")
            return DateTrigger(run_date=run_date)

        raise ValueError(f"unsupported trigger type: {trigger_type}")

    def _normalize_job(self, job: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(job)

        job_id = str(normalized.get("id", "")).strip()
        if not job_id:
            raise ValueError("job.id is required")
        normalized["id"] = job_id
        normalized["enabled"] = bool(normalized.get("enabled", True))

        trigger = normalized.get("trigger")
        if not isinstance(trigger, dict):
            if normalized.get("cron"):
                trigger = {"type": "cron", "expression": normalized["cron"]}
            elif normalized.get("run_date"):
                trigger = {"type": "date", "run_date": normalized["run_date"]}
            elif normalized.get("interval_seconds"):
                trigger = {"type": "interval", "seconds": normalized["interval_seconds"]}
            else:
                raise ValueError("job.trigger is required")
        normalized["trigger"] = trigger

        task = normalized.get("task")
        if not isinstance(task, dict):
            raise ValueError("job.task is required")
        task_name = str(task.get("name", "")).strip()
        if not task_name:
            raise ValueError("job.task.name is required")
        task_kwargs = task.get("kwargs") or {}
        if not isinstance(task_kwargs, dict):
            raise ValueError("job.task.kwargs must be an object")
        normalized["task"] = {"name": task_name, "kwargs": task_kwargs}

        return normalized

    def _read_jobs_doc(self, raw_text: bool = True) -> dict[str, Any]:
        if raw_text:
            raw = self.jobs_path.read_text(encoding="utf-8")
            if not raw.strip():
                return {"version": 1, "jobs": []}
            doc = json.loads(raw)
        else:
            with self.jobs_path.open("r", encoding="utf-8") as f:
                doc = json.load(f)

        if not isinstance(doc, dict):
            raise ValueError("jobs.json root must be an object")
        doc.setdefault("version", 1)
        doc.setdefault("jobs", [])
        return doc

    def _write_jobs_doc(self, doc: dict[str, Any]) -> None:
        tmp_path = self.jobs_path.with_suffix(".json.tmp")
        payload = json.dumps(doc, ensure_ascii=False, indent=2)
        tmp_path.write_text(payload + "\n", encoding="utf-8")
        tmp_path.replace(self.jobs_path)

    @staticmethod
    def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
        out = dict(base)
        for key, value in patch.items():
            if isinstance(value, dict) and isinstance(out.get(key), dict):
                out[key] = CronManager._deep_merge(out[key], value)
            else:
                out[key] = value
        return out

    @staticmethod
    def _hash_doc(doc: dict[str, Any]) -> str:
        stable = json.dumps(doc, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(stable.encode("utf-8")).hexdigest()


WORKSPACE_ROOT = Path(os.getenv("WORKSPACE_ROOT") or Path.cwd())
JOBS_PATH = WORKSPACE_ROOT / "cron" / "jobs.json"

_cron_manager = CronManager(JOBS_PATH)


def _task_log(message: str = "cron event", level: str = "info") -> str:
    level_name = level.lower().strip()
    log_method = getattr(logger, level_name, logger.info)
    log_method("[cron.log] %s", message)
    return message


def register_cron_task(name: str, func: TaskFunc) -> None:
    _cron_manager.register_task(name, func)


def shutdown_cron_manager() -> None:
    _cron_manager.stop()


def start_cron_manager() -> None:
    _cron_manager.start()


def reload_cron_jobs(force: bool = True) -> None:
    _cron_manager.start()
    _cron_manager.reload_from_disk(force=force)


def get_cron_jobs() -> dict[str, Any]:
    _cron_manager.start()
    return _cron_manager.list_jobs()


def get_cron_tasks() -> list[str]:
    _cron_manager.start()
    return _cron_manager.list_tasks()


def add_cron_job(job: dict[str, Any]) -> dict[str, Any]:
    _cron_manager.start()
    return _cron_manager.create_job(job)


def update_cron_job(job_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    _cron_manager.start()
    return _cron_manager.update_job(job_id, patch)


def remove_cron_job(job_id: str) -> bool:
    _cron_manager.start()
    return _cron_manager.delete_job(job_id)


def run_cron_job_now(job_id: str) -> dict[str, Any]:
    _cron_manager.start()
    return _cron_manager.run_job_now(job_id)


def parse_at_to_run_date(at_value: str, now: datetime | None = None) -> str:
    raw = at_value.strip()
    if not raw:
        raise ValueError("--at cannot be empty")

    for fmt in _ABSOLUTE_TIME_FORMATS:
        try:
            dt = datetime.strptime(raw, fmt)
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue

    matched = list(_DURATION_TOKEN.finditer(raw))
    if not matched:
        raise ValueError("invalid --at format, use 'YYYY-MM-DD HH:MM:SS' or like '1h 20m 5s'")

    consumed = "".join(m.group(0) for m in matched).replace(" ", "").lower()
    compact_raw = raw.replace(" ", "").lower()
    if consumed != compact_raw:
        raise ValueError("invalid --at duration expression")

    total_seconds = 0
    for m in matched:
        amount = int(m.group(1))
        unit = m.group(2).lower()
        if unit == "d":
            total_seconds += amount * 86400
        elif unit == "h":
            total_seconds += amount * 3600
        elif unit == "m":
            total_seconds += amount * 60
        elif unit == "s":
            total_seconds += amount

    if total_seconds <= 0:
        raise ValueError("--at duration must be greater than 0")

    base = now or datetime.now()
    run_date = base + timedelta(seconds=total_seconds)
    return run_date.strftime("%Y-%m-%d %H:%M:%S")


def bootstrap_cron_tasks() -> None:
    register_cron_task("log", _task_log)
    try:
        from app.agents.tools.fetch_url import fetch_url as _fetch_url_tool

        register_cron_task("fetch_url", _fetch_url_tool)
    except Exception as exc:
        logger.warning("Failed to register built-in cron task 'fetch_url': %s", exc)


bootstrap_cron_tasks()
