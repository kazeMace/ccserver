# ccserver Agent Loop 生命周期梳理与改进建议

>  Date: 2026/04/11  
>  目标：参考 Claude Code / OpenClaw 生命周期，对 ccserver 的 agent loop 进行系统性梳理，找出缺失的细粒度 hook、emitter 配合、subagent 复用等关键点，为教学和工程落地提供清晰蓝图。

---

## 一、当前已实现的 Agent Loop 全景图

### 1.1 主循环入口（AgentRunner.run）

```text
AgentRunner.run(session, message, emitter)
    ├─ session:mcp.connect_all()           # 首次调用时才 lazy connect
    ├─ AgentFactory.create_root()          # 构造根 Agent
    ├─ hook: session:start                 # (observing)
    ├─ agent.run(message)
    │      ├─ hook: message:inbound:received   # (modifying) 可改消息、追加上下文
    │      ├─ 如果是 /command → 处理 command 并包装为 dict
    │      ├─ _append({role:"user", content:message})
    │      └─ _loop()
    └─ hook: session:end                   # (observing)
```

### 1.2 Agent._loop 核心轮次

```text
for round in 1..round_limit:
    ├─ _maybe_compact()                    # token 阈值触发 Compaction
    │      ├─ hook: agent:compact:before   # (observing)
    │      └─ hook: agent:compact:after    # (observing)
    ├─ _call_llm_with_retry()
    │      ├─ hook: prompt:llm:input       # (observing) 看即将发出去的输入
    │      ├─ adapter.stream()             # 真正调 LLM
    │      └─ 返回 (collected_tokens, response)
    ├─ recorder.record()                   # 归档本轮输入输出
    ├─ _append({role:"assistant", content:content})
    ├─ 提取 round_text
    │      └─ hook: prompt:llm:output      # (observing) 每轮 LLM 文本输出后
    ├─ 如果 stop_reason != "tool_use"
    │      ├─ 推送 last_tokens（只推送最近一次有内容的 token）
    │      ├─ hook: agent:stop             # (observing) 根代理完成
    │      ├─ emitter.emit_done()          # 或子代理 emit_subagent_done()
    │      └─ return last_text
    └─ _handle_tools(response.content)
           ├─ 遍历每个 tool_use block
           │      ├─ 权限检查 (ask_tools)
           │      │      ├─ hook: tool:permission:request  # (modifying)
           │      │      └─ 可能 emitter.emit_permission_request()
           │      │             └─ hook: tool:permission:denied  # (observing)
           │      ├─ hook: tool:call:before                  # (modifying)
           │      ├─ emitter.emit_tool_start()
           │      ├─ 实际调用工具
           │      ├─ emitter.emit_tool_result()
           │      ├─ hook: tool:call:after  /  tool:call:failure  # (observing)
           │      └─ 收集 tool_result
           └─ 返回 (results, trigger_compact)
              └─ 如果 trigger_compact → _do_compact

如果 round_limit 到达:
    ├─ hook: agent:limit                   # (observing)
    └─ _on_limit() 按策略处理
           ├─ last_text  (默认)
           ├─ ask_user   (仅根代理)
           ├─ graceful
           ├─ summarize
           └─ callback
```

### 1.3 子代理（Subagent）生命周期

```text
Agent._handle_agent()
    ├─ 深度检查 (MAX_DEPTH)
    ├─ spawn_child(prompt, agent_def, ...)
    │      ├─ 过滤工具（黑白名单、MCP、skills）
    │      ├─ 可能包装 FilterEmitter（按 output_mode）
    │      └─ 初始化 AgentContext（depth+1, parent_id, parent_name）
    ├─ hook: subagent:spawning             # (observing) 用 child._build_hook_ctx()
    ├─ hook: subagent:spawned              # (observing)
    ├─ child._loop()                       # 子代理复用完全相同的 _loop
    ├─ hook: subagent:ended                # (observing)
    └─ ToolResult.ok(summary)
```

---

## 二、与 OpenClaw / Claude Code 生命周期的对照

### 2.1 已实现的生命周期阶段

| 阶段 | 状态 | 备注 |
|------|------|------|
| Session 启动/结束 | 已实现 | `session:start`, `session:end` |
| 消息入站 | 已实现 | `message:inbound:received` (modifying) |
| LLM 输入 | 已实现 | `prompt:llm:input` (observing) |
| LLM 输出 | 已实现 | `prompt:llm:output` (observing) |
| 工具权限请求 | 已实现 | `tool:permission:request` (modifying) |
| 工具调用前 | 已实现 | `tool:call:before` (modifying) |
| 工具调用后/失败 | 已实现 | `tool:call:after`, `tool:call:failure` |
| Agent 停止 | 已实现 | `agent:stop`, `agent:stop:failure` |
| Compaction | 已实现 | `agent:compact:before/after` |
| Round Limit | 已实现 | `agent:limit` |
| Subagent spawn/end | 已实现 | `subagent:spawning/spawned/ended` |

### 2.2 尚未实现或不够完善的生命周期阶段

#### A. 更细粒度的消息生命周期（Message Lifecycle）

| Hook 点 | OpenClaw 对应 | 当前状态 | 说明 |
|--------|---------------|----------|------|
| `message:inbound:claim` | `inbound_claim` | 定义了，未在代码中触发 | 用于 claiming 模式，决定哪条 handler 接管消息 |
| `message:preprocessed` | — | 定义了，未触发 | prompt lib on_message 之后、写入 messages 之前 |
| `message:outbound:sending` | `message_sending` | 定义了，未触发 | assistant 消息写入 messages 后、发向用户前 |
| `message:outbound:sent` | `message_sent` | 定义了，未触发 | 用户已收到消息之后 |
| `message:write:before` | `before_message_write` | 定义了，未触发 | 持久化写入磁盘前（modifying） |
| `message:notify` | `Notification` | 定义了，未触发 | 系统通知类消息 |
| `message:transcribed` | — | 定义了，未触发 | 语音/图片转文本后 |
| `message:dispatch:before` | `before_dispatch` | 定义了，未触发 | 网关分发前（多租户/IM 场景） |

**关键缺失分析：**

当前 `_append()` 直接把消息塞入 `context.messages` 并 optionally `session.persist_message()`。这个过程中没有拦截点。对于需要“在消息写入前做审计 / 修改 / 过滤”的场景，缺少 `message:preprocessed` 和 `message:write:before`。

#### B. Prompt / System 构建生命周期

| Hook 点 | 当前状态 | 说明 |
|--------|----------|------|
| `prompt:build:before` | 定义了，未触发 | system + messages 组装成最终 prompt 前，可修改 system / messages |
| `prompt:model:before` | 定义了，未触发 | 真正调 LLM 前，可修改 model / temperature / max_tokens |
| `prompt:agent:before` | 定义了，未触发 | 子 agent 启动前对 prompt 做最后调整 |

**关键缺失分析：**

- `prompt:build:before` 是 **非常重要** 的扩展点。当前 system prompt 在 `Agent.__init__` 中由 `lib.build_system()` 一次性构建，之后再也无法更改。如果用户想在运行时动态注入 context（如最新的 git diff），没有 hook 点可拦截。
- `prompt:model:before` 对 A/B 测试、动态降级（比如按 token 数换模型）很关键。

#### C. Agent 启动与 Bootstrap 生命周期

| Hook 点 | 当前状态 | 说明 |
|--------|----------|------|
| `agent:bootstrap` | 定义了，未触发 | Agent 构造完成后、第一次循环开始前，可修改 tools / schemas |

**关键缺失分析：**

当前 `AgentFactory.create_root` 构建完 Agent 后，就直接进入 `agent.run()`。`_loop()` 开始前没有任何 hook。这导致想在 `session:start` 和 `_loop()` 之间做“agent 级初始化”（如根据当前 task list 动态裁剪 tool schemas）没有合适的时机。

#### D. 工具结果持久化生命周期

| Hook 点 | 当前状态 | 说明 |
|--------|----------|------|
| `tool:result:persist` | 定义了，未触发 | 工具结果写入 storage / 返回给 LLM 前，可做脱敏或审计 |

**关键缺失分析：**

-sensitive data（如包含 password 的 Bash 输出） currently 没有任何 scrubbing 拦截点。`tool:result:persist` 可以对 tool_result 内容做最后修改再返回给 LLM 或写入 storage。

#### E. Session 级事件

| Hook 点 | 当前状态 | 说明 |
|--------|----------|------|
| `session:reset:before` | 定义了，未触发 | `/clear` 或 session reset 前 |
| `session:patch` | 定义了，未触发 | instructions / settings 热更新时 |
| `session:config:change` | 定义了，未触发 | settings.json 变更时 |
| `session:instructions:load` | 定义了，未触发 | 从文件加载 system instructions 时 |
| `session:elicitation` / `elicitation:result` | 定义了，未触发 | 追问澄清时 |

---

## 三、Emitter 与 Hook 配合现状分析

### 3.1 当前 Emitter 架构

```text
BaseEmitter
    ├─ fmt_token / fmt_tool_start / fmt_tool_result / fmt_done / fmt_error
    ├─ fmt_subagent_done / fmt_compact / fmt_ask_user / fmt_permission_request
    └─ emit(event: dict)  [抽象]

实现类：
    SSEEmitter        → asyncio.Queue，支持双向交互（ask_user / permission）
    WSEmitter         → WebSocket（代码未读取，但 presumably 类似 SSE）
    CollectEmitter    → 内存 list，用于 HTTP 非流式
    TUIEmitter        → TUI 输出
    FilterEmitter     → 包装器，按 output_mode 过滤事件
```

### 3.2 Emitter 与 Hook 的耦合现状

当前 **Emitter 完全不知道 Hook 的存在**。数据流向是单向的：

```text
Agent._loop / _handle_tools
    ├─ 先触发 hook（session.hooks.emit/emit_void）
    └─ 再调用 self.emitter.emit_xxx()
```

**问题 1：Hook 无法“选择性地阻止内容发向用户”**

`tool:call:before` 可以 block 工具调用，但它 block 的是“执行”，而不是“给用户看什么”。如果我想让工具**继续执行**，但**不把 tool_start / tool_result 事件推给用户**，当前做不到。

**问题 2：Emitter 无法被 Hook 装饰或修改**

Claude Code 有一个模式是：hook 可以在 `message:outbound:sending` 时把 assistant 消息注释掉，或者把 tool_result 替换为简化版。当前 emitter 是 Agent 的一个纯输出通道，没有 hook 后处理层。

**问题 3：子代理的 output_mode 与 Emitter 耦合较深**

`FilterEmitter` 是在 `spawn_child()` 时按 `agent_def.output_mode` 决定的。这意味着子代理的过滤策略在**构造时就固定了**，运行中无法由 hook 动态调整。

---

## 四、Subagent Loop 复用现状分析

### 4.1 当前复用方式

子代理与根代理**共用同一个 `_loop()` 方法**，这是好的设计。差异仅通过构造参数体现：

| 属性 | 根代理 | 子代理 |
|------|--------|--------|
| `persist` | True | False |
| `round_limit` | MAIN_ROUND_LIMIT | SUB_ROUND_LIMIT |
| `depth` | 0 | >= 1 |
| `tools` | 全量 | 过滤后的白名单 |
| `skills_override` | None | 通常为 [] 或 agent_def.skills |
| `run_mode` | 从 settings 读取 | 强制 "auto" |
| `emitter` | 原始 emitter | 可能包装 FilterEmitter |

### 4.2 复用上的不足

**不足 1：没有 Subagent Pool / 重用机制**

子代理是“用完即焚”的：`await child._loop()` 结束后，`child` 对象和它的 `AgentContext` 一起被 GC。对于频繁调用的同类型子代理（如 Quality Check），每次都重新初始化 system prompt、构建 schemas、过滤 tools，有一定的性能开销。

**不足 2：缺少 subagent 的独立生命周期 hook**

- 子 agent 的 `_loop()` 内部触发的 hook（如 `prompt:llm:input/output`, `tool:call:before` 等）和根代理**共用同一个 event namespace**。
- 虽然 `HookContext.depth` 可以区分，但如果想对“特定子代理类型”绑定专属 hook，没有 `subagent:loop:before / subagent:loop:after` 这样的细粒度点。

**不足 3：子代理的结果聚合缺少 hook**

父代理收到 `ToolResult.ok(summary)` 后，直接把 summary 作为 tool_result 塞回 LLM。如果我想对 summary 做后处理（如要求统一 JSON 格式、追加元数据），没有专用 hook。

---

## 五、面向教学的代码清晰度评估

### 5.1 当前教学优势

1. **根/子代理无代码分叉**：`Agent` 一个类统管，非常有利于教学理解“代理就是上下文 + 循环 + 工具集”。
2. **Hook 系统与执行逻辑解耦**：`HookLoader`、`HookContext`、`HookResult` 三层职责清晰。
3. **Emitter 抽象干净**：`BaseEmitter` 只负责格式化 + `emit(event)`，SSE/WS/Collect 分层明确。
4. **注释和日志较完善**：`agent.py` 中几乎每个阶段都打了 debug log，参数含义有注释。

### 5.2 当前教学劣势

1. **Hook 点散落在代码各处**：`_loop`、`_handle_tools`、`_append` 里都有 hook 调用，但没有统一的生命周期图示或注册表，学生很难一眼看清“在哪里插了什么 hook”。
2. **Emitter 事件类型没有文档化**：`token`、`tool_start`、`done`、`subagent_done` 等事件散落在 `BaseEmitter` 的子类中，没有一个 `EventCatalog` 供学生快速查阅。
3. **`_append` 做了太多隐式工作**：消息包装由 `lib.on_message()` 完成，对初学者来说是一个“黑箱”，难以跟踪用户消息是怎么变成 LLM 可见格式的。
4. **缺少生命周期可视化辅助**：代码本身没有打印 agent 状态转换图的能力，调试时只能靠 grep `logger.debug`。

---

## 六、改进建议（分阶段落地）

### 阶段一：补齐缺失的 Hook 拦截点（高优先级）

#### 6.1.1 在 `_append()` 中插入 `message:preprocessed` 和 `message:write:before`

```python
# _append 方法改造后
async def _append(self, message: dict):
    message = lib.on_message(message, ...)
    
    # hook: message:preprocessed
    hook_result = await self.session.hooks.emit(
        "message:preprocessed",
        {"message": message},
        self._build_hook_ctx(),
    )
    if hook_result.updated_input:   # 复用字段语义：这里表示修改后的 message dict
        message = hook_result.updated_input
    
    self.context.messages.append(message)
    
    # hook: message:write:before（仅 persist=True 时）
    if self.persist and self.storage:
        write_hook = await self.session.hooks.emit(
            "message:write:before",
            {"message": message},
            self._build_hook_ctx(),
        )
        if write_hook.block:
            # block 表示不写入磁盘，但仍然在内存中保留
            logger.debug("Hook blocked message persist | agent={}", self.aid_label)
            return
        self.session.persist_message(message)
```

#### 6.1.2 在 `_call_llm_with_retry()` 中插入 `prompt:build:before` 和 `prompt:model:before`

```python
# 改造后的调用前逻辑
input_messages = [dict(m) for m in self.context.messages]

# hook: prompt:build:before
build_hook = await self.session.hooks.emit(
    "prompt:build:before",
    {"system": self.system, "messages": input_messages},
    self._build_hook_ctx(),
)
system = build_hook.updated_input.get("system") if build_hook.updated_input else self.system
messages = build_hook.updated_input.get("messages") if build_hook.updated_input else input_messages

# hook: prompt:model:before
model_hook = await self.session.hooks.emit(
    "prompt:model:before",
    {"model": self.model, "messages": messages, "system": system},
    self._build_hook_ctx(),
)
model = model_hook.updated_input.get("model") if model_hook.updated_input else self.model
max_tokens = model_hook.updated_input.get("max_tokens", 8000) if model_hook.updated_input else 8000

# 然后传给 adapter.stream(...)
```

#### 6.1.3 在 `_loop()` 开始处插入 `agent:bootstrap`

```python
async def _loop(self) -> str:
    # 新增
    bootstrap_hook = await self.session.hooks.emit(
        "agent:bootstrap",
        {"tools": list(self.tools.keys()), "schemas_count": len(self._schemas)},
        self._build_hook_ctx(),
    )
    if bootstrap_hook.updated_input and "schemas" in bootstrap_hook.updated_input:
        self._schemas = bootstrap_hook.updated_input["schemas"]
    # ... 原有逻辑
```

#### 6.1.4 在 tool_result 返回前插入 `tool:result:persist`

```python
# _handle_tools 中，收集到 result 后、results.append 前
persist_hook = await self.session.hooks.emit(
    "tool:result:persist",
    {"tool_name": name, "tool_result": result.to_api_dict(block_id)},
    self._build_hook_ctx(),
)
if persist_hook.updated_input:
    result = ToolResult.from_api_dict(persist_hook.updated_input)  # 需要新增 from_api_dict
```

### 阶段二：Emitter 与 Hook 解耦重构

#### 6.2.1 引入 `HookableEmitter` 中间层

将“事件发送给用户”这一动作也变为可被 hook 的生命周期。设计一个新的中间层：

```python
class HookableEmitter(BaseEmitter):
    """
    包装一个真实 Emitter，在发送给客户端之前和之后触发 hook。
    让 hook 可以：
      1. 修改/替换事件内容（message:outbound:sending）
      2. 完全阻止事件发送（block）
      3. 在发送后做审计/日志（message:outbound:sent）
    """
    def __init__(self, inner: BaseEmitter, session: Session, agent: "Agent"):
        self._inner = inner
        self._session = session
        self._agent = agent
```

**事件映射表（hook → emitter 调用点）：**

| Emitter 动作 | 触发 hook | payload |
|-------------|-----------|---------|
| `emit_token` | `message:outbound:sending` | `{type:"token", content:text}` |
| `emit_tool_start` | `message:outbound:sending` | `{type:"tool_start", tool:name, preview:preview}` |
| `emit_tool_result` | `message:outbound:sending` | `{type:"tool_result", tool:name, output:output}` |
| `emit_done` | `message:outbound:sending` | `{type:"done", content:content}` |
| `emit_error` | `message:outbound:sending` | `{type:"error", message:message}` |
| 上述所有 emit 返回后 | `message:outbound:sent` | 同上 |

> **注意**：`permission_request` 和 `ask_user` 因为涉及阻塞交互，建议**跳过** `message:outbound:sending` hook，直接走 inner emitter，避免 hook 误 block 导致 agent 挂起。

#### 6.2.2 Agent 改造：让 Emitter 自持有 Hook 上下文

当前 Agent 需要先调 hook 再调 emitter，改为 Agent 统一走 `HookableEmitter`：

```python
# Agent.__init__ 中
self.emitter = HookableEmitter(emitter, session, self)
```

这样 Agent 的代码可以简化，把 hook 调用从 Agent 中剥离，落到 Emitter 层：

```python
# 改造前（Agent._loop 中）
for token in last_tokens:
    await self.emitter.emit_token(token)
await self.session.hooks.emit_void("agent:stop", ...)
await self.emitter.emit_done(last_text)

# 改造后
for token in last_tokens:
    await self.emitter.emit_token(token)
await self.session.hooks.emit_void("agent:stop", ...)   # 这个保留在 Agent 层
await self.emitter.emit_done(last_text)                  # emitter 内部自行触发 outbound hook
```

### 阶段三：Subagent Loop 复用增强

#### 6.3.1 引入 `SubagentPool`（可选优化）

如果同类型子代理被频繁调用（如 pipeline graph 中的循环节点），可以引入轻量级池：

```python
class SubagentPool:
    """按 agent_def 名称缓存子代理上下文，支持 warm start。"""
    def __init__(self, parent_agent: Agent):
        self._parent = parent_agent
        self._pool: dict[str, AgentContext] = {}   # name -> 复用上下文
```

核心思想不是复用 `Agent` 对象本身（因为 asyncio 状态复杂），而是**复用 `AgentContext.messages`** 中的 system prompt 部分，或复用已经预热好的 `schemas` / `tools` 字典。

> **教学建议**：先不做对象池，因为会引入并发和状态管理复杂度。作为替代，先把 `spawn_child()` 拆成 `build_child_context()` + `create_child_from_context()`，让学生看到“构造子代理 = 组装上下文 + 创建实例”两步，就已经是很好的教学素材。

#### 6.3.2 增加 `subagent:result:finalizing` hook

在 `_handle_agent()` 返回 `ToolResult` 之前，插入一个 summarization / post-processing hook：

```python
summary = await child._loop()

final_hook = await self.session.hooks.emit(
    "subagent:result:finalizing",
    {"summary": summary, "subagent_id": child.context.agent_id},
    child._build_hook_ctx(),
)
if final_hook.message is not None:
    summary = final_hook.message

return ToolResult.ok(summary)
```

### 阶段四：教学友好性提升

#### 6.4.1 增加 `AgentLifecycleTracer`（调试/教学工具）

```python
class AgentLifecycleTracer:
    """
    将 Agent 的生命周期打印为 ASCII 流程图或 JSON trace。
    用于教学和远程调试（让学生把 trace 贴到 issue 里）。
    """
    def __init__(self, emitter: BaseEmitter):
        self._events = []
        
    def trace(self, phase: str, agent_id: str, payload: dict):
        self._events.append({"ts": datetime.now(timezone.utc).isoformat(), "phase": phase, ...})
```

可以在 `Agent._loop` 的每个阶段调用 `self._tracer.trace("loop.round.start", ...)`。

#### 6.4.2 把 `_append` 中的 `lib.on_message()` 显式注释+文档化

在代码中明确写出包装过程：

```python
# Step 1: 原始 user/assistant message
# Step 2: prompt lib 格式化（如 cc_reverse 会注入 skills catalog、command wrapper）
# Step 3: hook: message:preprocessed
# Step 4: 写入 memory (context.messages)
# Step 5: hook: message:write:before
# Step 6: 写入 disk (if persist)
```

#### 6.4.3 维护一份 `docs/agent_lifecycle.md` 和 `docs/hook_reference.md`

- `agent_lifecycle.md`：用 Mermaid / ASCII 画完整的生命周期图。
- `hook_reference.md`：表格列出所有 hook 的名称、触发时机、mode（modifying/observing/claiming）、payload 字段。

---

## 七、总结：当前最急需处理的 5 件事

| 优先级 | 事项 | 影响面 | 教学价值 |
|--------|------|--------|----------|
| P0 | **补齐 `prompt:build:before`** | 高。让外部可以在运行时动态修改 system/messages | 展示 LLM 调用前的“最后修改窗口” |
| P0 | **补齐 `message:preprocessed` 和 `message:outbound:sending`** | 高。完成消息双向生命周期闭环 | 展示 Message Pipeline 的完整流经 |
| P1 | **实现 `HookableEmitter` 中间层** | 中。把 emitter 变为 hook 感知层 | 解耦 Agent 和 Emitter 的教学示例 |
| P1 | **补齐 `agent:bootstrap`** | 中。让 agent 启动初期具备可扩展性 | 展示延迟初始化 / 动态 schema 裁剪 |
| P2 | **增加 `subagent:result:finalizing`** | 中。子代理结果后处理 | 展示父代理对子代理结果的聚合控制 |

---

## 八、附录：建议新增的 KNOWN_EVENTS 条目

为了让代码和文档同步，建议在 `managers/hooks/manager.py` 的 `KNOWN_EVENTS` 中补充以下条目（如果已实现对应的触发点）：

```python
# 新增或确认已有
"message:preprocessed":      {"mode": "modifying", "phase": "p0", "execution": "parallel", "collect": "all"},
"message:outbound:sending":  {"mode": "modifying", "phase": "p0", "execution": "parallel", "collect": "all"},
"message:outbound:sent":     {"mode": "observing", "phase": "p0", "execution": "parallel", "collect": "all"},
"message:write:before":      {"mode": "modifying", "phase": "p1", "execution": "parallel", "collect": "all"},
"prompt:build:before":       {"mode": "modifying", "phase": "p0", "execution": "parallel", "collect": "all"},
"prompt:model:before":       {"mode": "modifying", "phase": "p0", "execution": "parallel", "collect": "all"},
"agent:bootstrap":           {"mode": "modifying", "phase": "p0", "execution": "parallel", "collect": "all"},
"tool:result:persist":       {"mode": "modifying", "phase": "p0", "execution": "parallel", "collect": "all"},
"subagent:result:finalizing":{"mode": "modifying", "phase": "p0", "execution": "parallel", "collect": "all"},
```

> 原则：**只有代码中真正触发了的 hook 才注册到 KNOWN_EVENTS**，避免文档与代码脱节。可以先加条目，再逐个补触发点；也可以按 P0→P2 的顺序边实现边注册。
