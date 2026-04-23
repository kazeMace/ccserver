# ccserver Agent Loop 流式机制深度分析与重构方案

> Date: 2026/04/11  
> 目标：回答"如果不使用现在的 round-level token buffering 机制，如何实现只返回最后一条消息？"，并对比 Claude Code 原生逐 token streaming 的差异，给出两种可落地的改造方案。

---

## 一、Claude Code 原生行为的流式机制

### 1.1 原生行为的用户体验

在 Claude Code (Anthropic 官方 CLI) 中，用户体验是这样的：

```text
[用户输入] 帮我改一下这个 bug

Assistant: 让我先看一下代码...
            ~（token 逐个出现）~
            
            ### 工具调用：Read file.py
            （停顿，执行工具）
            
            好的，我看到了问题。需要把第 42 行的...
            ~（token 继续逐个出现）~
```

### 1.2 原生行为的技术实现

Claude Code 使用的是 Anthropic SDK 的 `stream=True` 模式，其技术特征是：

1. **单次 API call = 一个持续的 SSE stream**。Assistant 输出的文本 token 在到达时立即写入 stdout。
2. **Tool use 会自然中断 stream**。当模型输出 `tool_use` block 时，API 返回 `stop_reason="tool_use"`，stream 结束。
3. **工具执行期间无输出**。执行 Bash/Read/Edit 等工具时，终端上不再出现新 token。
4. **第二轮 stream 无缝衔接**。把 `tool_result` 加入 messages 后，立即发起下一轮 `stream=True` 调用，新的 token 继续实时涌出。

**关键数据流：**

```text
API stream (Round 1)
    ├─ "Let"          -> stdout
    ├─ " me"         -> stdout
    ├─ " look"       -> stdout
    └─ tool_use(Bash) -> stop_reason="tool_use", stream 结束
    
执行 Bash...

API stream (Round 2)
    ├─ "I"           -> stdout
    ├─ " found"      -> stdout
    ├─ " the"        -> stdout
    └─ ...           -> stop_reason="end_turn", stream 结束
```

**结论：Claude Code 是 "true token-level streaming"，stream 的生命周期绑定在单次 API call 上，而不是绑定在整个 agent loop 上。**

---

## 二、ccserver 当前的 "round-level buffering" 机制

### 2.1 当前 `_call_llm_with_retry()` 的实现

```python
# agent.py:585-595
async with self.adapter.stream(...) as stream:
    async for text in stream.text_stream:
        collected_tokens.append(text)    # 只 append，不 emit
    response = await stream.get_final_message()
return collected_tokens, response
```

### 2.2 当前 `_loop()` 的 emit 策略

```python
# agent.py:394-411
if response.stop_reason != "tool_use":
    # 最终轮：把"最近一次有文本的轮次"的 token 一起 emit
    for token in last_tokens:
        await self.emitter.emit_token(token)
    await self.emitter.emit_done(last_text)
    return last_text

# tool_use 轮：完全不 emit token
tool_results, trigger_compact = await self._handle_tools(response.content)
self._append({"role": "user", "content": tool_results})
# 直接下一轮...
```

### 2.3 当前机制的问题

**问题 1：工具调用轮如果包含引导文本，用户完全看不到**

假设 Round 1 的 assistant 输出：

```json
[
  {"type": "text", "text": "Let me search for that."},
  {"type": "tool_use", "name": "WebSearch", "input": {"query": "..."}}
]
```

在 Claude Code 中，用户会实时看到 `"Let me search for that."`，然后看到工具调用。
但在 ccserver 中，这些 token 被收集到 `collected_tokens`，但因为 `stop_reason == "tool_use"`，当前轮次不会 emit。它们只在 `last_tokens = collected_tokens` 中被暂存，等到**最终回复轮**时才和前序的 `last_tokens` 一起被覆盖或播放。

**问题 2：最终轮 emit 的是"历史 token"，不是"当前正在生成"的实时感**

假设对话：
- Round 1: "Let me search." + tool_use —> `collected_tokens` = ["Let", " me", ...], `last_tokens` 被更新
- Round 2: tool_result 回来，assistant 输出最终答案 "Done."

此时 `_loop` 的 ending 分支执行：
```python
for token in last_tokens:    # 播放 Round 1 的 "Let me search."？不！
```

等等，实际上 `last_tokens` 会在 Round 2 被覆盖为 Round 2 的 `collected_tokens`（假设 Round 2 有文本）。所以如果 Round 2 有 "Done"，`last_tokens` 就是 ["Done", "."]。

但如果 Round 2 是纯 tool_use（比如 assistant 又调了一个工具）或者 `round_text=""`（比如只有 thinking block），`last_tokens` 不会被覆盖，仍然保留着 Round 1 的 "Let me search."。到了最终轮时，就会把 Old Token 当作最终回复发出去——这正是 `agent_loop_deep_dive.md` 里提到的边界 bug。

**问题 3：`emit_done` 只包含 `last_text`（纯文本），不包含完整的 block 列表**

如果 assistant 的最终回复里掺杂了其他 block（如 `thinking`），`last_text` 只是文本部分的拼接，`emit_done` 会丢失这些信息。

---

## 三、方案 A：完全非流式 —— "只返回最后一条消息"

如果产品需求是"像普通 HTTP API 一样，只返回最终答案"，最简单的方式是**彻底移除 `_loop` 中的 token buffering 和 last_tokens 逻辑**。

### 3.1 方案 A 的改动点

#### 改动 1：`_call_llm_with_retry` —— 不再收集 tokens

```python
async def _call_llm_with_retry(self):
    # ... 重试逻辑不变 ...
    try:
        # 不再收集 token
        async with self.adapter.stream(...) as stream:
            # 对于非流式需求，可以直接用 create() 不用 stream()
            # 但为了改动最小，也可以保留 stream() 但不收集
            async for _ in stream.text_stream:
                pass
            response = await stream.get_final_message()
        return response  # 直接返回 response，不再带 collected_tokens
    except ...
```

更好的做法：直接用非流式 API：

```python
response = await self.adapter.create(
    model=self.model,
    system=self.system,
    messages=self.context.messages,
    tools=self._schemas,
    max_tokens=8000,
)
return response
```

#### 改动 2：`_loop` —— 移除 `last_tokens` / `last_text` / `collected_tokens`

```python
async def _loop(self) -> str:
    for round_num in range(self.round_limit):
        await self._maybe_compact()
        input_messages_snapshot = [dict(m) for m in self.context.messages]

        # 直接返回 response，不再带 tokens
        response = await self._call_llm_with_retry_non_stream()
        if response is None:
            await self.session.hooks.emit_void("agent:stop:failure", ...)
            return ""

        content = normalize_content_blocks(response.content)
        self.recorder.record(...)
        self._append({"role": "assistant", "content": content})

        if response.stop_reason != "tool_use":
            final_text = extract_text(content)  # 只取文本部分作为返回值
            if self.context.is_orchestrator:
                await self.session.hooks.emit_void("agent:stop", ...)
                await self.emitter.emit_done(final_text)
            else:
                await self.emitter.emit_subagent_done(final_text)
            return final_text

        tool_results, trigger_compact = await self._handle_tools(response.content)
        self._append({"role": "user", "content": tool_results})
        if trigger_compact:
            await self._do_compact(...)

    # round limit
    return await self._on_limit(...)
```

#### 改动 3：`_on_limit` 系列方法 —— 移除 `last_tokens` 参数

`_on_limit` 及其子方法（`_on_limit_ask_user`、`_on_limit_graceful` 等）原本接收 `(last_text, last_tokens)`，现在只接收 `last_text`。

#### 改动 4：Emitter 层 —— 移除 `emit_token`

如果彻底非流式，`BaseEmitter.fmt_token` / `emit_token` 可以保留但不再被调用，或完全移除。

CollectEmitter 的工作方式本来就是 `events.append({"type": "done", "content": ...})`，所以改动很小。

### 3.2 方案 A 的时序图

```text
Agent.run("hello")
    ├─ _append(user_msg)
    ├─ _loop()
    │      ├─ Round 1: create_llm() -> response("Let me search." + tool_use)
    │      ├─ _append(assistant_msg)
    │      ├─ _handle_tools() -> tool_results
    │      ├─ _append(user_msg_with_tool_results)
    │      ├─ Round 2: create_llm() -> response("Done.")
    │      ├─ _append(assistant_msg)
    │      └─ emit_done("Done.")          # 只有这一个 emitter 事件
    └─ return "Done."
```

### 3.3 方案 A 的优缺点

**优点：**
- 代码最简单，`_loop` 里没有 token 状态管理，教学理解成本低。
- 完全兼容 `CollectEmitter`（普通 HTTP 响应）和 `SSEEmitter`（虽然只有一次 done 事件）。
- 不存在 `last_tokens` 过时 bug。
- 可以改用更便宜的 `create()` API（有些模型流式 API 比非流式慢或贵）。

**缺点：**
- 用户体验差：用户必须等到所有工具调用执行完毕后，才能看到任何文字。如果工具执行耗时 10 秒，这段时间前端完全空白。
- 无法支持真正的打字机效果（TUI/Web 端）。

---

## 四、方案 B：真正的逐 token streaming —— 像 Claude Code 一样

如果产品需求是"用户能看到 assistant 在实时打字"，需要把 emit 的粒度从"round-level"降到"token-level"。

### 4.1 方案 B 的核心思路

**在 `stream.text_stream` 的 `async for` 循环中实时 emit token，而不是收集到列表末尾再批量 flush。**

但这会破坏 `_call_llm_with_retry` 的职责边界（它原来只负责 LLM 调用，不负责 emitter）。所以需要一个更清晰的职责划分：

```
旧架构：
_call_llm_with_retry() 返回 (collected_tokens, response)
_loop 决定什么时候 emit 这些 tokens

新架构：
_call_llm_streaming() 返回一个 async generator：一边 consume stream token，一边实时 emit
_loop 只负责在 stream 结束后拿到 response
```

### 4.2 方案 B 的具体改造

#### 4.2.1 新增 `_stream_llm_with_retry()`

```python
async def _stream_llm_with_retry(self):
    """
    带重试的 LLM 流式调用。
    在 token 到达时立即通过 emitter 推送。
    返回最终 response。
    """
    import asyncio
    from anthropic import APIConnectionError, APITimeoutError

    max_retries = 3
    retry_delays = [2, 5, 10]
    for attempt in range(max_retries):
        try:
            await self.session.hooks.emit_void(
                "prompt:llm:input",
                {"messages": [dict(m) for m in self.context.messages], "model": self.model},
                self._build_hook_ctx(),
            )
            async with self.adapter.stream(
                model=self.model,
                system=self.system,
                messages=self.context.messages,
                tools=self._schemas,
                max_tokens=8000,
            ) as stream:
                async for text in stream.text_stream:
                    # 立即 emit token！
                    await self.emitter.emit_token(text)
                response = await stream.get_final_message()
            return response
        except (APIConnectionError, APITimeoutError) as e:
            if attempt < max_retries - 1:
                await asyncio.sleep(retry_delays[attempt])
            else:
                await self.emitter.emit_error(str(e))
                return None
        except Exception as e:
            await self.emitter.emit_error(str(e))
            return None
```

#### 4.2.2 简化 `_loop()` —— 移除 `last_tokens` / `last_text`

```python
async def _loop(self) -> str:
    logger.debug("Loop start | agent={} depth={} msgs={}", ...)
    for round_num in range(self.round_limit):
        await self._maybe_compact()
        logger.debug("Round {}/{} | agent={}", round_num + 1, self.round_limit, self.aid_label)

        input_messages_snapshot = [dict(m) for m in self.context.messages]
        response = await self._stream_llm_with_retry()
        if response is None:
            await self.session.hooks.emit_void("agent:stop:failure", ...)
            return ""

        content = normalize_content_blocks(response.content)
        self.recorder.record(round_num + 1, input_messages_snapshot, content, response.stop_reason)
        self._append({"role": "assistant", "content": content})

        round_text = "".join(b["text"] for b in content if b.get("type") == "text")
        if round_text:
            await self.session.hooks.emit_void("prompt:llm:output", {"reply": round_text}, ...)

        if response.stop_reason != "tool_use":
            logger.debug("Loop done | agent={} rounds={}", ...)
            if self.context.is_orchestrator:
                await self.session.hooks.emit_void("agent:stop", {"reply": round_text}, ...)
                await self.emitter.emit_done(round_text)
            else:
                await self.emitter.emit_subagent_done(round_text)
            return round_text

        # tool_use 轮：stream 已经实时 emit 了引导文本，这里不需要额外处理 token
        tool_results, trigger_compact = await self._handle_tools(response.content)
        self._append({"role": "user", "content": tool_results})
        if trigger_compact:
            await self._do_compact(...)

    # round limit
    # 此时 last_text 可以从最近一轮的 response 中提取
    last_text = self._extract_last_text()
    logger.warning("Round limit reached | agent={} limit={}", ...)
    return await self._on_limit(last_text, [])
```

**关键变化：**
- `last_tokens` 和 `last_text` 变量被完全删除。
- token 在 `_stream_llm_with_retry` 的 `async for` 中实时 emit。
- `_handle_tools` 之前不再需要 `for token in last_tokens: emit_token(token)`。
- `emit_done` 仍然只在最终轮调用一次。

#### 4.2.3 关于 `round_text` 为空的问题

如果某一轮只有 `tool_use` 没有引导文本，`_stream_llm_with_retry` 不会 emit 任何 token（因为 `text_stream` 不会有文本 token），这是正确的行为。

如果 Round N 是最终轮，但 assistant 没有返回 `type="text"`（比如返回空 thinking 或直接 stop），`round_text` 为空，`emit_done("")` 也是合理的。

**旧 bug 被自然消除：** 不再依赖 `last_tokens` 变量，不存在"把上一轮 token 当作最终回复"的问题。

#### 4.2.4 `_on_limit` 的适配

```python
async def _on_limit(self, last_text: str) -> str:
    # ... hook agent:limit ...
    # 不需要 last_tokens 了
    # 如果是 last_text 策略，直接 emit_done(last_text)
```

### 4.3 方案 B 的时序图

```text
Agent.run("hello")
    ├─ _append(user_msg)
    ├─ _loop()
    │      ├─ Round 1: _stream_llm_with_retry()
    │      │      ├─ API: "Let"          -> emitter.emit_token("Let")
    │      │      ├─ API: " me"         -> emitter.emit_token(" me")
    │      │      ├─ API: " search"     -> emitter.emit_token(" search")
    │      │      └─ API: tool_use      -> stream 结束，return response
    │      ├─ _append(assistant_msg)
    │      ├─ _handle_tools()
    │      │      ├─ emit_tool_start(WebSearch)
    │      │      ├─ 执行 WebSearch
    │      │      └─ emit_tool_result(...)
    │      ├─ _append(user_msg_with_tool_results)
    │      ├─ Round 2: _stream_llm_with_retry()
    │      │      ├─ API: "I"           -> emitter.emit_token("I")
    │      │      ├─ API: " found"      -> emitter.emit_token(" found")
    │      │      └─ API: end_turn      -> stream 结束
    │      ├─ _append(assistant_msg)
    │      └─ emitter.emit_done("I found...")
    └─ return "I found..."
```

### 4.4 方案 B 的优缺点

**优点：**
- UI 体验与 Claude Code 原生一致：文本 token 实时可见，工具调用前有自然的打字机效果。
- 消除了 `last_tokens` 过时 bug。
- 代码更简洁：`_loop` 不再需要维护 `last_tokens` 状态。

**缺点：**
- `recorder` 不再能记录"本轮收集到的 token 列表"（但可以改为在 stream consumer 中边 emit 边记录）。
- 如果 `FilterEmitter` 模式是 `final_only`（只保留 done/error），实时 emit 的 token 会被丢弃，但逻辑上仍然正确（只是用户看不到实时内容）。
- `subagent_done` 的 summary 仍然是整段文本一次性 emit，这是合理的行为。

---

## 五、两种方案的可复用 Emitter 设计

### 5.1 当前 Emitter 与两种方案的兼容性

| Emitter | 方案 A (非流式) | 方案 B (真流式) | 说明 |
|---------|-----------------|-----------------|------|
| `CollectEmitter` | 完全适用 | 适用，但内存中会积累大量 token 事件 | `final_only` 场景可用方案 A 优化 |
| `SSEEmitter` | 适用，但用户只看到最后的 done | 适用，用户体验最佳 | 方案 B 是 SSE 的理想搭档 |
| `TUIEmitter` | 适用 | 适用 | TUI 通常需要实时更新光标 |
| `FilterEmitter` | 适用 | 适用 | token 被 filter 掉不影响逻辑正确性 |

### 5.2 "只返回最后一条消息" 在 Graph Pipeline 中的价值

在 `pipeline/graph.py` 中，AgentNode 使用 `_NullEmitter`（丢弃所有事件）或外部传入的 emitter：

```python
# graph.py:352-356
agent.tools.pop("Task", None)
agent._schemas = [t.to_schema() for t in agent.tools.values()]
final_text = await agent.run(prompt)
return NodeData(data={node.output_key: final_text}, from_node=node.id)
```

对于 Graph 中的 AgentNode，前端通常只关心 `final_text`（即 `emit_done` 的内容），对中间 token 不感兴趣。

**因此建议：**
- `Agent` 支持一个 `stream: bool = True` 构造参数。
- `AgentRunner.run()` (根 agent) 使用 `stream=True`（给用户最佳体验）。
- `Graph._run_agent_node()` 使用 `stream=False`（走方案 A 的非流式路径，减少 overhead）。

这是一个干净的教学级抽象：

```python
class Agent:
    def __init__(..., stream: bool = True):
        self.stream = stream
```

---

## 六、推荐的落地路径

### Phase 1：快速切换（低代码改动）

直接在 `_loop` 中根据 `self.stream` 做分支：

```python
async def _loop(self) -> str:
    if not self.stream:
        return await self._loop_non_stream()   # 方案 A
    return await self._loop_stream()            # 方案 B
```

但这会导致代码重复。更好的做法是：

### Phase 2：重构 `_call_llm_xxx` 的接口

```python
# 统一接口，不论是否流式
async def _call_llm(self) -> tuple[str | None, Any]:
    """
    调用 LLM。
    流式模式下实时 emit token，返回 (None, response)。
    非流式模式下不 emit token，返回 (round_text, response)。
    """
```

### Phase 3：彻底移除 `last_tokens`

这是最关键的一步。一旦改成真流式，`last_tokens` 和 `_on_limit` 里对 `last_tokens` 的使用都应该删除。

---

## 七、结论

**"只返回最后一条消息"（方案 A）** 是最容易落地的：
1. 把 `_call_llm_with_retry` 改成非流式 `create()` 调用。
2. 删除 `_loop` 里的 `last_tokens` 和 `last_text` 变量。
3. 最终轮直接 `emit_done(round_text)`。

**如果要追求 Claude Code 原生的用户体验（方案 B）**：
1. 在 `stream.text_stream` 的 `async for` 里实时 `emit_token(text)`。
2. 删除 `_loop` 末尾的 `for token in last_tokens: emit_token(token)`。
3. 其余逻辑基本不变。

**最佳工程实践**：让 `Agent` 支持 `stream: bool` 参数，根 agent 默认 `stream=True`，Graph Pipeline 中的 agent node 默认 `stream=False`。这样兼顾了前端体验和后台效率。

