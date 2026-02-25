# OpenXBot

基于 FastAPI + LangGraph 的可扩展智能体服务。支持：

- 会话记忆（PostgreSQL checkpointer/store）
- 扩展工具动态加载与热重载
- `chat` API 与 CLI 双入口
- cron 任务管理（文件热重载 + 手动执行）

## 核心能力

- 聊天接口：`POST /api/v1/chat`，支持 `session_id` 续聊
- 扩展热重载：`POST /api/v1/extensions/reload`
- 内置工具：
  - `load_skill`：读取 `skills/*/SKILL.md`
  - `fetch_url`：抓取网页文本
  - `cron`：管理 `cron/jobs.json`
- 扩展系统：
  - 自动扫描 `extensions/*/extension.py`
  - 支持 `TOOL` / `TOOLS`
  - 单个扩展失败不影响其他扩展
- cron 调度：
  - 启动时自动加载
  - 监听 `cron/jobs.json` 变更后自动重载
  - 支持 `date` / `cron` / `interval`
- 稳定性：
  - 聊天调用遇到 checkpointer 序列化异常时自动回退到无 checkpointer 图

## 项目结构

```text
.
├── app/
│   ├── main.py                     # FastAPI 入口
│   ├── lifespan.py                 # 生命周期与连接池
│   ├── workflow.py                 # LangGraph 初始化/重载/关闭
│   ├── channels/routers/           # chat/extensions API
│   ├── agents/                     # Agent 构建与内置工具
│   ├── cron/cron_manage.py         # cron 管理器
│   └── cli/                        # CLI（chat/cron）
├── extensions/
│   ├── bash_tool/extension.py      # exec/process 扩展
│   ├── web_search/extension.py     # web_search 扩展
│   ├── shell/extension.py          # shell 示例扩展（默认排除）
│   └── example/extension.py        # 示例扩展（默认排除）
├── cron/jobs.json                  # cron 任务定义
├── setup/                          # PostgreSQL 初始化脚本
├── workspace/                      # 启动时注入的上下文 Markdown
├── run.sh                          # 本地启动脚本
└── openxbot.json                   # 可选工具配置（例如 web_search）
```

## 环境要求

- Python `>= 3.12`
- [uv](https://docs.astral.sh/uv/)
- PostgreSQL（用于 LangGraph store/checkpoint）

## 快速开始

### 1) 安装依赖

```bash
uv sync
```

### 2) 配置 `.env`

最小可用示例：

```bash
# 模型
MODEL=openai/gpt-5-mini
MODEL_PROVIDER=openai
BASE_URL=https://openrouter.ai/api/v1
API_KEY=your_api_key

# 工作目录（用于 skills/workspace/cron 相对路径解析）
WORKSPACE_ROOT=./

# PostgreSQL
PG_CONFIG_HOST=127.0.0.1
PG_CONFIG_PORT=5432
PG_CONFIG_DATABASE=openxbot
PG_CONFIG_USERNAME=postgres
PG_CONFIG_PASSWORD=postgres

# 可选：工具配置文件路径（web_search 会读取）
CONFIG_PATH=./openxbot.json
```

可选环境变量：

```bash
# 扩展管理接口保护
EXTENSIONS_ADMIN_TOKEN=

# 扩展排除（逗号分隔，忽略大小写）
EXTENSION_EXCLUDED_EXTENSIONS=example,shell
EXTENSION_EXCLUDED_TOOLS=

# web_search 配置
WEB_SEARCH_PROVIDER=brave
WEB_SEARCH_TIMEOUT_SECONDS=20
WEB_SEARCH_CACHE_TTL_MINUTES=10
BRAVE_API_KEY=
PERPLEXITY_API_KEY=
OPENROUTER_API_KEY=
PERPLEXITY_BASE_URL=
PERPLEXITY_MODEL=
XAI_API_KEY=
GROK_MODEL=
```

### 3) 初始化数据库（首次）

```bash
uv run python setup/db_setup.py
uv run python setup/memory_setup.py
```

如果你要使用向量检索，再执行：

```bash
uv run python setup/vector_setup.py
```

### 4) 启动服务

```bash
./run.sh
```

默认地址：`http://0.0.0.0:8000`

## API

### 1) 聊天接口

`POST /api/v1/chat`

```bash
curl -X POST http://127.0.0.1:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "你好，介绍一下你能做什么",
    "session_id": "demo-session-001"
  }'
```

响应示例：

```json
{
  "session_id": "demo-session-001",
  "response": "...",
  "created_at": "2026-02-25T09:00:00.000000+00:00"
}
```

### 2) 扩展热重载

`POST /api/v1/extensions/reload`

```bash
curl -X POST http://127.0.0.1:8000/api/v1/extensions/reload
```

如果设置了 `EXTENSIONS_ADMIN_TOKEN`：

```bash
curl -X POST http://127.0.0.1:8000/api/v1/extensions/reload \
  -H "x-admin-token: <your-token>"
```

## CLI

推荐入口：

```bash
uv run openxbot-cli --help
```

聊天：

```bash
uv run openxbot-cli chat
uv run openxbot-cli chat -m "你好"
uv run openxbot-cli chat --session-id demo-session-001
```

cron：

```bash
uv run openxbot-cli cron help
uv run openxbot-cli cron list
uv run openxbot-cli cron run <job-id>
uv run openxbot-cli cron remove <job-id>
```

添加任务示例：

```bash
# 单次任务（绝对时间）
uv run openxbot-cli cron add \
  --id once-demo \
  --name log \
  --at "2026-03-01 09:30:00" \
  --kwargs '{"message":"once job"}'

# 单次任务（相对时长）
uv run openxbot-cli cron add \
  --id delay-demo \
  --name log \
  --at "1h 20m 5s" \
  --kwargs '{"message":"delay job"}'

# 周期任务（cron）
uv run openxbot-cli cron add \
  --id cron-demo \
  --name log \
  --cron "0 7 * * *" \
  --kwargs '{"message":"daily job"}'
```

模块入口也可用：

```bash
uv run python -m app.cli.cli --help
```

说明：`uv run python -m app.cli` 当前会触发 `ImportError`，请使用 `openxbot-cli` 或 `python -m app.cli.cli`。

## Cron 任务格式

`cron/jobs.json` 根结构：

```json
{
  "version": 1,
  "jobs": []
}
```

单个任务示例：

```json
{
  "id": "morning-report",
  "enabled": true,
  "trigger": {
    "type": "cron",
    "expression": "0 7 * * *"
  },
  "task": {
    "name": "log",
    "kwargs": {
      "message": "good morning"
    }
  },
  "coalesce": true,
  "max_instances": 1,
  "misfire_grace_time": 60
}
```

触发器：

- `date`: `run_date`（支持 `YYYY-MM-DD HH:MM[:SS]` 或 `YYYY-MM-DDTHH:MM[:SS]`）
- `cron`: `expression`（标准 crontab）
- `interval`: 例如 `seconds` / `minutes` / `hours`

内置任务：

- `log`
- `fetch_url`

## 扩展系统

加载规则：

- 扫描路径：`extensions/*/extension.py`
- 导出：`TOOL` 或 `TOOLS`
- 必填字段：`label` / `name` / `description` / `parameters` / `execute`
- `name` 全局唯一

默认排除的扩展目录：

- `example`
- `shell`

当前仓库在默认配置下可加载扩展：

- `bash_tool`（工具：`exec`, `process`）
- `web_search`（工具：`web_search`）

详细规范见：`extensions/README.md`

## Web 搜索扩展配置

`extensions/web_search/extension.py` 支持 provider：

- `brave`
- `perplexity`
- `grok`

`brave` 的 API Key 优先读取：

1. `CONFIG_PATH` 指向 JSON 文件中的 `web_search.BRAVE_API_KEY`
2. 环境变量 `BRAVE_API_KEY`

`openxbot.json` 示例（仅示意）：

```json
{
  "web_search": {
    "provider": "brave",
    "BRAVE_API_KEY": "your_brave_key"
  }
}
```

## 常用脚本

- `setup/db_setup.py`: 初始化 LangGraph store
- `setup/memory_setup.py`: 初始化 checkpoint
- `setup/vector_setup.py`: 初始化向量存储
- `setup/generate_model.py`: 从 PostgreSQL 表生成模型
- `app/tools/hybrid_search.py`: 混合检索 CLI（向量 + 全文）

## 注意事项

- `.env` 与 `openxbot.json` 可能包含密钥，不要提交到公共仓库。
- `WORKSPACE_ROOT` 会影响以下路径：
  - `cron/jobs.json`
  - `workspace/*.md`
  - `skills/*/SKILL.md`
- 当前代码未实际读取 `SKILLS_ROOT` 与 `CRON_PATH` 环境变量；路径由 `WORKSPACE_ROOT` 推导。
