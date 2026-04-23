# ccserver Agent Loop 深度拆解

> Date: 2026/04/11  
> 目标：逐行跟踪 `Agent._loop()` 的执行流，分析每个阶段的数据状态变化、边缘情况、真实代码缺陷，并提出可落地的改进点。

---

## 一、入口到 `_loop()` 的精确路径

### 1.1 `AgentRunner.run()` (`main.py:45-81`)

```python
AgentRunner.run(session, message, emitter)
    ├─ session.mcp.connect_all()          # lazy connect
    ├─ AgentFactory.create_root(session, emitter, ...)
    │      ├─ ToolManager.get_enabled_tools()  # 按 settings 过滤
    │      ├─ agent = Agent(session, emitter, tools, ...)
    │      │      ├─ lib.build_system(...)     # 构建 system prompt 列表
    │      │      ├─ self._schemas 构建        # 工具 schema 缓存
    │      │      └─ Recorder 初始化
    │      └─ agent._schemas += settings.filter_mcp_schemas(...)
    │         └─ lib.patch_tool_schemas(agent._schemas)
    ├─ hook: session:start                 # (observing)
    └─ agent.run(message)
```

### 1.2 `Agent.run()` (`agent.py:162-181`)

```python
async def run(self, message: str) -> str:
    # 1. hook message:inbound:received (modifying)
    hook_result = await self.session.hooks.emit(...)
    if hook_result.message is not None:
        message = hook_result.message
    if hook_result.additional_context:
        message = message + "\n\n" + hook_result.additional_context

    # 2. Command 分支
    if message.startswith("/"):
        await self._handle_command(message)    # 内部也会调用 self._append()
    else:
        self._append({"role": "user", "content": message})

    # 3. 进入核心循环
    return await self._loop()
```

**注意点：**
- 如果 hook `message:inbound:received` 把 `message` 改成了一个 `"/clear"` 这样的 command，`_handle_command` 会在**用户原始输入之后**被触发。这是一个合法的 but potentially surprising 的行为。
- `_handle_command` 对于 `/clear` 会先清空 `self.context.messages`，然后**又把 clear command 本身 append 回去**。这意味着 clear 后的 messages 不是空的，而是一条 command 消息 (`_type: "command"`)。

---

## 二、`Agent._loop()` 逐轮状态机

### 2.1 初始化状态

```python
last_tokens: list[str] = []   # 最近一次有文本输出的 token 列表
last_text: str = ""           # 最近一次有文本输出的拼接文本
```

### 2.2 单轮执行流程（Round N）

> 以下标注 `msgs[n]` 表示 `self.context.messages` 在第 n 步时的状态。

```
Round N 开始
    ├─ msgs[0] = 当前 messages（可能已经包含前几轮 assistant + tool_result 的交替）
    ├─ _maybe_compact()
    │      ├─ compactor.micro(msgs[0])          # in-place 截断旧 tool_result
    │      └─ if needs_compact():
    │             ├─ hook: agent:compact:before  (observing)
    │             ├─ emitter.emit_compact(reason)
    │             ├─ compact() -> [summary_user, ack_assistant]
    │             ├─ if persist: session.rewrite_messages(compacted)
    │             │   else:       context.messages[:] = compacted
    │             └─ hook: agent:compact:after   (observing)
    │             # 注意：compact 后 msgs 可能只剩 2 条消息
    │
    ├─ input_messages_snapshot = [dict(m) for m in msgs[0]]
    │   # 深拷贝，仅用于 recorder 记录
    │
    ├─ _call_llm_with_retry()
    │      ├─ hook: prompt:llm:input  (observing)
    │      ├─ adapter.stream(model, system, messages=msgs[0], tools=self._schemas)
    │      │      └─ async for text in stream.text_stream:
    │      │             collected_tokens.append(text)
    │      │             # ⚠️ 此时 token 并没有 emit 给用户！
    │      └─ return (collected_tokens, response)
    │
    ├─ content = normalize_content_blocks(response.content)  # API response -> list[dict]
    ├─ recorder.record(round_num, input_messages_snapshot, content, stop_reason)
    ├─ _append({"role": "assistant", "content": content})
    │      └─ msgs[1] = msgs[0] + [assistant_msg]
    │
    ├─ round_text = "".join(b["text"] for b in content if b.get("type") == "text")
    │   # ⚠️ 只统计 type="text" 的 block。如果 assistant 这轮只返回 tool_use，
    │   #    或只有 thinking，round_text 为空
    ├─ if round_text:
    │      last_tokens = collected_tokens   # 覆盖！不是 append
    │      last_text = round_text           # 覆盖！
    │      hook: prompt:llm:output         # (observing)
    │
    ├─ if response.stop_reason != "tool_use":
    │      # 最终回复轮
    │      for token in last_tokens:
    │          await emitter.emit_token(token)    # 一次性补发之前缓存的 token！
    │      if is_orchestrator:
    │          hook: agent:stop  (observing)
    │          emitter.emit_done(last_text)
    │      else:
    │          emitter.emit_subagent_done(last_text)
    │      return last_text
    │
    └─ else:
           # 工具调用轮
           tool_results, trigger_compact = await _handle_tools(response.content)
           _append({"role": "user", "content": tool_results})
           │      └─ msgs[2] = msgs[1] + [user_msg_with_tool_results]
           │
           if trigger_compact:
               _do_compact("manual compact requested")
           # 继续下一轮 (Round N+1)
```

---

## 三、关键数据状态变化表

以一次完整的多轮对话为例：

| 阶段 | `len(msgs)` | 最后一条消息 role | 备注 |
|------|-------------|-------------------|------|
| 初始 | 0~N | varies | 可能是历史残留或全新会话 |
| `run("hello")` | +1 | `user` | 经过 `lib.on_message()` 包装，可能注入 skills catalog |
| `_loop()` Round 1 | +1 | `assistant` | 可能是 `tool_use` block 列表或纯文本 |
| `_handle_tools()` | +1 | `user` | content 是 `tool_result` 列表（`{"type":"tool_result", ...}`） |
| `_loop()` Round 2 | +1 | `assistant` | LLM 对 tool_result 的回应 |
| ... | | | |

### 3.1 `_append()` 的精确行为 (`agent.py:839-858`)

```python
def _append(self, message: dict):
    # Step 1: prompt lib 包装
    message = get_lib(self.prompt_version).on_message(
        message, self.session, self.context.messages,
        skills_override=self.skills_override,
    )

    # Step 2: 写入内存上下文
    self.context.messages.append(message)

    # Step 3: 条件写入磁盘
    if self.persist:
        self.session.persist_message(message)
```

**对于 user 消息：**
- `lib.on_message()` 会把 `{"role": "user", "content": "hello"}` 变成 `{"role": "user", "content": [{"type": "text", "text": "hello"}]}`
- 如果是第一条 user 消息，`build_user_message()` 通常还会注入 skills catalog 和 system reminder。

**对于 assistant 消息：**
- `lib.on_message()` 中 `message["role"] != "user"`，直接原样返回。
- 所以 assistant `content` 通常是 Anthropic API 原生的 block 列表：`[{"type": "text", "text": "..."}, {"type": "tool_use", ...}]`。

**缺失的 hook 点（精确位置）：**
- `agent.py:849` (`lib.on_message` 之后)：应该插入 `message:preprocessed`
- `agent.py:855` (`context.messages.append` 之后)：应该插入 `message:write:before`（modifying，可 block 持久化）

---

## 四、`_call_llm_with_retry()` 的隐藏问题

### 4.1 Token 收集与 Emit 的分离

```python
async with self.adapter.stream(...) as stream:
    async for text in stream.text_stream:
        collected_tokens.append(text)   # 只收集，不 emit
    response = await stream.get_final_message()
```

**关键发现：**
- `collected_tokens` 在整个流式过程中**从未被分段 emit 给用户**。
- 只有当 `stop_reason != "tool_use"`（最终轮）时，`last_tokens`（即最近一轮有文本的 `collected_tokens`）才会被一次性 `for token in last_tokens: emitter.emit_token(token)` 发出。

**这意味着对于 SSE 客户端：**
- 在工具调用轮，客户端**完全看不到任何流式输出**。
- 在最终回复轮，客户端看到的不是真正的实时流，而是**整轮文本被拆成 token 列表后逐个快速 flush 的"伪流式"**。

**对教学的启示：**
- 如果学生期望"每收到一个 token 就实时显示"，这个实现会让他们困惑。应该明确说明这是"round-level streaming" 而非 "true token-level streaming"。

### 4.2 Hook 插入点

`agent.py:585` (`adapter.stream(...)` 之前) 有 `prompt:llm:input` (observing)。但缺少：
- **`prompt:model:before`**（在 stream 调用前，可修改 model / max_tokens / temperature）
- **`prompt:build:before`**（在 stream 调用前，可修改 `self.system` 或 `messages`）

实现建议（精确代码位置 `agent.py:578-595` 之间）：

```python
# hook: prompt:build:before
build_hook = await self.session.hooks.emit(
    "prompt:build:before",
    {"system": self.system, "messages": [dict(m) for m in self.context.messages]},
    self._build_hook_ctx(),
)
system_for_llm = build_hook.updated_input.get("system") if build_hook.updated_input else self.system
messages_for_llm = build_hook.updated_input.get("messages") if build_hook.updated_input else self.context.messages

# hook: prompt:model:before
model_hook = await self.session.hooks.emit(
    "prompt:model:before",
    {"model": self.model, "max_tokens": 8000, "messages": messages_for_llm},
    self._build_hook_ctx(),
)
model_for_llm = model_hook.updated_input.get("model") if model_hook.updated_input else self.model
max_tokens_for_llm = model_hook.updated_input.get("max_tokens", 8000) if model_hook.updated_input else 8000

async with self.adapter.stream(
    model=model_for_llm,
    system=system_for_llm,
    messages=messages_for_llm,
    tools=self._schemas,
    max_tokens=max_tokens_for_llm,
) as stream:
    ...
```

---

## 五、`_handle_tools()` 的精确流程与真实缺陷

### 5.1 逐 block 处理时序

```
for each block in response.content:
    if block.type != "tool_use": continue

    name = block.name
    input_ = block.input
    block_id = block.id

    ├─ 权限检查 (if name in ask_tools)
    │      ├─ hook: tool:permission:request (modifying)
    │      │      ├─ if block -> 直接拒绝，append error result，continue 到下一个 block
    │      │      ├─ if behavior == "allow" -> 跳过弹窗，继续
    │      │      ├─ if behavior == "deny" -> 拒绝（已在上面的 block 处理，此处是 dead code）
    │      │      └─ if behavior in ("ask", "passthrough"):
    │      │             if interactive:
    │      │                 granted = emitter.emit_permission_request(name, input_)
    │      │                 if not granted:
    │      │                     hook: tool:permission:denied
    │      │                     append error result, continue
    │      │             else:
    │      │                 hook: tool:permission:denied
    │      │                 append error result, continue
    │      │             # ⚠️ "ask" 和 "passthrough" 在此处分支行为完全相同！
    │      │
    ├─ hook: tool:call:before (modifying)
    │      ├─ if block -> append error result, continue
    │      └─ if updated_input -> input_ = updated_input
    │
    ├─ emitter.emit_tool_start(name, preview)    # (A)
    │
    ├─ 执行工具 -> result
    │      ├─ Agent -> _handle_agent -> child._loop()
    │      ├─ AskUserQuestion -> _handle_ask_user -> emitter.emit_ask_user()
    │      ├─ Compact -> trigger_compact = True
    │      ├─ mcp__ -> _handle_mcp_tool
    │      └─ other -> tool(**input_)
    │
    ├─ emitter.emit_tool_result(name, result.content)   # (B)
    │
    ├─ hook: tool:call:after / tool:call:failure   # (C)
    │      # observing only
    │
    └─ results.append(result.to_api_dict(block_id))
```

### 5.2 真实缺陷：Hook 无法修改用户已看到的 Tool Result

注意事件顺序：**(B) emit_tool_result 发生在 (C) `tool:call:after` 之前**。

这意味着：
1. 工具执行完毕，结果已经通过 emitter 发给用户了。
2. 然后 `tool:call:after` hook 才被触发。
3. 即使 hook 想修改 `tool_response`（比如 scrub 密码），用户端的 `tool_result` 事件内容已经不可撤销。

**改进建议（交换顺序）：**

```python
# 当前代码（有缺陷）
await self.emitter.emit_tool_result(name, result.content)
if result.is_error:
    await self.session.hooks.emit_void("tool:call:failure", ...)
else:
    await self.session.hooks.emit_void("tool:call:after", ...)
results.append(result.to_api_dict(block_id))

# 改进后
# 1. 先触发 hook，给 hook 修改 result 的机会
tool_after_hook = await self.session.hooks.emit(
    "tool:call:after" if not result.is_error else "tool:call:failure",
    {"tool_name": name, ..., "tool_response": result.content},
    self._build_hook_ctx(),
)
if tool_after_hook.updated_input is not None:
    result = ToolResult.ok(tool_after_hook.updated_input)  # 或相应构造函数

# 2. 再 emit 修改后的结果
await self.emitter.emit_tool_result(name, result.content)
results.append(result.to_api_dict(block_id))
```

### 5.3 权限流中的代码 Smell

`agent.py:668`：

```python
elif behavior in ("ask", "passthrough"):
```

在这个分支内，`"ask"` 和 `"passthrough"` 的行为**完全相同**：interactive 模式下都弹窗，auto 模式下都拒绝。这说明 `"passthrough"` 这个值并没有起到"透传、不干预"的语义作用。

**Claude Code 的真实语义应该是：**
- `passthrough`：不干预，走默认逻辑（即 ask_tools 里的默认行为，interactive 弹窗 / auto 拒绝）。当前实现是对的，但命名上容易误导。
- 如果期望 `passthrough` = "直接允许，不弹窗"，那这是一个 bug。

**建议：** 要么把 `"passthrough"` 改成和 `"allow"` 一样的语义（hook 不干预 = 直接放行），要么在文档中明确 `"passthrough"` 的语义就是"还原为系统默认的权限检查"。

---

## 六、子代理（Subagent）的精确生命周期

### 6.1 `spawn_child()` 的构造细节 (`agent.py:230-350`)

```python
# 1. 构建初始消息（已经过 lib.on_message 处理）
initial_message = get_lib(self.prompt_version).on_message(
    {"role": "user", "content": prompt}, self.session, [],
    skills_override=child_skills_override,
)

# 2. 创建独立的 AgentContext
child_context = AgentContext(
    name=agent_name,
    messages=[initial_message],    # ⚠️ 全新列表，和 session.messages 无关
    depth=self.context.depth + 1,
    parent_id=self.context.agent_id,
    parent_name=self.context.name,
)

# 3. 工具过滤（5 层决策）
...

# 4. 可能包装 FilterEmitter
child_emitter = self.emitter
if agent_def is not None and agent_def.output_mode:
    child_emitter = FilterEmitter(self.emitter, mode=agent_def.output_mode)

# 5. 创建子 Agent
child = Agent(
    session=self.session,            # ⚠️ 共享同一个 Session 对象
    emitter=child_emitter,
    context=child_context,           # 但 context 是独立的
    persist=False,                   # 不持久化
    ...
)
```

**重要观察：**
- `session` 对象是共享的，但 `child.context.messages` 是独立列表。
- 由于 `persist=False`，子 agent 的 `_append()` 不会写入磁盘，`_do_compact()` 也不会调用 `session.rewrite_messages()`。
- 但子 agent 仍然可以访问 `self.session.tasks`、`self.session.hooks`、`self.session.mcp` 等共享资源。

### 6.2 `_handle_agent()` 的完整时序 (`agent.py:759-803`)

```python
async def _handle_agent(self, task_input: dict) -> ToolResult:
    # 1. 深度检查
    if self.context.depth >= MAX_DEPTH:
        return ToolResult.error(...)

    # 2. 查找 agent_def
    agent_def = self.session.agents.get(subagent_type) if subagent_type else None

    # 3. 构造子代理
    child = self.spawn_child(prompt, agent_def=..., agent_name=..., model_override=...)

    # 4. hooks（observing）
    await self.session.hooks.emit_void("subagent:spawning", {}, child._build_hook_ctx())
    await self.session.hooks.emit_void("subagent:spawned", {...}, child._build_hook_ctx())

    # 5. 运行子代理循环
    summary = await child._loop()

    # 6. 结束 hook
    logger.info("Child agent done ...")
    await self.session.hooks.emit_void("subagent:ended", {...}, child._build_hook_ctx())

    # 7. 返回结果给父 agent
    return ToolResult.ok(summary or "(no summary)")
```

**缺失点：**
- 步骤 5 和 6 之间，没有任何 hook 可以对 `summary` 做后处理。
- `child._loop()` 内部触发的所有 hook（`tool:call:before`、`agent:limit` 等）和根 agent **共用同一个 namespace**。虽然 `HookContext.depth` 可以区分，但如果想为子代理类型绑定专属 hook（例如只对 "coder" 子代理做审计），没有 `subagent:loop:before/after` 这样更细粒度的点。

**建议新增：**

```python
# 在 child._loop() 调用前
await self.session.hooks.emit_void(
    "subagent:loop:before",
    {"subagent_type": subagent_type, "prompt": prompt},
    child._build_hook_ctx(),
)

summary = await child._loop()

# 在 child._loop() 调用后
final_hook = await self.session.hooks.emit(
    "subagent:result:finalizing",
    {"summary": summary, "subagent_type": subagent_type},
    child._build_hook_ctx(),
)
if final_hook.message is not None:
    summary = final_hook.message
```

---

## 七、Round Limit 与 `last_tokens` 的隐患

### 7.1 `last_tokens` 覆盖逻辑

```python
round_text = "".join(b["text"] for b in content if b.get("type") == "text")
if round_text:
    last_tokens = collected_tokens   # 覆盖！
    last_text = round_text           # 覆盖！
```

**隐患：如果最终轮 assistant 只返回了 `tool_use` 或者 thinking blocks（没有 `type="text"`）**

场景示例：
- Round 1：assistant 输出 "Let me search for that." + tool_use(Bash)
  - `last_tokens` = ["Let", " me", ...]
  - `last_text` = "Let me search for that."
- Round 2：assistant 直接 tool_use(WebSearch)，没有文本前缀
  - `round_text` = ""
  - `last_tokens` 保持 Round 1 的内容！
- Round 3（最终轮）：assistant 输出 "Done."
  - `last_tokens` = ["Done", "."]
  - 正常。

但如果因为某种原因，**最终轮也没有 `type="text"`**（比如直接返回空 thinking 结束），`_loop` 会执行到 `stop_reason != "tool_use"` 分支：

```python
for token in last_tokens:
    await self.emitter.emit_token(token)   # 发出 Round 1 的 "Let me search for that."
return last_text                           # 返回 "Let me search for that."
```

这会把**过时内容**当作最终回复返回给用户。虽然 cc_reverse prompt lib 几乎不会出现这种情况，但从通用循环引擎角度看，这是一个边界 bug。

**修复建议：**

```python
if round_text:
    last_tokens = collected_tokens
    last_text = round_text
else:
    # 即使 round_text 为空，也要保留 token 记录，供无文本的最终轮使用
    last_tokens = collected_tokens  # 或至少给一个空列表的备选逻辑
```

但更根本的修复是：**如果 stop_reason != "tool_use" 且 last_text 为空，应该 emit_error 而不是 emit 上一轮的 token**。

### 7.2 `_on_limit_ask_user` 的递归重入

```python
self.context.messages.append({"role": "user", "content": "继续执行未完成的任务。"})
self.round_limit = self.round_limit  # NO-OP！
return await self._loop()            # 递归调用
```

问题：
1. `self.round_limit = self.round_limit` 是无用的自赋值。
2. 递归调用 `_loop()` 意味着如果用户连续多次点"继续"，调用栈会不断加深。虽然 Python 默认递归限制是 1000，但这仍然是一个潜在风险。
3. 没有重置 `last_tokens` 和 `last_text`，不过它们会随新一轮正常更新，所以不是大问题。

**改进建议（迭代替代递归）：**

```python
# 在 _loop 中引入一个外层 while 循环，而不是 for + 递归
async def _loop(self) -> str:
    last_tokens = []
    last_text = ""
    while True:   # 外层循环，支持 ask_user 后继续而不递归
        for round_num in range(self.round_limit):
            ...
            if response.stop_reason != "tool_use":
                ...
                return last_text
            ...
        # round limit 到达
        result = await self._on_limit(last_text, last_tokens)
        if result == "__CONTINUE__":   # ask_user 选择继续
            continue   # 回到 while True，重新开始 for 循环
        return result
```

---

## 八、Emitter 与 Hook 的深层耦合问题

### 8.1 当前架构的问题

当前 Agent 中，Hook 和 Emitter 的调用是**交错硬编码**的：

```python
# _loop 最终轮
for token in last_tokens:
    await self.emitter.emit_token(token)
await self.session.hooks.emit_void("agent:stop", ...)
await self.emitter.emit_done(last_text)

# _handle_tools 中
await self.session.hooks.emit("tool:call:before", ...)
await self.emitter.emit_tool_start(name, preview)
result = await tool(...)
await self.emitter.emit_tool_result(name, result.content)
await self.session.hooks.emit_void("tool:call:after", ...)
```

**导致的后果：**
1. Agent 类同时要负责"业务逻辑"和"编排 hook + emitter 的顺序"，违反了 SRP。
2. 如果未来想新增一个 emitter 事件（如 `emit_thinking_start`），必须同时修改 `Agent._loop` 和可能的多处调用点。
3. `message:outbound:sending` 这个 hook 完全没有落点，因为 Agent 的代码里没有"统一的发送出口"。

### 8.2 建议的重构：EmitterPhaseRouter

引入一个中间层，把"事件发送"和"hook 触发"统一封装：

```python
class EmitterPhaseRouter:
    """
    包装 Agent.emitter，在发送给客户端之前/之后自动触发对应的 outbound hook。
    """
    def __init__(self, inner: BaseEmitter, agent: "Agent"):
        self._inner = inner
        self._agent = agent

    async def emit_token(self, text: str):
        event = self._inner.fmt_token(text)
        await self._route_outbound(event)

    async def emit_tool_start(self, name: str, preview: str):
        event = self._inner.fmt_tool_start(name, preview)
        await self._route_outbound(event)

    async def emit_tool_result(self, name: str, output: str):
        event = self._inner.fmt_tool_result(name, output)
        await self._route_outbound(event)

    async def emit_done(self, content: str):
        event = self._inner.fmt_done(content)
        await self._route_outbound(event)

    async def _route_outbound(self, event: dict):
        # 1. 触发 message:outbound:sending (modifying)
        hook_result = await self._agent.session.hooks.emit(
            "message:outbound:sending",
            {"event": event},
            self._agent._build_hook_ctx(),
        )
        if hook_result.block:
            logger.debug("Hook blocked outbound event | type={}", event.get("type"))
            return
        if hook_result.updated_input:
            event = hook_result.updated_input

        # 2. 发给真实 emitter
        await self._inner.emit(event)

        # 3. 触发 message:outbound:sent (observing)
        await self._agent.session.hooks.emit_void(
            "message:outbound:sent",
            {"event": event},
            self._agent._build_hook_ctx(),
        )
```

**改造后 Agent 的简化：**

```python
# Agent.__init__ 中
self.emitter = EmitterPhaseRouter(emitter, self)

# _loop 最终轮
for token in last_tokens:
    await self.emitter.emit_token(token)
await self.session.hooks.emit_void("agent:stop", ...)
await self.emitter.emit_done(last_text)

# _handle_tools 中
# 删除显式的 tool:call:after emit_void 调用，原样保留
# emit_tool_start / emit_tool_result 由 router 自动处理 hook
```

**例外处理：**
- `emit_ask_user` 和 `emit_permission_request` 不应该走 `message:outbound:sending`，因为 block 会导致 agent 永久挂起。这两个方法保留直接调用 `self._inner.emit_ask_user()`。

---

## 九、Subagent Loop 复用的具体落地建议

### 9.1 当前问题：每次 `spawn_child` 都重建 schemas

```python
child = Agent(
    session=self.session,
    adapter=self.adapter,
    emitter=child_emitter,
    tools=child_tools,
    disabled_tools=disabled_child_tools,
    ...
)

child._schemas = get_lib(child.prompt_version).patch_tool_schemas(child._schemas)
```

对于同一个 `agent_def`，`child_tools`、`disabled_child_tools`、最终 `child._schemas` 都是**确定性的**。每次调用都重新构造，有一定开销。

### 9.2 建议：把 `spawn_child` 拆成 "Context 构建" 和 "实例化"

```python
def build_child_context(self, prompt: str, agent_def=None, agent_name=None, ...):
    """只构建子代理的上下文和配置，不创建 Agent 实例。"""
    ...
    return {
        "context": child_context,
        "tools": child_tools,
        "disabled_tools": disabled_child_tools,
        "schemas": child_schemas,
        "model": child_model,
        "emitter": child_emitter,
    }

def spawn_child(self, prompt: str, **kwargs):
    config = self.build_child_context(prompt, **kwargs)
    return Agent(
        session=self.session,
        adapter=self.adapter,
        emitter=config["emitter"],
        tools=config["tools"],
        disabled_tools=config["disabled_tools"],
        context=config["context"],
        model=config["model"],
        _schemas=config["schemas"],    # 允许传入预计算的 schemas
        ...
    )
```

**教学价值：**
- 学生可以清晰看到"子代理 = 父代理的过滤视图 + 独立上下文"。
- `build_child_context` 可以被 Graph/Pipeline 调用，用于预校验或缓存。

### 9.3 更进一步的轻量复用（可选）

如果子代理被频繁调用（如 pipeline 中的循环 QC 节点），可以引入 `AgentTemplate`：

```python
@dataclass
class AgentTemplate:
    """子代理的'模具'，缓存了除初始消息和 emitter 之外的所有配置。"""
    tools: dict
    disabled_tools: dict
    schemas: list
    model: str
    round_limit: int
    limit_strategy: str
    prompt_version: str
    skills_override: list
    system: list   # 预构建的 system prompt

    def instantiate(self, session, adapter, emitter, initial_message, depth, parent_id):
        ctx = AgentContext(messages=[initial_message], depth=depth, parent_id=parent_id)
        return Agent(session, adapter, emitter, tools=self.tools, ...)
```

> 注意：**不缓存 Agent 实例本身**，因为 Agent 实例包含异步状态（messages 列表引用）。只缓存配置（tools, schemas, system），这样可以显著减少 `lib.build_system()` 等开销。

---

## 十、急需修复的代码缺陷清单（按优先级）

| P | 问题 | 位置 | 修复方式 |
|---|------|------|----------|
| P0 | `emit_tool_result` 在 `tool:call:after` 之前，hook 无法 scrub 输出 | `agent.py:741-754` | 交换顺序：先 hook，后 emit_tool_result |
| P0 | `last_tokens` 可能被过时内容覆盖/误用 | `agent.py:383-386` | 如果最终轮为空文本，不应该返回上一轮内容；为空时应 emit_error |
| P1 | `_handle_command` 的 `clear` 清空后又 append 自身 | `agent.py:223-227` | `clear` 后直接 `return ""`，跳过后续 `_append` |
| P1 | `_call_llm_with_retry` 中 token 只收集不实时 emit | `agent.py:591-593` | 文档化说明；若要真流式，需重构 `_loop` 为逐 token 决策 |
| P1 | `_on_limit_ask_user` 递归调用 `_loop` 可能导致栈溢出 | `agent.py:513` | 外层引入 while 循环，用 `continue` 替代递归 |
| P2 | `self.round_limit = self.round_limit` 无意义 | `agent.py:511` | 删除或改为 `self._rounds_executed = 0` 的重置逻辑 |
| P2 | `passthrough` 与 `ask` 在权限逻辑中行为完全相同 | `agent.py:668` | 明确语义并在文档/代码中区分 |

---

## 十一、建议补全的 Hook 点（精确位置与代码）

### 11.1 `message:preprocessed`

```python
# agent.py:849 之后
tool_after_hook = await self.session.hooks.emit(
    "message:preprocessed",
    {"message": message, "role": message.get("role")},
    self._build_hook_ctx(),
)
if tool_after_hook.updated_input is not None:
    message = tool_after_hook.updated_input
```

### 11.2 `message:write:before`

```python
# agent.py:855 之后（context.messages.append 之后，persist 之前）
if self.persist:
    write_hook = await self.session.hooks.emit(
        "message:write:before",
        {"message": message},
        self._build_hook_ctx(),
    )
    if not write_hook.block:
        self.session.persist_message(message)
```

### 11.3 `prompt:build:before` 和 `prompt:model:before`

见 **第 4.2 节**。

### 11.4 `agent:bootstrap`

```python
# agent.py:354 (_loop 开头)
async def _loop(self) -> str:
    bootstrap_hook = await self.session.hooks.emit(
        "agent:bootstrap",
        {"tools": list(self.tools.keys()), "schemas": self._schemas},
        self._build_hook_ctx(),
    )
    if bootstrap_hook.updated_input and "schemas" in bootstrap_hook.updated_input:
        self._schemas = bootstrap_hook.updated_input["schemas"]
    ...
```

### 11.5 `tool:result:persist`

```python
# agent.py:755 (results.append 之前)
persist_hook = await self.session.hooks.emit(
    "tool:result:persist",
    {"tool_name": name, "tool_result": result.to_api_dict(block_id)},
    self._build_hook_ctx(),
)
if persist_hook.updated_input is not None:
    result = ToolResult.from_api_dict(persist_hook.updated_input)
```

### 11.6 `subagent:loop:before` / `subagent:result:finalizing`

见 **第 6.2 节**。

---

## 十二、面向教学的最终建议

1. **给 `_loop` 增加“生命周期注释标号”**
   在代码的每个阶段用统一格式的注释标记，例如 `#[L1] #[L2]`，并配套 `docs/agent_loop_phases.md` 解释每个标号的含义。这让初学者可以按图索骥。

2. **把 `normalize_content_blocks` 的变换逻辑显式化**
   当前 `response.content` 经过 `normalize_content_blocks` 后变成了什么格式，对初学者并不透明。建议在关键步骤加一个断言或日志打印格式化后的结构。

3. **维护一个“消息内容格式演变图”**
   从用户输入 `str`，到 `lib.on_message()` 后的 `list[dict]`，到 LLM 返回的 API 格式，再到 `tool_result` 的 API 格式，画出一张表格。这是理解整个 loop 最关键的数据契约。

4. **（可选）增加 `AGENT_LOOP_TRACE` 环境变量**
   当设置 `AGENT_LOOP_TRACE=1` 时，`_loop` 的每一轮都打印 `messages` 的 JSON 摘要和 emitter 事件序列。这对远程调试和学生自学极有价值。

