import argparse
import json
import sys
from typing import Any

from app.cron.cron_manage import (
    add_cron_job,
    get_cron_jobs,
    parse_at_to_run_date,
    reload_cron_jobs,
    remove_cron_job,
    run_cron_job_now,
)


def register_cron_parser(subparsers: argparse._SubParsersAction) -> None:
    cron_parser = subparsers.add_parser("cron", help="管理定时任务")
    cron_parser.set_defaults(cron_parser=cron_parser)
    cron_subparsers = cron_parser.add_subparsers(dest="cron_action")

    add_parser = cron_subparsers.add_parser("add", help="添加定时任务")
    add_parser.add_argument("--id", dest="job_id", default="", help="任务ID，不传则自动生成")
    add_parser.add_argument("--name", required=True, help="任务名（对应注册的 task name）")
    add_parser.add_argument("--kwargs", default="{}", help="任务参数 JSON 字符串")
    add_parser.add_argument("--at", default="", help="单次执行时间（绝对时间或相对时长）")
    add_parser.add_argument("--cron", dest="cron_expr", default="", help="cron 表达式")
    add_parser.add_argument("--enabled", dest="enabled", action="store_true", default=True, help="启用任务（默认）")
    add_parser.add_argument("--disabled", dest="enabled", action="store_false", help="禁用任务")
    add_parser.add_argument("--coalesce", dest="coalesce", action="store_true", default=True, help="misfire 时合并触发（默认）")
    add_parser.add_argument("--no-coalesce", dest="coalesce", action="store_false", help="misfire 时不合并触发")
    add_parser.add_argument("--max-instances", type=int, default=1, help="最大并发实例数")
    add_parser.add_argument("--misfire-grace-time", type=int, default=60, help="错过执行允许延迟秒数")

    run_parser = cron_subparsers.add_parser("run", help="立即手动执行任务")
    run_parser.add_argument("job_id", help="任务ID")

    remove_parser = cron_subparsers.add_parser("remove", help="删除任务")
    remove_parser.add_argument("job_id", help="任务ID")

    cron_subparsers.add_parser("list", help="查看任务列表")
    cron_subparsers.add_parser("help", help="查看 cron 命令帮助")


def handle_cron_command(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    cron_parser = getattr(args, "cron_parser", parser)
    action = (args.cron_action or "").strip().lower()
    if action in {"", "help"}:
        cron_parser.print_help()
        return 0

    try:
        if action == "add":
            return _handle_add(args)

        if action == "run":
            result = run_cron_job_now(args.job_id)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0

        if action == "remove":
            ok = remove_cron_job(args.job_id)
            print(json.dumps({"ok": ok, "job_id": args.job_id}, ensure_ascii=False))
            return 0 if ok else 1

        if action == "list":
            reload_cron_jobs(force=False)
            jobs = get_cron_jobs()
            print(json.dumps(jobs, ensure_ascii=False, indent=2))
            return 0

        print(f"不支持的 cron 操作: {args.cron_action}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"cron 命令执行失败: {exc}", file=sys.stderr)
        return 1


def _handle_add(args: argparse.Namespace) -> int:
    trigger = _build_trigger(at=args.at, cron_expr=args.cron_expr)
    kwargs = _parse_json_object(args.kwargs, arg_name="--kwargs")

    job: dict[str, Any] = {
        "enabled": bool(args.enabled),
        "trigger": trigger,
        "task": {
            "name": args.name,
            "kwargs": kwargs,
        },
        "coalesce": bool(args.coalesce),
        "max_instances": int(args.max_instances),
        "misfire_grace_time": int(args.misfire_grace_time),
    }
    if args.job_id:
        job["id"] = args.job_id

    created = add_cron_job(job)
    print(json.dumps({"ok": True, "job": created}, ensure_ascii=False, indent=2))
    return 0


def _build_trigger(*, at: str, cron_expr: str) -> dict[str, Any]:
    at_value = at.strip()
    cron_value = cron_expr.strip()

    if at_value and cron_value:
        raise ValueError("--at 与 --cron 只能二选一")
    if not at_value and not cron_value:
        raise ValueError("必须提供 --at 或 --cron 之一")

    if cron_value:
        return {"type": "cron", "expression": cron_value}

    run_date = parse_at_to_run_date(at_value)
    return {"type": "date", "run_date": run_date}


def _parse_json_object(raw: str, *, arg_name: str) -> dict[str, Any]:
    text = raw.strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{arg_name} 不是合法 JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"{arg_name} 必须是 JSON 对象")
    return parsed
