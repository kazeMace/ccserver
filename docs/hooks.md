# CCServer Hook API 文档

## 概述

CCServer Hook 系统兼容 Claude Code 的 `hooks` 配置，同时支持 OpenClaw 的 `HOOK.md` 目录格式。Hook 用于在 Agent 生命周期的关键节点插入自定义逻辑，例如拦截工具调用、修改用户消息、注入上下文、记录日志等。

---

## 注册来源与加载优先级

CCServer 从以下来源加载 hook，优先级从高到低：

| 优先级 | 来源 | 格式 |
|--------|------|------|
| 1 | `{project_root}/.ccserver/settings.local.json` | `hooks` JSON 字段 |
| 2 | `{project_root}/.ccserver/hooks/<dir>/` | OpenClaw `HOOK.md` 目录 |
| 3 | `~/.ccserver/settings.json` | `hooks` JSON 字段 |
| 4 | `~/.ccserver/hooks/<dir>/` | OpenClaw `HOOK.md` 目录 |

> **注意**：相对路径的 command hook 统一以 `CCSERVER_PROJECT_DIR`（即 `project_root`）为工作目录执行。

---

## 事件名规范

所有事件名在加载时统一规范为 **ccserver 标准名**（冒号分隔）。支持三种写法：

| 类型 | 示例 | 使用场景 |
|------|------|----------|
| ccserver 标准名 | `tool:call:before` | 通用，推荐 |
| CC 写法（PascalCase） | `PreToolUse` | `settings.json` 兼容 |
| OpenClaw 写法（下划线） | `before_tool_call` | **仅限** `HOOK.md` 的 `events` 字段 |

### 完整事件列表

#### 工具生命周期

| 标准名 | 模式 | 说明 |
|--------|------|------|
| `tool:call:before` | `modifying` | 工具调用前，可修改输入或阻断 |
| `tool:call:after` | `observing` | 工具调用成功后观察 |
| `tool:call:failure` | `observing` | 工具调用失败后观察 |
| `tool:permission:request` | `modifying` | 权限请求前，可自动决策 |
| `tool:permission:denied` | `observing` | 权限被拒绝后观察 |
| `tool:result:persist` | `modifying` | 工具结果持久化前（需存储层支持） |

#### 消息生命周期

| 标准名 | 模式 | 说明 |
|--------|------|------|
| `message:inbound:received` | `modifying` | 收到用户消息，可修改内容或注入上下文 |
| `message:inbound:claim` | `claiming` | 消息认领，第一个 `handled=True` 短路 |
| `message:outbound:sending` | `observing` | 发送回复前 |
| `message:outbound:sent` | `observing` | 发送回复后 |
| `message:notify` | `observing` | 收到系统通知 |
| `message:preprocessed` | `observing` | 消息预处理后 |
| `message:transcribed` | `observing` | 语音转文字后 |
| `message:dispatch:before` | `modifying` | 消息分发前（需网关支持） |
| `message:write:before` | `modifying` | 消息写入前（需存储层支持） |

#### 会话 / Agent 生命周期

| 标准名 | 模式 | 说明 |
|--------|------|------|
| `session:start` | `observing` | 会话启动 |
| `session:end` | `observing` | 会话结束 |
| `session:reset:before` | `observing` | 重置会话前 |
| `session:config:change` | `observing` | 配置变更 |
| `session:instructions:load` | `observing` | 系统指令加载 |
| `session:elicitation` | `modifying` | 用户意图澄清 |
| `agent:stop` | `observing` | Agent 停止前，`continue: false` 可阻止停止 |
| `agent:bootstrap` | `modifying` | Agent 初始化 |
| `agent:compact:before` | `observing` | 上下文压缩前 |
| `agent:compact:after` | `observing` | 上下文压缩后 |
| `subagent:spawning` | `observing` | 子 Agent 启动前 |
| `subagent:spawned` | `observing` | 子 Agent 启动后 |
| `subagent:ended` | `observing` | 子 Agent 结束后 |

> 更多事件见源码 `ccserver/hooks/loader.py` 中的 `KNOWN_EVENTS`。

### 常用别名对照表

| CC 写法 | OpenClaw 写法 | ccserver 标准名 |
|---------|---------------|-----------------|
| `PreToolUse` | `before_tool_call` | `tool:call:before` |
| `PostToolUse` | `after_tool_call` | `tool:call:after` |
| `UserPromptSubmit` | `message_received` | `message:inbound:received` |
| `PermissionRequest` | — | `tool:permission:request` |
| `SessionStart` | `session_start` | `session:start` |
| `Stop` | `agent_end` | `agent:stop` |

---

## 注册方式一：`settings.json` 的 `hooks` 字段

适用于快速注册 command / bun / http / prompt 类型的 hook，无需创建目录。

### 基本结构

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "echo hello",
            "timeout": 10,
            "env": {"MY_VAR": "value"}
          }
        ]
      }
    ],
    "tool:call:before": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 .ccserver/hooks/check.py"
          }
        ]
      }
    ]
  }
}
```

### Hook 配置字段

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `type` | `string` | 是 | 执行器类型：`command` / `bun` / `http` / `prompt` |
| `command` | `string` | `type=command` 时 | 要执行的 shell 命令 |
| `script` | `string` | `type=bun` 时 | `.ts` / `.js` 脚本路径 |
| `url` | `string` | `type=http` 时 | POST 目标地址 |
| `prompt` | `string` | `type=prompt` 时 | 给 LLM 的评估提示词 |
| `matcher` | `string` | 否 | 事件级匹配表达式（见下文 matcher 语法） |
| `if` | `string` | 否 | 工具输入级精确筛选（权限规则语法） |
| `timeout` | `int` | 否 | 超时秒数，默认 `30` |
| `env` | `dict` | 否 | 额外注入的环境变量 |
| `execution` | `string` | 否 | `parallel`（默认）/ `serial` |
| `collect` | `string` | 否 | `all`（默认）/ `first` / `last` |
| `async` | `bool` | 否 | `true` 时后台执行，不阻塞流程 |

### 批量控制（`hooks.internal`）

用于控制 `HOOK.md` 目录的启用/禁用和注入环境变量，**不是事件注册**：

```json
{
  "hooks": {
    "internal": {
      "my-hook": {
        "enabled": false,
        "env": {"DEBUG": "1"}
      }
    }
  }
}
```

---

## 注册方式二：`HOOK.md` 目录格式

适用于与 OpenClaw 兼容的复杂 hook，支持 TypeScript / Python / Shell 脚本。

### 目录结构

```
.ccserver/hooks/my-hook/
├── HOOK.md          # YAML 前置参数
└── handler.ts       # 执行体（bun 运行）
# 或 handler.py
# 或 handler.sh
```

### `HOOK.md` 示例

```yaml
---
metadata:
  openclaw:
    events:
      - message:inbound:received
      - tool:call:before
    requires:
      bins: [python3]
    execution: parallel
    collect: all
---

# 这里是给人类看的说明文档
```

### `HOOK.md` 字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `events` | `list[str]` | 要注册的事件名列表 |
| `requires.bins` | `list[str]` | 运行所需的可执行文件 |
| `requires.env` | `list[str]` | 运行所需的环境变量 |
| `execution` | `string` | `parallel` / `serial` |
| `collect` | `string` | `all` / `first` / `last` |
| `always` | `bool` | `true` 时跳过所有 `requires` 检查 |

### handler 脚本

脚本通过 **stdin** 接收 JSON payload，通过 **stdout** 返回 JSON 结果。

#### 退出码语义

| 退出码 | 含义 |
|--------|------|
| `0` | 成功，解析 stdout JSON |
| `2` | **阻断**，`stderr` 内容作为阻断原因 |
| 其他 | 非阻断错误，只记日志，流程继续 |

#### Python handler 示例

```python
import os
import json
import sys

def main() -> None:
    payload = json.load(sys.stdin)
    event = payload.get("hook_event_name")
    session_id = payload.get("session_id")
    project_root = payload.get("project_root")

    output = {
        "continue": True,
        "hookSpecificOutput": {
            "hookEventName": event,
            "additionalContext": f"[session={session_id}]",
        },
    }
    print(json.dumps(output, ensure_ascii=False))
    sys.exit(0)

if __name__ == "__main__":
    main()
```

#### stdout 输出格式

```json
{
  "continue": true,
  "hookSpecificOutput": {
    "hookEventName": "message:inbound:received",
    "message": "替换后的消息内容",
    "additionalContext": "追加给 LLM 的上下文",
    "updatedInput": {"command": "修改后的工具输入"},
    "permissionDecision": "allow",
    "handled": true
  },
  "systemMessage": "注入给 LLM 的系统提示"
}
```

### 输出字段说明

| 字段 | 适用事件 | 说明 |
|------|----------|------|
| `message` | `message:inbound:received` | 替换用户消息内容 |
| `additionalContext` | `message:inbound:received`, `tool:call:before` | 追加给 LLM 的上下文 |
| `updatedInput` | `tool:call:before` | 替换工具输入参数 |
| `permissionDecision` | `tool:permission:request` | `block` / `deny` / `allow` / `ask` / `passthrough` |
| `handled` | `message:inbound:claim` | `true` 表示已认领，短路后续 handler |
| `continue` | `agent:stop` | `false` 阻止 Agent 停止 |
| `systemMessage` | 任意 | 注入给 LLM 的系统提示 |

---

## Matcher 语法

用于 `settings.json` 的 `matcher` 字段，对事件 payload 进行筛选。

### 简单匹配（LiteralMatcher）

| 写法 | 含义 |
|------|------|
| `*` 或 空字符串 | 无条件匹配 |
| `Bash` | 精确匹配 `tool_name == "Bash"` |
| `Bash|Write|Edit` | 多值精确匹配 |
| `^Write.*` | 正则匹配（包含非字母数字下划线竖线字符时） |

### 表达式匹配（ExpressionMatcher）

支持完整布尔表达式：

```
tool == "Bash" && tool_input.command matches "git *"
```

#### 运算符

| 运算符 | 说明 | 示例 |
|--------|------|------|
| `==` | 等于 | `tool == "Bash"` |
| `!=` | 不等于 | `tool != "Read"` |
| `matches` | 正则匹配 | `tool_input.file_path matches "\.(ts|js)$"` |
| `contains` | 包含子串 | `tool_output contains "error"` |
| `startswith` | 前缀匹配 | `tool_input.command startswith "python"` |
| `endswith` | 后缀匹配 | `tool_input.file_path endswith ".md"` |
| `&&` / `and` | 逻辑与 | `a && b` |
| `\|\|` / `or` | 逻辑或 | `a \|\| b` |
| `!` / `not` | 逻辑非 | `!a` |

#### 可用字段

| 字段 | 来源 |
|------|------|
| `tool` | `payload["tool_name"]` |
| `tool_input.xxx` | `payload["tool_input"]["xxx"]` |
| `tool_output` | `payload["tool_response"]` |
| `tool_use_id` | `payload["tool_use_id"]` |

---

## 执行策略

### `execution`

- `parallel`（默认）：所有 handler 同时启动
- `serial`：按顺序执行，前一个完成后再启动下一个

### `collect`

- `all`（默认）：聚合所有结果（`_merge_results`）
- `first`：只返回第一个成功的 `HookResult`
- `last`：只返回最后一个成功的 `HookResult`

### 模式语义

| 模式 | 行为 |
|------|------|
| `modifying` | 结果可修改输入 / 阻断流程。`block=True` 时短路停止后续 handler |
| `observing` | 结果只读，返回值被忽略 |
| `claiming` | `handled=True` 时短路，第一个认领者胜出 |

---

## 环境变量注入

### 命令级注入

在 `settings.json` 的 hook 配置里使用 `env` 字段：

```json
{
  "type": "command",
  "command": "python3 hook.py",
  "env": {
    "CONVERSATION_ID": "abc123"
  }
}
```

### 控制面板级注入

```json
{
  "hooks": {
    "internal": {
      "my-hook": {
        "env": {"DEBUG": "1"}
      }
    }
  }
}
```

### 自动继承

所有 hook 子进程自动继承 server 的完整 `os.environ`。若启动 server 时传了环境变量（如 `CONVERSATION_ID=xxx python server.py`），hook 内可直接通过 `os.environ` 读取。

---

## stdin Payload 格式

每个 handler 通过 stdin 接收的 JSON 结构：

```json
{
  "hook_event_name": "tool:call:before",
  "session_id": "uuid",
  "cwd": "/tmp/ccserver/.../workdir",
  "project_root": "/Volumes/DISK/programs/ccserver/playground/agents/roleplay_agent_neo",
  "depth": 0,
  "is_orchestrator": true,
  "agent_id": "uuid",
  "agent_name": "orchestrator",
  "tool_name": "Bash",
  "tool_input": {"command": "echo hello"},
  "tool_use_id": "uuid"
}
```

- **基础字段**：`hook_event_name`, `session_id`, `cwd`, `project_root`, `depth`, `is_orchestrator`, `agent_id`, `agent_name`
- **事件专属字段**：如 `tool_name`, `tool_input`, `tool_output`, `prompt` 等，取决于触发的事件

---

## 最佳实践

1. **优先使用 ccserver 标准名**（如 `tool:call:before`），避免混用多种写法
2. **相对路径基于 `CCSERVER_PROJECT_DIR`**：command hook 的相对路径会自动在项目根目录下执行
3. **阻断用 exit 2**：command 类型的 hook 想要阻断流程，返回 exit code 2 最简洁
4. **observing 事件不要返回 block**：虽然不会报错，但语义上无效
5. **用 `timeout` 防止挂起**：尤其是调用外部 API 的 hook
