# EventBus 重构设计方案

**日期：** 2026-04-23  
**背景：** agent loop 中 `forward_agent_events` 和 `_poll_agent_progress` 共享同一个 outbox Queue，存在竞争消费问题。本文记录 B+C 方案的完整设计推理。

---

## 一、问题根源

当前架构中，`spawn_background()` 启动三个并发任务，共享一个 `outbox` Queue：

```
asyncio.create_task(forward_agent_events(handle, self.emitter))   # 任务A：等 done/error
asyncio.create_task(_poll_agent_progress(handle, self.emitter))   # 任务B：等 progress
handle._task = asyncio.create_task(_run_background())             # 任务C：主任务
```

Queue 消费即销毁，任务A和任务B竞争同一个队列：
- 任务B先拿到 `done` 事件 → 任务A永远等不到终结信号，永远不退出
- 任务A先拿到 `progress` 事件 → 进度丢失

根本原因：**Agent 的状态变化是可观察对象，不应用点对点的 Queue 暴露。**

---

## 二、方案选择：B+C

### 方案 B：Agent.run() 变为 AsyncIterator

Agent 是纯计算单元，输入 prompt，输出事件流，不关心谁在消费。

```python
async def run(prompt: str) -> AsyncIterator[AgentEvent]:
    yield AgentEvent(type="started", ...)
    yield AgentEvent(type="token", content="...")
    yield AgentEvent(type="tool_start", tool="bash")
    yield AgentEvent(type="done", content="最终结果")
```

### 方案 C：Session 级 EventBus

Session 持有唯一 EventBus，任何组件都是平等的发布者和订阅者。

```python
class Session:
    event_bus: EventBus  # session 内唯一实例
```

### B+C 分工

- **B 负责生产**：Agent 只管 yield/publish 事件，不关心谁在听
- **C 负责分发**：EventBus 接收所有 Agent 的事件，统一路由给 SSE、父 Agent、日志等

---

## 三、架构草图

```
Agent.run(prompt) → AsyncIterator[AgentEvent]
        │
        ▼
   EventBus.publish(event)          ← Session 级，唯一写入口
        │
        ├──► SSEEmitter / WSEmitter  ← 推给客户端
        ├──► ParentAgent 订阅者      ← 父 Agent 感知子 Agent 状态
        ├──► Recorder                ← 持久化/审计
        └──► TeamCoordinator         ← Team 协作感知
```

`forward_agent_events`、`_poll_agent_progress`、`QueueEmitter`、inbox/outbox 竞争问题全部消失。

---

## 四、核心设计

### 4.1 AgentEvent

```python
@dataclass
class AgentEvent:
    agent_id: str
    session_id: str
    type: str          # "token" | "tool_start" | "tool_done" | "progress" | "done" | "error"
    round_num: int
    payload: dict
    ts: float          # timestamp
```

所有事件带 `agent_id`，EventBus 按此做路由过滤。

### 4.2 EventBus（纯 asyncio，无外部依赖）

```python
class EventBus:
    _subscribers: dict[str, asyncio.Queue]  # subscriber_id → Queue

    def publish(self, event: AgentEvent):
        for q in self._subscribers.values():
            q.put_nowait(event)   # fan-out，每个订阅者独立副本

    def subscribe(self, filter_fn=None) -> AsyncIterator[AgentEvent]:
        # 返回独立游标的异步迭代器
        # filter_fn 可按 agent_id、event type 过滤
        # async with bus.subscribe() as stream 退出时自动取消注册
```

### 4.3 BusEmitter（过渡层，最低风险切入点）

保留现有 emitter 接口，底层换成写 EventBus，Agent 内部代码不用改：

```python
class BusEmitter(BaseEmitter):
    def __init__(self, bus: EventBus, agent_id: str):
        self.bus = bus
        self.agent_id = agent_id

    async def emit_token(self, token: str):
        await self.bus.publish(AgentEvent(type="token", payload={"token": token}, ...))

    async def emit_done(self, content: str):
        await self.bus.publish(AgentEvent(type="done", payload={"content": content}, ...))
```

### 4.4 progress 改为 push 模型

现在是 pull 模型（外部轮询问 → child 回答），改为 Agent 主动 push：

```python
# _loop() 每轮结束时主动 publish
await session.event_bus.publish(AgentEvent(
    type="progress",
    payload={"round_num": self.state.round_num, "phase": self.state.phase, ...}
))

# tool 执行前后各 publish 一次，覆盖长时间工具调用的进度空白
```

`_poll_agent_progress`、`MsgType.STATUS_REQUEST`、`_drain_inbox_and_respond` 中的 STATUS_REQUEST case 全部删除。

---

## 五、Team 协作功能兼容性推理

Team 协作有两条完全独立的信道：
- **入方向**（Lead→Teammate）：Mailbox（持久化）→ Poller → handle.inbox，与 EventBus 无关
- **出方向**（Teammate→父 Agent）：outbox/QueueEmitter，这是被替换的部分

逐个功能验证：

| 功能 | 现有路径 | B+C 后 | 兼容性 |
|---|---|---|---|
| Lead 发布任务，Teammate 认领 | Dispatcher→Mailbox→Poller→inbox→idle loop | **不变** | ✅ 完全兼容 |
| Teammate 完成任务通知 Lead | outbox→forward_events→_notify_parent_done | EventBus done 事件→订阅者→_notify_parent_done | ✅ 逻辑不变，触发方式变 |
| progress 推送 | status_request 轮询（竞争问题） | Agent 主动 push，EventBus fan-out | ✅ 更干净，消除竞争 |
| 权限审批（PermissionRelay） | Mailbox 信道（骨架未实现） | 不变；可用 EventBus 加速感知 | ✅ 完全兼容 |
| SendMessage 聊天 | mailbox.send() → 对方 Poller→inbox | **不变** | ✅ 完全不受影响 |
| TeamHealthMonitor | 检查 Dispatcher/Relay/Poller 存活 | 删除对 forward_events/_poll_progress 的检查 | ✅ 小幅修改 |

**结论：Team 所有核心功能均可保留，B+C 完全可行。**

---

## 六、变化汇总

| 现有组件 | B+C 后 | 变化说明 |
|---|---|---|
| `QueueEmitter` | `BusEmitter` | 替换，往 EventBus publish |
| `forward_agent_events` | 删除 | 改由 EventBus 订阅者处理 |
| `_poll_agent_progress` | 删除 | 改为 Agent 主动 push progress |
| `handle.outbox` | 删除 | 不再需要中间队列 |
| `MsgType.STATUS_REQUEST` | 删除 | push 模型不再需要 |
| `_drain_inbox_and_respond` STATUS_REQUEST case | 删除 | 同上 |
| `_notify_parent_done` | 改为订阅 EventBus | 逻辑不变，触发方式变 |
| Mailbox → Poller → handle.inbox | **不变** | 独立信道 |
| Dispatcher | **不变** | 独立组件 |
| TeamRegistry | **不变** | 无关 |
| SendMessage | **不变** | 走 Mailbox 信道 |
| idle loop | **不变** | 监听 inbox，不监听 outbox |

---

## 七、难点与解法

### 难点 1：背压（backpressure）
fan-out 时某个订阅者消费慢（SSE 客户端断网），Queue 无限增长。  
**解法：** 每个订阅者 Queue 设 maxsize，put_nowait 失败时 drop oldest 并 log warning。

### 难点 2：订阅者生命周期泄漏
注册后忘记取消订阅导致 Queue 堆积。  
**解法：** `async with bus.subscribe() as stream` context manager，退出时自动取消注册。

### 难点 3：现有 emitter 体系迁移量大
Agent 内部到处调用 `self.emitter.emit_token()` 等方法。  
**解法：** BusEmitter 实现 BaseEmitter 接口，Agent 内部代码零修改，只换底层实现。

### 难点 4：长时间工具调用期间进度空白
某一轮工具执行 30 秒，中间没有 round 结束，progress 不更新。  
**解法：** 在 tool 执行前后各 publish 一次 progress 事件。

---

## 八、迁移路径（三步，不推倒重来）

```
第一步：引入 EventBus + BusEmitter（1周内可完成，风险最低）
  - Session 加 event_bus 字段
  - 新建 BusEmitter，实现 BaseEmitter 接口，替换 QueueEmitter
  - forward_agent_events / _poll_agent_progress 改为订阅 EventBus
  - Agent.run() 签名不变，外部无感知
  - 已解决竞争问题

第二步：消除 inbox/outbox 残留
  - 父 Agent 感知子 Agent 改为订阅 EventBus
  - 删除 forward_agent_events / _poll_agent_progress / QueueEmitter
  - 删除 MsgType.STATUS_REQUEST 和相关处理代码
  - progress 改为 Agent 主动 push

第三步：Agent.run() 改为 AsyncIterator（可选，最激进）
  - 完全翻转控制权，调用方自己决定消费策略
  - 改动量最大，需重构所有调用方
```

**建议：从第一步开始，每步独立可验证，不需要一次性完成。**
