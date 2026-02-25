import argparse
import asyncio
import sys
from typing import Sequence

from app.cli.chat import run_chat
from app.cli.cron import handle_cron_command, register_cron_parser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="OpenXBot 命令行")
    subparsers = parser.add_subparsers(dest="command", required=True)

    chat_parser = subparsers.add_parser("chat", help="启动聊天会话")
    chat_parser.add_argument(
        "-s",
        "--session-id",
        default=None,
        help="会话ID；不传则自动生成，可用于多轮续聊。",
    )
    chat_parser.add_argument(
        "-m",
        "--message",
        default=None,
        help="单条消息模式；传入后发送一次并退出。",
    )
    chat_parser.add_argument(
        "--no-banner",
        action="store_true",
        help="禁用启动提示。",
    )

    register_cron_parser(subparsers)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    raw_argv = list(argv) if argv is not None else sys.argv[1:]
    # Backward compatibility: treat bare args as chat command.
    if not raw_argv or (
        raw_argv[0].startswith("-") and raw_argv[0] not in {"-h", "--help"}
    ):
        raw_argv = ["chat", *raw_argv]

    parser = build_parser()
    args = parser.parse_args(raw_argv)

    try:
        if args.command == "chat":
            return asyncio.run(
                run_chat(
                    session_id=args.session_id,
                    message=args.message,
                    no_banner=args.no_banner,
                )
            )
        if args.command == "cron":
            return handle_cron_command(args, parser)

        parser.print_help()
        return 1
    finally:
        try:
            from app.workflow import close_async_components

            asyncio.run(close_async_components())
        except (RuntimeError, asyncio.CancelledError):
            # close_async_components may run after loop shutdown in short-lived paths.
            pass


if __name__ == "__main__":
    raise SystemExit(main())
