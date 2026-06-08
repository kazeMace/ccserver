# ccserver Agent 生态扩展路线图

> Date: 2026/04/11  
> 目标：梳理 "Agent 功能扩展 → Task Manager → 后台 Agent → Agent 间通信" 的依赖关系与落地优先级，避免架构债务堆叠。

---

## 当前代码基座快照

| 模块 | 当前状态 | 关键文件 |
|------|----------|----------|
| Agent Core | 阻塞式单线程 `_loop()`，根/子代理共用同一套逻辑 | `agent.py` |
| Subagent | 同步 `spawn_child()` + `await child._loop()`，仅通过 `summary: str` 返结果 | `agent.py:759-803` |
| Task Manager | 内存级任务列表（自增 ID，无持久化），与 Agent 无直接绑定 | `managers/tasks/manager.py` |
| Graph Pipeline | 有向有环图执行引擎，每个 AgentNode 仍是阻塞调用 `agent.run()` | `pipeline/graph.py` |
| Emitter | SSE/Collect/Filter 分层清晰，但无异步消息总线 | `emitters/*.py` |
| Hook | 已有 20+ 事件定义，部分未触发；无 agent 间路由能力 | `managers/hooks/manager.py` |

---

## 一、四个扩展目标的定义与边界

### 1.1 Agent 功能扩展

**定义**：让 Agent Core (`_loop`) 支持更灵活的运行模式，为上层功能打好基础。

具体包括：
1. **`stream: bool` 开关**（已在 `agent_loop_streaming_analysis.md` 中分析）
2. **非阻塞式 Agent 运行能力**：`_loop` 支持被"放入后台"而不阻塞调用方
3. **更细粒度的 Hook 补齐**：`prompt:build:before`、`agent:bootstrap`、`message:outbound:sending` 等
4. **Agent 状态机外显**：让外部系统（如 Task Manager）能查询 agent 当前处于 "thinking" / "tool_executing" / "waiting" 等状态

**为什么必须优先做它？**  
Task Manager、后台 Agent、Agent 间通信都依赖于 Agent 能**被调度**。当前 `agent.run()` 和 `child._loop()` 是纯粹的阻塞黑盒——外部无法进入、无法中断、无法查询中间状态。如果不在底层把 Agent 从"阻塞函数"升级为"可观测对象"，所有上层扩展都会变成在沙地上盖楼。

### 1.2 Task Manager 增强

**定义**：从"内存待办清单"升级为"Agent 工作单元的调度器"。

具体包括：
1. **持久化**：Task 需要写入 session storage（SQLite/Mongo/文件），会话重启后恢复
2. **任务与 Agent 绑定**：一个 Task 可以被分配给一个特定的子 Agent（或后台 Agent）执行
3. **任务状态与 Agent 状态联动**：Task 的 `in_progress` 必须映射到某个 Agent 的 `running` 状态
4. **任务依赖图**：支持 Task A 阻塞 Task B（这样 Agent 才能按依赖顺序执行）

**为什么不能先做它？**  
现在的 Task Manager 只是一个 LLM 的"记事本"——LLM 用 `TaskCreate` 工具记录一下，用 `TaskUpdate` 改一下状态。它**不驱动任何实际执行**。在 Agent 还不支持非阻塞运行和后台调度之前，Task Manager 增强到再有花样也只是一个高级 todo list，无法真正触发代码层面的调度。

### 1.3 后台 Agent (Background Agent)

**定义**：不阻塞父 Agent、不占用当前 emitter 输出通道，能在 session 生命周期内独立运行的 agent。

具体包括：
1. **后台启动**：父 Agent 或外部系统（如 API 请求）可以启动一个 background agent，拿到一个 `agent_id` 后立即返回
2. **独立上下文**：后台 agent 有自己的 `AgentContext.messages`，但共享 `session`（从而共享 tasks、hooks、MCP）
3. **独立 Emitter**：后台 agent 的 emitter 不能是 `self.emitter`（否则会污染当前用户的 SSE 流），通常是一个消息队列或一个独立的 `BackgroundEmitter`
4. **生命周期管理**：支持查询状态、中断（cancel）、获取结果

**依赖关系**：强烈依赖 "Agent 功能扩展" 中的非阻塞运行能力和状态外显能力。

### 1.4 Agent 间通信 (Inter-Agent Communication)

**定义**：让多个并存的 Agent（根 Agent + 多个子 Agent / 后台 Agent）之间能传递消息、共享上下文、协同决策。

具体包括：
1. **Message Bus**：一个基于 `asyncio.Queue` 或 Redis Pub/Sub 的轻量级消息总线
2. **地址模型**：每个 Agent 有 `agent_id`，消息可以按 `agent_id` 寻址
3. **消息协议**：定义标准 envelope，如 `{from: "agent-1", to: "agent-2", type: "request|response|event", payload: {...}}`
4. **Hook 集成**：通过 `subagent:delivery:target` 等 hook 实现路由拦截和改写

**依赖关系**：依赖于"后台 Agent"的存在。如果所有 agent 都是阻塞运行的，同一时刻只有一个 agent 在运行，通信就会变成"同步 return value"（也就是现在的 `summary` 字符串），不需要 bus。

---

## 二、依赖拓扑图

```
                    ┌─────────────────────────────────────────┐
                    │     Layer 4: Agent 间通信 (IAC)         │
                    │   ┌──────────────┐   ┌──────────────┐  │
                    │   │  Message Bus │   │ Agent Router │  │
                    │   └──────────────┘   └──────────────┘  │
                    └──────────────────┬──────────────────────┘
                                       │
                    ┌──────────────────▼──────────────────────┐
                    │   Layer 3: 后台 Agent (Background)      │
                    │   ┌──────────────┐   ┌──────────────┐  │
                    │   │ Async Loop   │   │独立 Emitter  │  │
                    │   │ Scheduler    │   │ (Queue/Bus)  │  │
                    │   └──────────────┘   └──────────────┘  │
                    └──────────────────┬──────────────────────┘
                                       │
                    ┌──────────────────▼──────────────────────┐
                    │   Layer 2: Task Manager 增强            │
                    │   ┌──────────────┐   ┌──────────────┐  │
                    │   │ Persistence  │   │ Agent Binding│  │
                    │   └──────────────┘   └──────────────┘  │
                    └──────────────────┬──────────────────────┘
                                       │
                    ┌──────────────────▼──────────────────────┐
                    │   Layer 1: Agent Core 扩展 (Foundation) │
                    │   ┌──────────────┐   ┌──────────────┐  │
                    │   │ stream 开关  │   │ 状态机外显   │  │
                    │   │ 非阻塞 loop  │   │ hook 补齐    │  │
                    │   └──────────────┘   └──────────────┘  │
                    └─────────────────────────────────────────┘
```

**核心原则：必须按 Layer 1 → Layer 2 → Layer 3 → Layer 4 的顺序推进。** 跳过任何一层都会引入无法收敛的架构债务。

---

## 三、Layer 1 必须完成的具体清单

这是你的**当务之急**，也是后续一切扩展的地基。

### 3.1 `stream: bool` 重构（已完成分析，待代码落地）

目标：让 `_loop` 支持：
- `stream=True`：实时 `emit_token`，给根 Agent 用（前端体验）
- `stream=False`：非流式 `create()` API，给 Graph/后台 agent 用（效率优先）

产出：
- `_loop` 中删除 `last_tokens`/`last_text` 的冗余状态
- `_call_llm_with_retry` 拆分为 `_call_llm_stream()` 和 `_call_llm_sync()`
- `AgentFactory.create_root(..., stream=True)` 支持传入参数

### 3.2 Agent 状态机外显（最小可行版）

当前 Agent 对象没有任何运行时可查询的中间状态。建议新增：

```python
@dataclass
class AgentState:
    phase: str          # "idle" | "llm_calling" | "tool_executing" | "waiting_user" | "done" | "error"
    round_num: int
    current_tool: str | None
    start_time: datetime
    last_error: str | None
```

在 `Agent.__init__` 中初始化 `self.state = AgentState(phase="idle", ...)`，并在 `_loop` 的关键节点更新它：

```python
async def _loop(self) -> str:
    self.state.phase = "running"
    for round_num in range(self.round_limit):
        self.state.round_num = round_num
        self.state.phase = "llm_calling"
        response = await self._call_llm_xxx()
        if response.stop_reason == "tool_use":
            self.state.phase = "tool_executing"
            await self._handle_tools(...)
        else:
            self.state.phase = "done"
            return ...
    self.state.phase = "limit_reached"
```

**为什么这很重要**：Task Manager 和 Background Agent 都需要向外部报告"这个 agent 现在在干嘛"。没有这个状态机，后台 agent 就是黑盒。

### 3.3 补齐关键 Hook 点（P0 级别）

根据 `agent_loop_deep_dive.md` 的分析，最优先补这三个：

| Hook | 作用 | 对应扩展 |
|------|------|----------|
| `prompt:build:before` | 运行时可修改 system/messages | 后台 agent 需要动态注入任务上下文 |
| `agent:bootstrap` | agent 启动前动态裁剪 tools/schemas | Task Manager 分配任务时动态约束 agent 能力 |
| `message:outbound:sending` | 在 emitter 发送前拦截/修改 | 后台 agent 需要把输出重定向到 message bus |

### 3.4 修复已知的代码缺陷

|`agent_loop_deep_dive.md` 中提到的 P0/P1 问题 | 优先级 |
|-----------------------------------------------|--------|
| `emit_tool_result` 在 `tool:call:after` 之前 | P0 |
| `last_tokens` 过时 bug | P0 |
| `_on_limit_ask_user` 递归调用可能导致栈溢出 | P1 |
| `/clear` 清空后又把自己 append 回去 | P1 |

---

## 四、Layer 2: Task Manager 增强的蓝图

当 Layer 1 完成后，Task Manager 的增强就有了实现基础。以下是大致的架构设计：

### 4.1 从内存列表到持久化工作项

```python
class Task:
    id: str
    subject: str
    description: str
    status: "pending" | "in_progress" | "completed" | "failed" | "deleted"
    
    # 新增字段
    assigned_agent_id: str | None     # 分配给哪个 agent
    parent_task_id: str | None        # 子任务依赖
    dependencies: list[str]           # 阻塞本任务的前置 task_ids
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    output_summary: str | None        # agent 执行后的总结
```

### 4.2 Task 与 Agent 绑定的生命周期

```
TaskCreate("分析代码", "...")
    ├─ TaskManager.create() -> Task(id="1", status="pending")
    ├─ 用户/调度器决定把 Task 1 交给后台 Agent "analyzer"
    │      ├─ AgentScheduler.spawn_background(agent_type="analyzer", task_id="1")
    │      ├─ TaskManager.update("1", status="in_progress", assigned_agent_id="analyzer-xxx")
    │      └─ 后台 Agent 开始运行
    │
    ├─ 后台 Agent 运行中... (Layer 3)
    │      ├─ Agent 通过 hook 或 tool_call 更新 Task 状态
    │      └─ 外部可以通过 TaskManager.get("1") 查询进度
    │
    └─ 后台 Agent 完成
           ├─ TaskManager.update("1", status="completed", output_summary=summary)
           └─ AgentScheduler 回收后台 Agent 资源
```

**关键洞察**：Task Manager 不是 scheduler，它只是"工作项记录"。真正的调度应该由 `AgentScheduler`（Layer 3）来做。

---

## 五、Layer 3: 后台 Agent 的最小可行设计

### 5.1 核心概念：`BackgroundAgentHandle`

为了避免直接操作 `Agent` 对象（它是 asyncio 协程内部的，不好外部中断），引入一个 handle：

```python
@dataclass
class BackgroundAgentHandle:
    agent_id: str
    task_id: str | None
    state: AgentState
    inbox: asyncio.Queue        # 外部给这个 agent 发消息
    outbox: asyncio.Queue       # 这个 agent 的产出事件
    _task: asyncio.Task | None  # 内部的 asyncio.Task
    
    async def cancel(self):
        if self._task:
            self._task.cancel()
    
    async def send_message(self, msg: dict):
        await self.inbox.put(msg)
```

### 5.2 后台 Agent 的 `_loop` 改造

后台 Agent 有两个新增需求：
1. **能检查 inbox**：在 LLM 调用间隙（尤其是等待用户确认时），检查是否有新消息进来
2. **不阻塞父 agent**：通过 `asyncio.create_task(_loop())` 启动

```python
async def _loop_background(self) -> str:
    """后台运行版本的 _loop，支持从 inbox 接收中断/新消息。"""
    # ... 与 _loop 基本相同，但在关键等待点增加 inbox 检查
    while True:  # 或 for round...
        # 示例：在调用 LLM 前检查 inbox（是否有 cancel 指令）
        if self._check_inbox_cancel():
            return "(cancelled by user)"
        
        response = await self._call_llm_xxx()
        # ...
```

### 5.3 Emitter 的重定向

后台 Agent 不能用主 SSEEmitter（否则用户会看到多个 agent 的 token 混在一起）。它应该使用一个 `QueueEmitter`：

```python
class QueueEmitter(BaseEmitter):
    def __init__(self):
        self.queue = asyncio.Queue()
    
    async def emit(self, event: dict):
        await self.queue.put(event)
```

后台 Agent 的所有 `emit_token`、`emit_done`、`emit_tool_result` 都会进入这个队列。外部调度器可以从 `handle.outbox` 中消费这些事件，再决定是写日志、发 WebSocket、还是汇总后推给用户。

---

## 六、Layer 4: Agent 间通信的演进路径

### 6.1 阶段 1：父-子 Agent 的同步通信（当前已实现）

形式：`summary = await child._loop()`  
特点：单向、阻塞、只有最终结果字符串  
局限：无中间状态共享、无双向对话

### 6.2 阶段 2：单一 Session 内的后台 Agent 通信

当 Layer 3 的 `BackgroundAgentHandle` 完成后，同一 Session 内的多个后台 Agent 可以通过一个轻量 bus 通信：

```python
class SessionAgentBus:
    """Session 级别的 agent 消息总线。"""
    def __init__(self):
        self._agents: dict[str, BackgroundAgentHandle] = {}
    
    def register(self, handle: BackgroundAgentHandle):
        self._agents[handle.agent_id] = handle
    
    async def send(self, from_id: str, to_id: str, payload: dict):
        target = self._agents.get(to_id)
        if target:
            await target.inbox.put({"from": from_id, "payload": payload})
```

这时 Agent 间通信不需要 Redis，纯内存的 `asyncio.Queue` 就足够了。

### 6.3 阶段 3：跨 Session / 跨进程通信

当系统支持多个用户 Session、分布式部署时，再引入 Redis / RabbitMQ：

```python
class RedisAgentBus:
    """跨进程的 agent 消息总线。"""
    # ... 基于 redis pub/sub 实现
```

**建议**：先做阶段 2，不要一上来就引入 Redis。教学和工程落地的最佳路径是"够用即可"。

---

## 七、推荐的落地优先级（明确的时间线）

### Phase 1: Agent Core 现代化（1-2 周）

**必须先完成，否则后面的一切都会反复返工。**

1. `stream: bool` 重构（删除 `last_tokens`，拆分 `_call_llm`）
2. 修复 P0 代码缺陷（`tool:call:after` 顺序、`last_tokens` 过时 bug）
3. 引入 `AgentState` 最小状态机
4. 补齐 `prompt:build:before`、`agent:bootstrap` 两个 hook

### Phase 2: Task Manager 增强（1 周）

1. Task 持久化（写入 Session storage）
2. Task 字段扩展（`assigned_agent_id`、`dependencies`、`status` 扩展）
3. Task 工具增强：让 LLM 可以查看自己创建的任务的执行状态（比如 `TaskList` 返回时带上 assigned_agent 的状态）

### Phase 3: 后台 Agent MVP（1-2 周）

1. `BackgroundAgentHandle` + `QueueEmitter`
2. `AgentScheduler`（session 级别）：负责 spawn、cancel、list background agents
3. 改造 `spawn_child`：支持 `background=True` 参数
4. 绑定 Task Manager：一个 Task 可以触发一个后台 Agent

### Phase 4: Agent 间通信（1 周）

1. `SessionAgentBus`（内存级）
2. 定义标准消息 envelope
3. 暴露 `SendMessageToAgent` 工具给 LLM
4. Hook 集成：`subagent:delivery:target` 触发的路由拦截

---

## 八、如果你今天只改一件事

**改 `agent.py` 中的 `_loop`，把 `last_tokens` / `last_text` 删掉，引入 `stream: bool`。**

这是地基中的地基。它解决的是：
- 一个已知的 bug（过时 token）
- 一个代码可读性问题（少两个无意义的状态变量）
- 一个扩展性问题（为后台 agent 的非流式高效运行打开大门）

---

## 九、结论

**不要现在就开始做 Task Manager 的持久化或后台 Agent 的调度器。**

你的直觉是对的——从 **Agent 功能扩展** 开始。但要把范围收敛到 **Layer 1 的三件事** 上：
1. `stream` 重构
2. `AgentState` 状态机
3. 补齐 `prompt:build:before` + `agent:bootstrap`

完成这三点后，Task Manager 自然可以从"记事本"升级为"工作项调度器"，后台 Agent 也能有清晰的运行模型。否则就像在没有地基的地方盖楼，每加一层功能，都要回头补一层的 hack。

