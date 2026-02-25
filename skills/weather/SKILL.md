---
name: weather
description: "通过 wttr.in 或 Open-Meteo 获取当前天气与预报。适用场景：用户询问任意地点的天气、气温或天气预报。不适用场景：历史天气数据、恶劣天气预警或深入气象分析。无需 API Key。"
homepage: https://wttr.in/:help
metadata: { "pyclaw": { "emoji": "🌤️", "requires": { "bins": ["fetch_url"] } } }
---

# 天气技能

获取当前天气状况与天气预报。

## 执行步骤

1. 从用户消息中提取城市名称
2. 使用`fetch_url`工具访问wttr.in天气服务

✅ **在以下情况使用此技能：**

- “今天天气怎么样？”
- “今天/明天会下雨吗？”
- “[城市] 的气温”
- “本周天气预报”
- 出行前的天气查询

## 何时不要使用

❌ **在以下情况不要使用此技能：**

- 历史天气数据 → 使用天气档案/相关 API
- 气候分析或趋势研判 → 使用专业数据源
- 超本地微气候数据 → 使用本地传感器
- 恶劣天气预警 → 查看官方 NWS 来源
- 航空气象/海洋气象 → 使用专业服务（如 METAR 等）

## 位置信息

天气查询中应始终包含城市、地区或机场代码。

## 命令


### 当前天气

```bash
# 单行摘要
fetch_url("wttr.in/London?format=3")

# 详细当前状况
fetch_url("wttr.in/London?0")

# 指定城市
fetch_url("wttr.in/New+York?format=3")
```

### 天气预报

```bash
# 3 天预报
fetch_url("wttr.in/London")
# 一周预报
fetch_url("wttr.in/London?format=v2")
# 指定某一天（0=今天，1=明天，2=后天）
fetch_url("wttr.in/London?1")
```

### 输出格式选项

```bash
# 单行输出
fetch_url("wttr.in/London?format=%l:+%c+%t+%w")

# JSON 输出
fetch_url("wttr.in/London?format=j1")

# PNG 图片
fetch_url("wttr.in/London.png")
```

### 格式代码

- `%c` — 天气状况 emoji
- `%t` — 温度
- `%f` — “体感温度”
- `%w` — 风况
- `%h` — 湿度
- `%p` — 降水
- `%l` — 地点

## 快速回复

**“今天天气怎么样？”**

```python
fetch_url("wttr.in/London?format=%l:+%c+%t+(feels+like+%f),+%w+wind,+%h+humidity")
```

**“会下雨吗？”**

```bash
fetch_url("wttr.in/London?format=%l:+%c+%p")
```

**“周末天气预报”**

```bash
fetch_url("wttr.in/London?format=v2")
```

## 备注

- 无需 API Key（使用 wttr.in）
- 有频率限制；不要高频刷请求
- 支持大多数全球城市
- 支持机场代码：`fetch_url("wttr.in/ORD")`
