import sys

EXIT_COMMANDS = {"exit", "quit", "/exit", "/quit"}


async def run_chat(
    *,
    session_id: str | None = None,
    message: str | None = None,
    no_banner: bool = False,
) -> int:
    from app.channels.chat_service import invoke_chat

    if message:
        try:
            result = await invoke_chat(message, session_id=session_id)
        except Exception as exc:
            print(f"调用失败: {exc}", file=sys.stderr)
            return 1
        print(result.response)
        print(f"[session_id={result.session_id}]")
        return 0

    if not no_banner:
        print("OpenXBot CLI 已启动，输入 exit/quit 结束会话。")
        if session_id:
            print(f"当前 session_id: {session_id}")

    while True:
        try:
            user_message = input("你 > ").strip()
        except EOFError:
            print()
            break
        except KeyboardInterrupt:
            print("\n已中断。")
            break

        if not user_message:
            continue
        if user_message.lower() in EXIT_COMMANDS:
            break

        try:
            result = await invoke_chat(user_message, session_id=session_id)
        except Exception as exc:
            print(f"助手 > 调用失败: {exc}", file=sys.stderr)
            continue

        session_id = result.session_id
        print(f"助手 > {result.response}")
        print(f"[session_id={session_id}]")

    return 0
