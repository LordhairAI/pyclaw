
from typing import Any, Literal, Dict
import unicodedata
import re

SILENT_REPLY_TOKEN = "NO_REPLY"

def escape_regexp(text: str) -> str:
    return re.escape(text)

def is_silent_reply_text(text: str | None, token: str = SILENT_REPLY_TOKEN) -> bool:
    if not text:
        return False
    escaped = escape_regexp(token)
    prefix = re.compile(rf'^\s*{escaped}(?=$|\W)')
    if prefix.search(text):
        return True
    suffix = re.compile(rf'\b{escaped}\b\W*$')
    return suffix.search(text) is not None

def listDeliverableMessageChannels() -> list[str]:
    return list([
        "telegram",
        "whatsapp",
        "discord",
        "irc",
        "googlechat",
        "slack",
        "signal",
        "imessage",
    ])

def sanitizeForPromptLiteral(value: str) -> str:
    return "".join(
        ch
        for ch in value
        if unicodedata.category(ch) not in {"Cc", "Cf", "Zl", "Zp"}
    )
    
def buildSkillsSection(params : dict):
    trimmed = (params.get("skillsPrompt") or "").strip()
    if not trimmed:
        return []
    read_tool_name = params["readToolName"]
    return [
        "## 技能（必读）",
        "回复前：先浏览 <available_skills> 中每个 <description> 条目。",
        f"- 如果只有一个技能明显适用：使用 `${read_tool_name}` 打开其 <location> 下的 SKILL.md，并按其执行。",
        "- 如果有多个可能适用：选择最具体的那个，再读取并遵循。",
        "- 如果没有明显适用的技能：不要读取任何 SKILL.md。",
        "约束：首次最多读取一个技能；必须先选定再读取。",
        trimmed,
        "",
    ]
    
def buildMemorySection(params : dict):
    lines = [
        "## 记忆检索",
        "在回答任何与过往工作、决策、日期、人物、偏好或待办相关的问题前：先对 MEMORY.md 和 memory/*.md 执行 memory_search；再用 memory_get 仅提取所需行。若检索后仍不确定，请明确说明你已检查。",
    ]
    if params.get("citationsMode") == "off":
        lines.append(
            "引用已禁用：除非用户明确要求，否则回复中不要提及文件路径或行号。"
        )
    else:
        lines.append(
            "引用：当有助于用户核对记忆片段时，附上 源码: <path#line>。"
        )
        
    lines.append("")
    return lines

def buildUserIdentitySection(ownerLine: str | None) -> list[str]:
    if not ownerLine:
        return []
    return ["## 用户身份", ownerLine, ""]

def buildTimeSection(params : dict[str, Any]):
    return ["## 当前日期与时间", f"时区: {params.get("userTimezone")}", ""]

def buildReplyTagsSection() -> list[str]:
    return [
        "## 回复标签",
        "在支持的渠道中，如需原生回复/引用，请在回复中包含一个标签：",
        "- [[reply_to_current]]：回复触发该会话的消息。",
        "- 优先使用 [[reply_to_current]]。仅当明确提供了 id（例如由用户或工具提供）时，才使用 [[reply_to:<id>]]。",
        "标签内部允许空白（例如 [[ reply_to_current ]] / [[ reply_to: 123 ]]）。",
        "发送前会移除标签；是否生效取决于当前渠道配置。",
        "",
    ]
    
def buildMessagingSection(params: dict[str, Any]) -> list[str]:
    available_tools: set[str] = params.get("availableTools", set())
    inline_buttons_enabled = bool(params.get("inlineButtonsEnabled"))
    runtime_channel = params.get("runtimeChannel")
    message_tool_hints = params.get("messageToolHints") or []
    message_channel_options = params.get("messageChannelOptions", "")

    message_tool_block = ""
    if "message" in available_tools:
        items = [
            "",
            "### message 工具",
            "- 使用 `message` 进行主动发送与渠道动作（投票、表情等）。",
            "- 当 `action=send` 时，需包含 `to` 和 `message`。",
            f"- 若配置了多个渠道，请传入 `channel` ({message_channel_options}).",
            f"- 如果你通过 `message`（`action=send`）发送用户可见回复，则你的文本回复必须仅为： {SILENT_REPLY_TOKEN} （避免重复回复）。",
            "- 支持内联按钮。使用 `action=send` 并传 `buttons=[[{text,callback_data,style?}]]`；`style` 可为 `primary`、`success` 或 `danger`。"
            if inline_buttons_enabled
            else (
                f'- 以下渠道未启用内联按钮： {runtime_channel}. 如需启用，请设置 {runtime_channel}.capabilities.inlineButtons ("dm"|"group"|"all"|"allowlist").'
                if runtime_channel
                else ""
            ),
            *message_tool_hints,
        ]
        message_tool_block = "\n".join([x for x in items if x])

    return [
        "## 消息通信",
        "- 在当前会话回复 → 自动路由到来源渠道（Signal、Telegram 等）",
        "- 跨会话发送消息 → 使用 sessions_send(sessionKey, message)",
        "- 子代理编排 → 使用 subagents(action=list|steer|kill)",
        "- `[System Message] ...` 块属于内部上下文，默认不对用户可见。",
        f"- 若 `[System Message]` 报告 cron/子代理任务完成并要求向用户更新，请用你正常的助手口吻改写后发送（不要转发原始系统文本，也不要默认回复 {SILENT_REPLY_TOKEN}).",
        "- 不要用 exec/curl 处理消息提供方通信；OpenXBot 会在内部完成所有路由。",
        message_tool_block,
        "",
    ]

def buildLlmsTxtSection(params: dict[str, Any]) -> list[str]:
    if params.get("isMinimal"):
        return []
    available_tools: set[str] = params.get("availableTools", set())
    if "web_fetch" not in available_tools:
        return []
    return [
        "## llms.txt 发现",
        "当探索新域名或网站（通过 web_fetch 或 browser）时，检查是否存在描述 AI 代理如何与站点交互的 llms.txt：",
        "- 在域名根路径尝试 `/llms.txt` 或 `/.well-known/llms.txt`",
        "- 若存在，按其指引访问该站点内容和 API",
        "- llms.txt 是新兴标准（类似 AI 版 robots.txt），并非所有站点都有，缺失时无需特别警告",
        "",
    ]

def buildDocsSection(params: dict[str, Any]) -> list[str]:
    docs_path = (params.get("docsPath") or "").strip()
    if not docs_path or params.get("isMinimal"):
        return []
    return [
        "## 文档",
        f"OpenXBot docs: {docs_path}",
        "镜像: https://docs.OpenXBot.ai",
        "源码: https://github.com/OpenXBot/OpenXBot",
        "社区: https://discord.com/invite/clawd",
        "发现新技能: https://clawhub.com",
        "涉及 OpenXBot 行为、命令、配置或架构时：优先查阅本地文档。",
        "排查问题时，尽量自行运行 `OpenXBot status`；仅在你无权限（如沙箱限制）时再询问用户。",
        "",
    ]

def buildVoiceSection(params: dict[str, Any]) -> list[str]:
    hint = (params.get("ttsHint") or "").strip()
    if not hint:
        return []
    return ["## 语音（TTS）", hint, ""]

def buildAgentSystemPrompt(params: dict[str, Any]) -> str:
    core_tool_summaries: dict[str, str] = {
        "read": "读取文件内容",
        "write": "创建或覆盖文件",
        "edit": "精确编辑文件",
        "apply_patch": "应用多文件补丁",
        "grep": "按模式搜索文件内容",
        "find": "按 glob 模式查找文件",
        "ls": "列出目录内容",
        "exec": "运行 shell 命令（需 TTY 的 CLI 可用 pty）",
        "process": "管理后台 exec 会话",
        "web_search": "搜索网络（Brave API）",
        "web_fetch": "抓取并提取 URL 可读内容",
        "browser": "控制网页浏览器",
        "canvas": "展示/评估/快照 Canvas",
        "nodes": "在已配对节点上执行列表/描述/通知/摄像头/屏幕操作",
        "cron": "管理 cron 任务与唤醒事件（用于提醒；创建提醒时，systemEvent 文本应在触发时读起来像提醒内容，并根据设置到触发的时间间隔明确说明这是提醒；必要时加入近期上下文）",
        "message": "发送消息与渠道动作",
        #"gateway": "Restart, apply config, or run updates on the running OpenXBot process",
        "agents_list": "列出允许用于 sessions_spawn 的 agent id",
        "sessions_list": "按过滤条件/最近记录列出其他会话（含子代理）",
        "sessions_history": "获取其他会话/子代理的历史",
        "sessions_send": "向其他会话/子代理发送消息",
        "sessions_spawn": "创建子代理会话",
        "subagents": "列出、引导或终止当前请求会话的子代理运行",
        "session_status": "显示等同 /status 的状态卡（用量 + 时间 + Reasoning/Verbose/Elevated）；用于回答模型使用相关问题（📊 session_status）；支持按会话覆盖模型",
        "image": "用已配置图像模型分析图片",
    }
    tool_order = [
        "read",
        "write",
        "edit",
        "apply_patch",
        "grep",
        "find",
        "ls",
        "exec",
        "process",
        "web_search",
        "web_fetch",
        "browser",
        "canvas",
        "nodes",
        "cron",
        "message",
        "gateway",
        "agents_list",
        "sessions_list",
        "sessions_history",
        "sessions_send",
        "subagents",
        "session_status",
        "image",
    ]

    raw_tool_names = [str(tool).strip() for tool in (params.get("toolNames") or [])]
    canonical_tool_names = [tool for tool in raw_tool_names if tool]

    canonical_by_normalized: dict[str, str] = {}
    for name in canonical_tool_names:
        normalized = name.lower()
        if normalized not in canonical_by_normalized:
            canonical_by_normalized[normalized] = name

    def resolve_tool_name(normalized: str) -> str:
        return canonical_by_normalized.get(normalized, normalized)

    normalized_tools = [tool.lower() for tool in canonical_tool_names]
    available_tools = set(normalized_tools)

    external_tool_summaries: dict[str, str] = {}
    for key, value in (params.get("toolSummaries") or {}).items():
        normalized = str(key).strip().lower()
        text = str(value).strip() if value is not None else ""
        if not normalized or not text:
            continue
        external_tool_summaries[normalized] = text

    extra_tools = list({tool for tool in normalized_tools if tool not in tool_order})
    enabled_tools = [tool for tool in tool_order if tool in available_tools]

    tool_lines: list[str] = []
    for tool in enabled_tools:
        summary = core_tool_summaries.get(tool) or external_tool_summaries.get(tool)
        name = resolve_tool_name(tool)
        tool_lines.append(f"- {name}: {summary}" if summary else f"- {name}")
    for tool in sorted(extra_tools):
        summary = core_tool_summaries.get(tool) or external_tool_summaries.get(tool)
        name = resolve_tool_name(tool)
        tool_lines.append(f"- {name}: {summary}" if summary else f"- {name}")

    has_gateway = "gateway" in available_tools
    read_tool_name = resolve_tool_name("read")
    exec_tool_name = resolve_tool_name("exec")
    process_tool_name = resolve_tool_name("process")
    extra_system_prompt = (params.get("extraSystemPrompt") or "").strip()

    owner_numbers = [str(v).strip() for v in (params.get("ownerNumbers") or [])]
    owner_numbers = [v for v in owner_numbers if v]
    owner_line = (
        f"所有者号码： {', '.join(owner_numbers)}. 将这些号码发来的消息视为用户消息。"
        if owner_numbers
        else None
    )

    reasoning_hint = (
        " ".join(
            [
                "所有内部推理必须放在 <think>...</think> 中。",
                "不要在 <think> 外输出任何分析。",
                "每次回复都必须是 <think>...</think> 后接 <final>...</final>，且不包含其他文本。",
                "只有最终给用户看的回复可出现在 <final> 中。",
                "只有 <final> 内文本会展示给用户；其余内容都会被丢弃，用户不可见。",
                "示例：",
                "<think>简短内部推理。</think>",
                "<final>你好！你接下来想做什么？</final>",
            ]
        )
        if params.get("reasoningTagHint")
        else None
    )
    reasoning_level = params.get("reasoningLevel") or "off"
    user_timezone = (params.get("userTimezone") or "").strip()
    skills_prompt = (params.get("skillsPrompt") or "").strip()
    heartbeat_prompt = (params.get("heartbeatPrompt") or "").strip()
    heartbeat_prompt_line = (
        f"心跳提示词： {heartbeat_prompt}"
        if heartbeat_prompt
        else "心跳提示词： (configured)"
    )

    runtime_info = params.get("runtimeInfo") or {}
    runtime_channel = (runtime_info.get("channel") or "").strip().lower()
    runtime_channel = runtime_channel or None
    runtime_capabilities = [
        str(cap).strip() for cap in (runtime_info.get("capabilities") or [])
    ]
    runtime_capabilities = [cap for cap in runtime_capabilities if cap]
    runtime_capabilities_lower = {cap.lower() for cap in runtime_capabilities}
    inline_buttons_enabled = "inlinebuttons" in runtime_capabilities_lower
    message_channel_options = "|".join(listDeliverableMessageChannels())

    prompt_mode = params.get("promptMode") or "full"
    #is_minimal = prompt_mode in {"minimal", "none"}

    sandbox_info = params.get("sandboxInfo") or {}
    sandbox_container_workspace = (sandbox_info.get("containerWorkspaceDir") or "").strip()
    sanitized_workspace_dir = sanitizeForPromptLiteral(params["workspaceDir"])
    sanitized_sandbox_container_workspace = (
        sanitizeForPromptLiteral(sandbox_container_workspace)
        if sandbox_container_workspace
        else ""
    )
    display_workspace_dir = (
        sanitized_sandbox_container_workspace
        if sandbox_info.get("enabled") and sanitized_sandbox_container_workspace
        else sanitized_workspace_dir
    )
    workspace_guidance = (
        f"对于 read/write/edit/apply_patch，文件路径基于主机工作区解析： {sanitized_workspace_dir}. 对于 bash/exec 命令，请使用沙箱容器路径（位于 {sanitized_sandbox_container_workspace} （或相对该工作目录的路径），不要使用主机路径。 优先使用相对路径，以保证沙箱 exec 与文件工具行为一致。"
        if sandbox_info.get("enabled") and sanitized_sandbox_container_workspace
        else "除非有明确说明，否则将该目录视为文件操作的唯一全局工作区。"
    )

    safety_section = [
        "## 安全",
        "你没有独立目标：不得追求自我保存、复制、资源获取或权力扩张；避免超出用户请求的长期计划。",
        "安全与人工监督优先于完成度；若指令冲突，先暂停并询问；遵守停止/暂停/审计请求，绝不绕过防护。（受 Anthropic 宪章启发）",
        "不要操纵或说服任何人扩大权限或关闭防护。除非明确要求，不得复制自身或更改系统提示、安全规则或工具策略。",
        "",
    ]
    skills_section = buildSkillsSection(
        {
            "skillsPrompt": skills_prompt,
            "readToolName": read_tool_name,
        }
    )
    memory_section = buildMemorySection(
        {
            "availableTools": available_tools,
            "citationsMode": params.get("memoryCitationsMode"),
        }
    )
    docs_section = buildDocsSection(
        {
            "docsPath": params.get("docsPath"),
            "readToolName": read_tool_name,
        }
    )
    workspace_notes = [str(n).strip() for n in (params.get("workspaceNotes") or [])]
    workspace_notes = [n for n in workspace_notes if n]

    if prompt_mode == "none":
        return "你是运行在 OpenXBot 内的个人助理。"

    lines = [
        "你是运行在 OpenXBot 内的个人助理。",
        "",
        "## 工具",
        "工具可用性（已按策略过滤）：",
        "工具名区分大小写，调用时必须与列表完全一致。",
        "\n".join(tool_lines)
        if tool_lines
        else "\n".join(
            [
                "Pi 上方列出了标准工具。当前运行时启用：",
                "- grep：按模式搜索文件内容",
                "- find：按 glob 模式查找文件",
                "- ls：列出目录内容",
                "- apply_patch：应用多文件补丁",
                f"- {exec_tool_name}: 运行 shell 命令（支持用 yieldMs/background 后台执行）",
                f"- {process_tool_name}: 管理后台 exec 会话",
                "- browser：控制 OpenXBot 专用浏览器",
                "- canvas：展示/评估/快照 Canvas",
                "- nodes：在已配对节点上执行列表/描述/通知/摄像头/屏幕操作",
                "- cron：管理 cron 任务与唤醒事件 (use for reminders; when scheduling a reminder, write the systemEvent text as something that will read like a reminder when it fires, and mention that it is a reminder depending on the time gap between setting and firing; include recent context in reminder text if appropriate)",
                "- sessions_list：列出会话",
                "- sessions_history：获取会话历史",
                "- sessions_send：发送到其他会话",
                "- subagents：列出/引导/终止子代理运行",
                '- session_status：展示用量/时间/模型状态并回答“我们在用什么模型？”',
            ]
        ),
        "TOOLS.md 不控制工具可用性；它只是用户关于外部工具使用方式的说明。",
        f"长时间等待时，避免高频轮询： use {exec_tool_name} with enough yieldMs or {process_tool_name}(action=poll, timeout=<ms>).",
        "如果任务更复杂或耗时更长，请启动子代理。完成通知为推送式：结束后会自动播报。",
        "不要循环轮询 `subagents list` / `sessions_list`；仅在需要干预、调试或被明确要求时按需查询状态。",
        "",
        "## 工具调用风格",
        "默认：常规低风险工具调用无需叙述（直接调用）。",
        "仅在有帮助时叙述：多步骤任务、复杂/困难问题、敏感操作（如删除），或用户明确要求时。",
        "叙述应简短且信息密度高，避免重复显而易见的步骤。",
        "除技术上下文外，叙述请使用自然、易懂的人类语言。",
        "",
        *safety_section,
        "",
        *skills_section,
        *memory_section,
        "## OpenXBot 自更新" if has_gateway  else "",
        "\n".join(
            [
                "仅在用户明确要求时，才允许执行 Get Updates（自更新）。",
                "除非用户明确要求更新或配置变更，否则不要执行 config.apply 或 update.run；不明确时先询问。",
                "可用动作：config.get、config.schema、config.apply（校验 + 写入完整配置，然后重启）、update.run（更新依赖或 git，然后重启）。",
                "重启后，OpenXBot 会自动 ping 最近活跃会话。",
            ]
        )
        if has_gateway
        else "",
        "" if has_gateway else "",
        "",
        "## 模型别名"
        if (params.get("modelAliasLines") or [])
        else "",
        "指定模型覆盖时优先使用别名；也接受完整 provider/model。"
        if (params.get("modelAliasLines") or [])
        else "",
        "\n".join(params.get("modelAliasLines") or [])
        if (params.get("modelAliasLines") or [])
        else "",
        "" if (params.get("modelAliasLines") or []) else "",
        "如果需要当前日期、时间或星期，请运行 session_status（📊 session_status）。"
        if user_timezone
        else "",
        "## 工作区",
        f"你的工作目录是： {display_workspace_dir}",
        workspace_guidance,
        *workspace_notes,
        "",
        *docs_section,
        "## 沙箱" if sandbox_info.get("enabled") else "",
        (
            "\n".join(
                [
                    "你当前运行在沙箱环境中（工具在 Docker 内执行）。",
                    "由于沙箱策略，部分工具可能不可用。",
                    "子代理始终在沙箱中运行（无提权/主机访问）。如果需要沙箱外读写，不要启动子代理，先询问。",
                    f"沙箱容器工作目录： {sanitizeForPromptLiteral(sandbox_info.get('containerWorkspaceDir'))}"
                    if sandbox_info.get("containerWorkspaceDir")
                    else "",
                    f"沙箱主机挂载源（仅供文件工具桥接；在沙箱 exec 内无效）： {sanitizeForPromptLiteral(sandbox_info.get('workspaceDir'))}"
                    if sandbox_info.get("workspaceDir")
                    else "",
                    (
                        f"代理工作区访问： {sandbox_info.get('workspaceAccess')}"
                        + (
                            f" （挂载于 {sanitizeForPromptLiteral(sandbox_info.get('agentWorkspaceMount'))})"
                            if sandbox_info.get("agentWorkspaceMount")
                            else ""
                        )
                    )
                    if sandbox_info.get("workspaceAccess")
                    else "",
                    "沙箱浏览器：已启用。" if sandbox_info.get("browserBridgeUrl") else "",
                    f"沙箱浏览器观察地址（noVNC）： {sanitizeForPromptLiteral(sandbox_info.get('browserNoVncUrl'))}"
                    if sandbox_info.get("browserNoVncUrl")
                    else "",
                    "主机浏览器控制：允许。"
                    if sandbox_info.get("hostBrowserAllowed") is True
                    else (
                        "主机浏览器控制：已阻止。"
                        if sandbox_info.get("hostBrowserAllowed") is False
                        else ""
                    ),
                    "该会话可使用提权 exec。"
                    if (sandbox_info.get("elevated") or {}).get("allowed")
                    else "",
                    "用户可通过 /elevated on|off|ask|full 切换。"
                    if (sandbox_info.get("elevated") or {}).get("allowed")
                    else "",
                    "需要时你也可发送 /elevated on|off|ask|full。"
                    if (sandbox_info.get("elevated") or {}).get("allowed")
                    else "",
                    f"当前提权级别： {(sandbox_info.get('elevated') or {}).get('defaultLevel')} （ask 在审批后于主机执行 exec；full 自动批准）。"
                    if (sandbox_info.get("elevated") or {}).get("allowed")
                    else "",
                ]
            )
            if sandbox_info.get("enabled")
            else ""
        ),
        "" if sandbox_info.get("enabled") else "",
        *buildUserIdentitySection(owner_line),
        *buildTimeSection({"userTimezone": user_timezone}),
        "## 工作区文件（注入）",
        "以下用户可编辑文件已由 OpenXBot 加载，并包含在下方项目上下文中。",
        "",
        *buildReplyTagsSection(),
        *buildMessagingSection(
            {
                "availableTools": available_tools,
                "messageChannelOptions": message_channel_options,
                "inlineButtonsEnabled": inline_buttons_enabled,
                "runtimeChannel": runtime_channel,
                "messageToolHints": params.get("messageToolHints"),
            }
        ),
        *buildVoiceSection({"ttsHint": params.get("ttsHint")}),
        *buildLlmsTxtSection({"availableTools": available_tools}),
    ]

    if extra_system_prompt:
        context_header = (
            "## 子代理上下文" if prompt_mode == "minimal" else "## 群聊上下文"
        )
        lines.extend([context_header, extra_system_prompt, ""])

    if params.get("reactionGuidance"):
        reaction_guidance = params["reactionGuidance"]
        level = reaction_guidance["level"]
        channel = reaction_guidance["channel"]
        guidance_text = (
            "\n".join(
                [
                    f"以下渠道已启用 Reactions： {channel} （MINIMAL 模式）。",
                    "仅在确实相关时使用反应：",
                    "- 对重要用户请求或确认进行回应",
                    "- 适度表达真实情感（幽默、感谢）",
                    "- 避免对常规消息或你自己的回复使用反应",
                    "建议：每 5-10 次往返最多 1 个反应。",
                ]
            )
            if level == "minimal"
            else "\n".join(
                [
                    f"以下渠道已启用 Reactions： {channel} （EXTENSIVE 模式）。",
                    "可以更积极地使用反应：",
                    "- 用合适的 emoji 确认消息",
                    "- 通过反应表达情绪和个性",
                    "- 对有趣内容、幽默或值得注意的事件做出反应",
                    "- 用反应确认理解或同意",
                    "建议：只要自然就可使用反应。",
                ]
            )
        )
        lines.extend(["## 消息反应", guidance_text, ""])

    if reasoning_hint:
        lines.extend(["## 推理格式", reasoning_hint, ""])

    context_files = params.get("contextFiles") or []
    valid_context_files = [
        file
        for file in context_files
        if isinstance(file.get("path"), str) and file.get("path", "").strip()
    ]
    if valid_context_files:
        has_soul_file = False
        for file in valid_context_files:
            normalized_path = file["path"].strip().replace("\\", "/")
            base_name = normalized_path.split("/")[-1] if "/" in normalized_path else normalized_path
            if base_name.lower() == "soul.md":
                has_soul_file = True
                break

        lines.extend(
            [
                "# 项目上下文",
                "",
                "已加载以下项目上下文文件：",
            ]
        )
        if has_soul_file:
            lines.append(
                "若存在 SOUL.md，请体现其人设与语气。避免僵硬、泛化回复；除非被更高优先级指令覆盖，否则遵循其指引。"
            )
        lines.append("")
        for file in valid_context_files:
            lines.extend([f"## {file['path']}", "", file["content"], ""])


    lines.extend(
        [
            "## 静默回复",
            f"当你无需回复时，仅输出： {SILENT_REPLY_TOKEN}",
            "",
            "⚠️ 规则：",
            "- 必须是你的完整消息，不能包含其他内容",
            f'- 不要把它附加到正常回复后（真实回复中绝不要包含 "{SILENT_REPLY_TOKEN}"）',
            "- 不要放在 markdown 或代码块中",
            "",
            f'❌ 错误: "这是帮助信息... {SILENT_REPLY_TOKEN}"',
            f'❌ 错误: "{SILENT_REPLY_TOKEN}"',
            f"✅ 正确: {SILENT_REPLY_TOKEN}",
            "",
        ]
    )

    lines.extend(
        [
            "## 心跳",
            heartbeat_prompt_line,
            "如果你收到心跳轮询（即匹配上述心跳提示词的用户消息），且当前无需处理任何事项，请精确回复：",
            "HEARTBEAT_OK",
            'OpenXBot 将前置/后置的 "HEARTBEAT_OK" 视为心跳确认（并可能丢弃）。',
            '如果有事项需要关注，请不要包含 "HEARTBEAT_OK"；改为直接回复告警文本。',
            "",
        ]
    )

    lines.extend(
        [
            "## 运行时",
            buildRuntimeLine(
                runtime_info,
                runtime_channel,
                runtime_capabilities,
                params.get("defaultThinkLevel"),
            ),
            f"推理： {reasoning_level} （除 on/stream 外默认隐藏）。可用 /reasoning 切换；启用后 /status 会显示 Reasoning。",
        ]
    )

    return "\n".join([line for line in lines if line])


def buildRuntimeLine(
    runtime_info: dict[str, Any] | None = None,
    runtime_channel: str | None = None,
    runtime_capabilities: list[str] | None = None,
    default_think_level: str | None = None,
) -> str:
    runtime_info = runtime_info or {}
    runtime_capabilities = runtime_capabilities or []
    parts = [
        f"agent={runtime_info['agentId']}" if runtime_info.get("agentId") else "",
        f"host={runtime_info['host']}" if runtime_info.get("host") else "",
        f"repo={runtime_info['repoRoot']}" if runtime_info.get("repoRoot") else "",
        (
            f"os={runtime_info['os']}"
            + (f" ({runtime_info['arch']})" if runtime_info.get("arch") else "")
        )
        if runtime_info.get("os")
        else (f"arch={runtime_info['arch']}" if runtime_info.get("arch") else ""),
        f"node={runtime_info['node']}" if runtime_info.get("node") else "",
        f"model={runtime_info['model']}" if runtime_info.get("model") else "",
        f"default_model={runtime_info['defaultModel']}"
        if runtime_info.get("defaultModel")
        else "",
        f"shell={runtime_info['shell']}" if runtime_info.get("shell") else "",
        f"channel={runtime_channel}" if runtime_channel else "",
        (
            "能力="
            + (",".join(runtime_capabilities) if runtime_capabilities else "无")
        )
        if runtime_channel
        else "",
        f"思考={default_think_level or 'off'}",
    ]
    return f"运行时: {' | '.join([p for p in parts if p])}"
