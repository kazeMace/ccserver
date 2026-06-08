# Agent Loop 问题梳理

**日期：** 2026-04-23  
**分析范围：** ccserver/agent.py, ccserver/pipeline/graph.py, ccserver/emitters/

---

## 一、逻辑缺陷（P1）

### 1. `_drain_inbox_and_respond()` 双重循环冗余
**位置：** `agent.py` 第 807-884 行

代码对同一个 `inbox` 调用了两次 `get_nowait()` 轮询循环：
- 第一个循环仅处理 `status_request`，处理后 break
- 第二个循环再次处理包括 `status_request` 在内的全部消息

**影响：** 第一个循环消费了 `status_request` 消息后，如果 inbox 已空，第二个循环可能拿不到其他消息；逻辑不清晰，容易遗漏消息。  
**建议：** 合并为一个循环，统一处理所有消息类型。

---

### 2. `_on_limit_ask_user()` 中 round_limit 赋值无效
**位置：** `agent.py` 第 1050-1069 行

```python
self.round_limit = self.round_limit  # 无效操作
return await self._loop()  # 递归
```

**影响：** 用户选择"继续"后，轮次限制没有重置，再次循环会立刻再次触发 limit；递归调用虽然通常只一次，但不可控。  
**建议：** 改为 `self.round_limit += N` 或重置计数器 `self.state.round_num = 0`，并改为循环而非递归。

---

### 3. Teammate idle 循环无超时保护
**位置：** `agent.py` 第 619-665 行

```python
while True:
    msg = await handle.inbox.get()  # 永久阻塞
```

`_poll_agent_progress()` 每 5 秒注入一条 `status_request`，但一旦 poller 停止，Teammate 将永久 hang 在这里。  
**建议：** 改用 `asyncio.wait_for(handle.inbox.get(), timeout=30.0)` 并在超时时主动检查 handle 状态。

---

### 4. Team shutdown 消息处理过于粗暴
**位置：** `agent.py` 第 906-908 行

```python
if any(m.get("_ccserver_team_shutdown") for m in team_messages):
    return round_text + "\n[shutdown by lead]"
```

**影响：** 直接丢弃本轮所有其他 inbox 消息，没有 graceful shutdown 阶段。  
**建议：** 设置 shutdown 标志，等当前 LLM 调用完成后再退出。

---

## 二、异常处理不完整（P1）

### 5. Tool 执行结果无 try-except
**位置：** `agent.py` 第 1283-1423 行，`_handle_tools()`

```python
result = await tool(**input_)  # 无异常捕获
```

**影响：** 任意一个 tool 抛异常都会中断整个 agent loop，无法返回错误信息给 LLM 继续处理。  
**建议：** 包裹 `try-except Exception`，失败时返回 `ToolResult.error(str(e))`。

---

### 6. Hook 执行失败无保护
**位置：** `agent.py` 第 891-896 行，`agent:bootstrap` hook 调用处

```python
await self.session.hooks.emit("agent:bootstrap", ...)
# 无 try-except
```

**影响：** hook 执行失败直接抛出，中断 agent 启动。  
**建议：** 非关键 hook 统一 try-except，失败时只 log warning 不中断。

---

### 7. LLM 重试耗尽后无 `error` 事件的 phase 保证
**位置：** `agent.py` 第 1118-1200 行，`_call_llm_stream()`

重试期间 `state.phase` 保持 `"llm_calling"`，客户端看不到 "retrying" 状态；返回 `None` 后调用方只判断 `if response is None` 设置 phase，但有些分支可能漏掉。  
**建议：** 在重试时 emit 一个 `retrying` 事件，统一在 `_call_llm_stream()` 内处理 phase。

---

## 三、状态管理问题（P2）

### 8. AgentState 缺少关键计量字段
**位置：** `agent.py` 第 91-111 行

缺少 `end_time`、`total_tokens`、`llm_call_count`、`tool_call_count`，只有 `last_error` 丢失历史错误。  
**建议：** 补充以上字段，方便监控和调试。

---

### 9. phase 状态转换路径混乱
**位置：** `agent.py` 第 920-977 行

loop 中存在 3 条不同退出路径，分别赋值 `"done"`、`"error"`、`"limit_reached"`，`_on_limit()` 内部还会再次修改 phase，没有统一的状态转换表。  
**建议：** 抽取 `_set_phase()` 方法并记录转换日志；或用 enum 规范所有状态。

---

## 四、并发/异步问题（P2）

### 10. `_poll_agent_progress()` inbox 满时无限重试
**位置：** `agent.py` 第 139-215 行

```python
except asyncio.QueueFull:
    continue  # 无限重试，无退避
```

**影响：** inbox 持续满时该协程 CPU 空转。  
**建议：** 加 `await asyncio.sleep(1.0)` 退避，或增加最大重试次数。

---

### 11. spawn_background() 三个并发任务无统一 cancel 协调
**位置：** `agent.py` 第 581-588 行

`_run_background()` 结束后，`forward_agent_events()` 和 `_poll_agent_progress()` 可能仍在运行（等待 sleep 或 queue）。  
**建议：** 使用 `asyncio.TaskGroup` 或 `cancel()` 链式传播，确保三个任务同时退出。

---

### 12. hook fire-and-forget 竞态风险
**位置：** `agent.py` 第 480-494 行

部分 hook 使用 `create_task()` 不等待，多个 hook 并发修改同一资源时存在竞态。  
**建议：** 区分"观测型 hook"（可 fire-and-forget）和"干预型 hook"（必须 await）。

---

## 五、Pipeline/Graph 问题（P3）

### 13. 有环图缺乏运行时循环检测
**位置：** `pipeline/graph.py`，`_validate()` 和 `run()`

`max_steps` 是唯一防止无限循环的机制，但它是硬上限，不会提前预警。  
**建议：** 在 `run()` 中记录已访问节点计数，异常增长时 emit warning。

---

## 六、数据一致性问题（P3）

### 14. `_append()` 内存与磁盘持久化无事务保证
**位置：** `agent.py` 第 1659-1678 行

```python
self.context.messages.append(message)
if self.persist:
    self.session.persist_message(message)  # 失败时内存已有，磁盘没有
```

**建议：** 持久化失败时 log error 并标记 session 为 `dirty`，后续尝试补写。

---

## 优先修复顺序

| 优先级 | 问题编号 | 问题简述 |
|--------|---------|---------|
| P1 | #1 | `_drain_inbox_and_respond` 双重循环 |
| P1 | #3 | Teammate idle 循环死锁 |
| P1 | #5 | Tool 执行无异常捕获 |
| P1 | #2 | `round_limit` 重置无效 |
| P2 | #11 | 后台三任务无协调取消 |
| P2 | #9 | phase 状态混乱 |
| P3 | #13 | Graph 无循环预警 |
| P3 | #14 | 消息持久化不一致 |
