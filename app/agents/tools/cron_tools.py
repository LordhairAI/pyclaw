import json
from typing import Any

from langchain.tools import tool

from app.cron.cron_manage import (
    add_cron_job,
    get_cron_jobs,
    get_cron_tasks,
    reload_cron_jobs,
    remove_cron_job,
    run_cron_job_now,
    update_cron_job,
)


@tool
def cron(action: str, job_id: str = "", job: dict[str, Any] | None = None) -> str:
    """Manage cron jobs in cron/jobs.json.

    action: list | create | update | delete | reload | tasks | run
    job_id: required for update/delete/run
    job: job payload for create/update
    """
    op = action.strip().lower()
    try:
        if op == "list":
            return json.dumps(get_cron_jobs(), ensure_ascii=False, indent=2)

        if op == "tasks":
            return json.dumps({"tasks": get_cron_tasks()}, ensure_ascii=False)

        if op == "create":
            if not isinstance(job, dict):
                raise ValueError("job payload is required for create")
            created = add_cron_job(job)
            return json.dumps({"ok": True, "job": created}, ensure_ascii=False, indent=2)

        if op == "update":
            target = job_id.strip() or str((job or {}).get("id", "")).strip()
            if not target:
                raise ValueError("job_id is required for update")
            if not isinstance(job, dict):
                raise ValueError("job payload is required for update")
            updated = update_cron_job(target, job)
            return json.dumps({"ok": True, "job": updated}, ensure_ascii=False, indent=2)

        if op == "delete":
            target = job_id.strip()
            if not target:
                raise ValueError("job_id is required for delete")
            removed = remove_cron_job(target)
            return json.dumps({"ok": removed, "job_id": target}, ensure_ascii=False)

        if op == "reload":
            reload_cron_jobs(force=True)
            return json.dumps({"ok": True, "message": "reloaded"}, ensure_ascii=False)

        if op == "run":
            target = job_id.strip()
            if not target:
                raise ValueError("job_id is required for run")
            result = run_cron_job_now(target)
            return json.dumps(result, ensure_ascii=False)

        raise ValueError(f"unsupported action: {action}")
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)
