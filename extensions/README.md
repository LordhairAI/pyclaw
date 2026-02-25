# Extensions Guide

本项目支持在 `extensions/*/extension.py` 中动态注册 LangChain tools。

当前加载器位置：`app/extensions/loader.py`
当前重载接口：`POST /api/v1/extensions/reload`

## 1. 目录结构

每个扩展一个目录，文件名固定为 `extension.py`：

```text
extensions/
  README.md
  weather/
    extension.py
  your_ext/
    extension.py
```

加载器会扫描：`extensions/*/extension.py`

## 2. 扩展文件规范

`extension.py` 必须导出以下二选一：

1. `TOOL`：单工具定义（dict）
2. `TOOLS`：多工具定义（list[dict]）

每个工具 dict 必须包含 5 个字段：

- `label`: 工具标签（会写入 `tool.tags`）
- `name`: 工具名（全局唯一）
- `description`: 工具说明
- `parameters`: 参数定义（dict）
- `execute`: 可调用函数（同步或异步都支持）

## 3. 最小可用示例（单工具）

```python
# extensions/weather/extension.py

def get_weather(city: str, unit: str = "c") -> str:
    unit = unit.lower().strip()
    if unit not in {"c", "f"}:
        return "unit must be 'c' or 'f'"
    temp = 26 if unit == "c" else 79
    return f"{city} current temperature is {temp}°{unit.upper()} (mock)."

TOOL = {
    "label": "weather",
    "name": "get_weather",
    "description": "Query current weather by city",
    "parameters": {
        "city": {
            "type": "string",
            "description": "City name",
            "required": True,
        },
        "unit": {
            "type": "string",
            "description": "Temperature unit: c or f",
            "default": "c",
        },
    },
    "execute": get_weather,
}
```

## 4. 多工具示例

```python
def add(a: float, b: float) -> str:
    return str(a + b)

def multiply(a: float, b: float) -> str:
    return str(a * b)

TOOLS = [
    {
        "label": "math",
        "name": "add",
        "description": "Add two numbers",
        "parameters": {
            "a": {"type": "number", "required": True},
            "b": {"type": "number", "required": True},
        },
        "execute": add,
    },
    {
        "label": "math",
        "name": "multiply",
        "description": "Multiply two numbers",
        "parameters": {
            "a": {"type": "number", "required": True},
            "b": {"type": "number", "required": True},
        },
        "execute": multiply,
    },
]
```

## 5. parameters 支持格式

当前 `parameters` 支持两种写法。

1. 简写风格（推荐）

```python
"parameters": {
  "city": {"type": "string", "description": "City", "required": True},
  "days": {"type": "integer", "default": 1}
}
```

2. JSON Schema 风格（object/properties/required）

```python
"parameters": {
  "type": "object",
  "properties": {
    "city": {"type": "string", "description": "City"},
    "days": {"type": "integer", "description": "Forecast days", "default": 1}
  },
  "required": ["city"]
}
```

`type` 映射关系：

- `string` -> `str`
- `integer`/`int` -> `int`
- `number`/`float` -> `float`
- `boolean`/`bool` -> `bool`
- `array` -> `list[Any]`
- `object` -> `dict[str, Any]`
- 未识别类型 -> `str`

## 6. 加载与重载

应用启动时会自动加载扩展。
默认会排除 `extensions/example` 文件夹中的所有工具（不注册到 agent）。
可通过环境变量扩展排除扩展目录列表（逗号分隔，不区分大小写）：

```bash
EXTENSION_EXCLUDED_EXTENSIONS=example,internal_only,legacy
```

也支持按工具名排除（可选）：

```bash
EXTENSION_EXCLUDED_TOOLS=example,demo_tool,debug_only
```

修改扩展文件后，调用重载接口让新工具生效：

```bash
curl -X POST http://127.0.0.1:8000/api/v1/extensions/reload
```

如果配置了 `EXTENSIONS_ADMIN_TOKEN`，需要 header：

```bash
curl -X POST http://127.0.0.1:8000/api/v1/extensions/reload \
  -H "x-admin-token: <your-token>"
```

响应会包含：

- `version`: 当前扩展快照版本
- `loaded_extensions`: 成功加载的扩展目录名
- `failed_extensions`: 加载失败详情
- `tools`: 当前可用扩展工具名

## 7. 失败隔离与命名规则

- 单个扩展加载失败不会阻断其他扩展。
- `name` 必须全局唯一，重复会导致该扩展加载失败。
- 缺少必填字段（`label/name/description/parameters/execute`）会加载失败。
- `execute` 必须是可调用对象。

## 8. 最佳实践

- `name` 使用稳定、语义明确的 snake_case。
- `description` 写清输入输出和边界。
- `parameters` 的 `description` 尽量完整，便于模型正确调用。
- `execute` 内部自行处理异常并返回可读错误，避免把底层栈直接暴露给用户。
- 需要 I/O 的工具优先写成 `async def execute(...)`，减少阻塞。

## 9. 排查清单

扩展不生效时，按顺序检查：

1. 文件路径是否为 `extensions/<ext_name>/extension.py`
2. 是否导出了 `TOOL` 或 `TOOLS`
3. 工具字段是否完整
4. `name` 是否与已有工具冲突
5. 是否已调用 `/api/v1/extensions/reload`
6. 查看服务日志中的扩展加载错误

## 10. Shell 命令扩展（内置示例）

已新增：`extensions/shell/extension.py`

工具名：`run_shell_command`

参数：

- `command` (string, required): 要执行的命令，例如 `ls -la app`
- `cwd` (string, optional): 工作目录（相对 `WORKSPACE_ROOT`）
- `timeout_seconds` (integer, optional, 默认 20, 最大 120)
- `max_output_chars` (integer, optional, 默认 6000, 最大 20000)

行为说明：

- 命令通过 `shlex.split` + `subprocess.run(shell=False)` 执行。
- `cwd` 只能位于工作区内，越界会报错。
- 输出包含 `exit_code`、`stdout/stderr`，并支持截断。

修改后执行：

```bash
curl -X POST http://127.0.0.1:8000/api/v1/extensions/reload
```
