# ccserver Agent 生态扩展详细实施方案

> 版本：v1.0  
> 日期：2026/04/11  
> 目标：为 ccserver 的 Agent 扩展（Task Manager、后台 Agent、Agent 间通信）提供详细、可落地的实现方案

---

## 一、目标与范围

### 1.1 预期目标

| 目标 | 描述 |
|------|------|
| **目标 1** | Agent 运行时状态可外部查询（状态机外显） |
| **目标 2** | Agent 支持流式/非流式两种运行模式 |
| **目标 3** | 后台 Agent 非阻塞运行，独立 Emitter |
| **目标 4** | Task 持久化并与 Agent 绑定 |
| **目标 5** | 同 Session 内 Agent 间可通信 |

### 1.2 范围界定

- **包含**：Agent Core 改造、后台 Agent 框架、Task Manager 增强、Agent 通信
- **不包含**：
  - 跨 Session 的分布式通信（需要 Redis，超出本期范围）
  - MCP Server 扩展
  - UI/TUI 改造

---

## 二、总体架构

### 2.1 架构分层

```
┌─────────────────────────────────────────────────────────────┐
│                    Layer 4: Agent 间通信                    │
│   ┌─────────────┐  ┌─────────────┐                          │
│   │ SessionBus │  │  AgentMsg  │  (内存 Queue)              │
│   └─────────────┘  └─────────────┘                          │
├─────────────────────────────────────────────────────────────┤
│                  Layer 3: 后台 Agent 框架                    │
│   ┌─────────────┐  ┌─────────────┐  ┌─────────────┐         │
│   │Background  │  │ Queue      │  │ Scheduler   │         │
│   │AgentHandle │  │ Emitter    │  │ (session级) │         │
│   └─────────────┘  └─────────────┘  └─────────────┘         │
├─────────────────────────────────────────────────────────────┤
│                    Layer 2: Task Manager 增强               │
│   ┌─────────────┐  ┌─────────────┐  ┌─────────────┐         │
│   │ Task 持久化 │  │ Agent 绑定  │  │ 依赖管理   │         │
│   └─────────────┘  └─────────────┘  └─────────────┘         │
├─────────────────────────────────────────────────────────────┤
│                    Layer 1: Agent Core 基础                 │
│   ┌─────────────┐  ┌─────────────┐  ┌─────────────┐         │
│   │ AgentState │  │ stream 开关 │  │ Hook 补齐   │         │
│   └─────────────┘  └─────────────┘  └─────────────┘         │
└─────────────────────────────────────────────────────────────┘
```

### 2.2 依赖关系

```
Layer 1 (Agent Core)
    ↓ 必须先完成
Layer 2 (Task Manager) ← 可与 Layer 3 并行
    ↓ 依赖 Layer 1
Layer 3 (后台 Agent) ← 依赖 Layer 1 完成
    ↓ 依赖 Layer 3
Layer 4 (Agent 通信)
```

---

## 三、Phase 1：Agent Core 基础（必须先做）

### 3.1 目标

1. Agent 运行时状态可外部查询
2. 支持流式 (`stream=True`) 和非流式 (`stream=False`) 两种模式
3. 补齐关键 Hook 点

### 3.2 改动清单

| 文件 | 改动类型 | 描述 |
|------|----------|------|
| `ccserver/agent.py` | 修改 | 添加 `AgentState` 类、`stream` 参数、拆分 LLM 调用方法 |
| `ccserver/factory.py` | 修改 | `create_root()` 支持 `stream` 参数 |
| `ccserver/pipeline/graph.py` | 修改 | AgentNode 使用 `stream=False` |
| `ccserver/emitters/__init__.py` | 新增 | 添加 `QueueEmitter`（供后台 Agent 使用） |

### 3.3 具体实现

#### 3.3.1 新增 AgentState 数据类

**文件**: `ccserver/agent.py`

```python
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

@dataclass
class AgentState:
    """
    代理运行时状态，用于外部系统查询 agent 当前的运行阶段。
    
    phase 取值:
        - idle         : 刚创建，未开始运行
        - llm_calling : 正在调用 LLM
        - tool_executing: 正在执行工具
        - waiting_user: 正在等待用户确认（AskUserQuestion）
        - done        : 正常结束
        - error       : 异常结束
        - limit_reached: 达到轮次上限
        - cancelled   : 被外部取消
    """
    phase: str = "idle"
    round_num: int = 0
    current_tool: Optional[str] = None
    start_time: Optional[datetime] = None
    last_error: Optional[str] = None
```

#### 3.3.2 Agent.__init__ 修改

**参数变更**：
```python
def __init__(
    self,
    # ... 现有参数 ...
    stream: bool = True,  # 新增：True=实时 emit token，False=非流式
):
    # ... 现有逻辑 ...
    self.stream = stream
    self.state = AgentState()  # 新增：运行时状态
```

#### 3.3.3 拆分 LLM 调用方法

将原来的 `_call_llm_with_retry()` 拆分为两个方法：

**方法 1: `_call_llm_stream()` - 流式调用**
```python
async def _call_llm_stream(self):
    """流式调用 LLM，实时 emit token。用于 stream=True。"""
    # hook: prompt:llm_input
    await self.session.hooks.emit_void(...)
    
    async with self.adapter.stream(...) as stream:
        async for text in stream.text_stream:
            await self.emitter.emit_token(text)  # 实时 emit！
        response = await stream.get_final_message()
    
    return response
```

**方法 2: `_call_llm_sync()` - 非流式调用**
```python
async def _call_llm_sync(self):
    """非流式调用 LLM，不 emit token。用于 stream=False。"""
    # hook: prompt:llm_input
    await self.session.hooks.emit_void(...)
    
    response = await self.adapter.create(...)  # 非流式 API
    return response
```

#### 3.3.4 _loop 方法改造

**核心逻辑**：
```python
async def _loop(self) -> str:
    # 初始化状态
    self.state.start_time = datetime.now(timezone.utc)
    self.state.phase = "running"
    
    for round_num in range(self.round_limit):
        self.state.round_num = round_num + 1
        self.state.phase = "llm_calling"
        
        # 根据 stream 模式选择调用方式
        if self.stream:
            response = await self._call_llm_stream()
        else:
            response = await self._call_llm_sync()
        
        # ... 处理 response ...
        
        if response.stop_reason != "tool_use":
            self.state.phase = "done"
            # emit_done 并返回
            return round_text
        
        # 工具调用轮
        self.state.phase = "tool_executing"
        tool_results = await self._handle_tools(response.content)
    
    # 轮次耗尽
    self.state.phase = "limit_reached"
    return await self._on_limit(final_text)
```

**删除的内容**：
- `last_tokens: list[str]` 变量
- `last_text: str` 变量
- 最终轮的 `for token in last_tokens: emit_token(token)` 循环

#### 3.3.5 _on_limit 系列方法简化

移除 `last_tokens` 参数：
```python
async def _on_limit(self, last_text: str) -> str:  # 不再接收 last_tokens
    ...

async def _finish_with_last_text(self, last_text: str) -> str:
    if last_text:
        # 非流式模式下不单独 emit token，直接 emit done
        await self.emitter.emit_done(last_text)
        return last_text
```

#### 3.3.6 Factory 改动

**文件**: `ccserver/factory.py`

```python
def create_root(
    session: Session,
    emitter: BaseEmitter,
    *,
    # ... 现有参数 ...
    stream: bool = True,  # 新增：根 agent 默认为 True
) -> Agent:
    # ... 现有逻辑 ...
    agent = Agent(
        # ... 现有参数 ...
        stream=stream,
    )
    return agent
```

#### 3.3.7 Graph 节点改动

**文件**: `ccserver/pipeline/graph.py`

```python
agent = AgentFactory.create_root(
    # ... 现有参数 ...
    stream=False,  # Graph 节点不需要实时流式输出
)
```

#### 3.3.8 QueueEmitter（后台 Agent 用）

**文件**: `ccserver/emitters/queue.py`（新建）

```python
import asyncio
from .base import BaseEmitter

class QueueEmitter(BaseEmitter):
    """
    基于 asyncio.Queue 的 Emitter，用于后台 Agent。
    外部通过 `queue` 属性消费事件。
    """
    def __init__(self):
        self.queue: asyncio.Queue = asyncio.Queue()
    
    async def emit(self, event: dict) -> None:
        await self.queue.put(event)
    
    async def emit_done(self, content: str):
        await self.emit(self.fmt_done(content))
    
    # ... 其他 emit_* 方法类似 ...
```

### 3.4 Hook 补齐（可选，但推荐）

| Hook | 位置 | 作用 |
|------|------|------|
| `prompt:build:before` | `_call_llm_*` 调用前 | 可修改 system/messages |
| `agent:bootstrap` | `_loop` 开始处 | 可动态裁剪 tools/schemas |
| `message:outbound:sending` | 新增 Emitter 层（Phase 3） | 发送前拦截 |

---

## 四、Phase 2：后台 Agent 框架

### 4.1 目标

1. Agent 可在后台非阻塞运行
2. 外部可查询、取消后台 Agent
3. 后台 Agent 输出通过 Queue 收集

### 4.2 改动清单

| 文件 | 改动类型 | 描述 |
|------|----------|------|
| `ccserver/agent.py` | 修改 | `spawn_background()` 方法 |
| `ccserver/agent_scheduler.py` | 新增 | 后台 Agent 调度器 |
| `ccserver/emitters/queue.py` | 修改 | 完善 QueueEmitter |

### 4.3 具体实现

#### 4.3.1 BackgroundAgentHandle

**文件**: `ccserver/agent_handle.py`（新建）

```python
from dataclasses import dataclass, field
from typing import Optional
import asyncio

@dataclass
class BackgroundAgentHandle:
    """
    后台 Agent 的句柄，用于外部控制。
    """
    agent_id: str
    task_id: Optional[str]  # 绑定的 Task ID
    state: "AgentState"     # 引用 Agent.state
    inbox: asyncio.Queue    # 外部发消息给此 Agent
    outbox: asyncio.Queue   # 此 Agent 的产出事件
    _task: Optional[asyncio.Task] = None  # 内部的协程任务
    
    async def cancel(self):
        """取消后台 Agent。"""
        if self._task and not self._task.done():
            self._task.cancel()
            self.state.phase = "cancelled"
    
    async def send_message(self, payload: dict):
        """发送消息给后台 Agent。"""
        await self.inbox.put(payload)
    
    def get_output(self) -> str:
        """获取最终输出（阻塞等待）。"""
        # 从 outbox 等待 done 事件
        while True:
            event = asyncio.run(self.outbox.get())
            if event.get("type") == "done":
                return event.get("content", "")
```

#### 4.3.2 Agent.spawn_background()

**文件**: `ccserver/agent.py`

```python
def spawn_background(
    self,
    prompt: str,
    agent_def=None,
    agent_name=None,
    task_id: str = None,
) -> BackgroundAgentHandle:
    """
    启动后台 Agent（非阻塞）。
    
    返回 BackgroundAgentHandle，外部可通过 handle 查询状态、发送消息、获取结果。
    """
    # 1. 创建子 Agent（stream=False）
    child = self.spawn_child(
        prompt=prompt,
        agent_def=agent_def,
        agent_name=agent_name,
        stream=False,  # 后台 Agent 不需要实时流式
    )
    
    # 2. 替换 Emitter 为 QueueEmitter
    queue_emitter = QueueEmitter()
    child.emitter = queue_emitter
    
    # 3. 创建 Handle
    handle = BackgroundAgentHandle(
        agent_id=child.context.agent_id,
        task_id=task_id,
        state=child.state,
        inbox=asyncio.Queue(),
        outbox=queue_emitter.queue,
    )
    
    # 4. 启动协程（不阻塞）
    async def _run_background():
        try:
            await child.run(prompt)
            await handle.outbox.put({"type": "done", "content": child.context.messages[-1]})
        except asyncio.CancelledError:
            await handle.outbox.put({"type": "cancelled"})
        except Exception as e:
            await handle.outbox.put({"type": "error", "error": str(e)})
    
    handle._task = asyncio.create_task(_run_background())
    return handle
```

#### 4.3.3 AgentScheduler

**文件**: `ccserver/agent_scheduler.py`（新建）

```python
class AgentScheduler:
    """
    Session 级别的后台 Agent 调度器。
    管理所有后台 Agent 的生命周期。
    """
    
    def __init__(self, session: "Session"):
        self.session = session
        self._handles: dict[str, BackgroundAgentHandle] = {}
    
    def spawn(
        self,
        prompt: str,
        agent_def=None,
        agent_name: str = None,
        task_id: str = None,
    ) -> BackgroundAgentHandle:
        """启动后台 Agent。"""
        # 需要从根 Agent 获取 spawn 能力
        root_agent = self._get_root_agent()
        handle = root_agent.spawn_background(
            prompt=prompt,
            agent_def=agent_def,
            agent_name=agent_name,
            task_id=task_id,
        )
        self._handles[handle.agent_id] = handle
        return handle
    
    def get(self, agent_id: str) -> Optional[BackgroundAgentHandle]:
        """查询后台 Agent 状态。"""
        return self._handles.get(agent_id)
    
    def list(self) -> list[BackgroundAgentHandle]:
        """列出所有后台 Agent。"""
        return list(self._handles.values())
    
    def cancel(self, agent_id: str) -> bool:
        """取消后台 Agent。"""
        handle = self._handles.get(agent_id)
        if handle:
            asyncio.create_task(handle.cancel())
            return True
        return False
    
    def _get_root_agent(self) -> "Agent":
        """获取根 Agent（延迟获取）。"""
        # 实际实现需要从 session 或其他地方获取根 Agent 引用
        pass
```

---

## 五、Phase 3：Task Manager 增强与 Agent 通信

### 5.1 目标

1. Task 持久化
2. Task 与 Agent 绑定
3. 同 Session 内 Agent 可通信

### 5.2 改动清单

| 文件 | 改动类型 | 描述 |
|------|----------|------|
| `ccserver/managers/tasks/manager.py` | 修改 | Task 持久化、扩展字段 |
| `ccserver/managers/tasks/storage.py` | 新增 | Task 存储适配器 |
| `ccserver/session.py` | 修改 | 集成 AgentScheduler |
| `ccserver/agent_bus.py` | 新增 | Session 级消息总线 |

### 5.3 具体实现

#### 5.3.1 Task 扩展

**文件**: `ccserver/managers/tasks/manager.py`

```python
@dataclass
class Task:
    """任务记录。"""
    id: str
    type: str = "local_agent"  # local_agent / background_agent / local_bash / function / mcp_tool
    subject: str = ""
    description: str = ""
    status: str = "pending"  # pending / in_progress / completed / failed / deleted
    
    assigned_agent_id: Optional[str] = None  # 绑定的 Agent ID
    agent_type: Optional[str] = None         # agent 类型
    blocked_by: list[str] = field(default_factory=list)  # 依赖的 task IDs
    blocks: list[str] = field(default_factory=list)      # 阻塞的 task IDs
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    output_summary: Optional[str] = None  # Agent 执行后的总结
    output_data: Optional[dict] = None    # 结构化输出


class TaskManager:
    """支持持久化的任务管理器，直接复用现有 StorageAdapter。"""
    
    def __init__(self, session_id: str, storage: "StorageAdapter"):
        self._session_id = session_id
        self._storage = storage
    
    def _next_id(self) -> str:
        return str(self._storage.get_task_counter(self._session_id) + 1)
    
    def create(self, subject: str, description: str = "", task_type: str = "local_agent") -> Task:
        task_id = self._next_id()
        task = Task(id=task_id, type=task_type, subject=subject, description=description)
        self._storage.create_task(self._session_id, task)
        self._storage.set_task_counter(self._session_id, int(task_id))
        return task
    
    def bind_agent(self, task_id: str, agent_id: str, agent_type: Optional[str] = None):
        """将 Task 绑定到 Agent。"""
        task = self.get(task_id)
        task.assigned_agent_id = agent_id
        task.agent_type = agent_type
        task.status = "in_progress"
        task.started_at = datetime.now(timezone.utc)
        self._storage.update_task(self._session_id, task)
    
    def complete(self, task_id: str, output_summary: str):
        """标记 Task 完成。"""
        task = self.get(task_id)
        task.status = "completed"
        task.completed_at = datetime.now(timezone.utc)
        task.output_summary = output_summary
        task.assigned_agent_id = None
        self._storage.update_task(self._session_id, task)
```

#### 5.3.2 StorageAdapter 扩展

**文件**: `ccserver/storage/base.py` / `ccserver/storage/file_adapter.py` / `ccserver/storage/mongo_adapter.py`

直接在现有的 `StorageAdapter` 基类上扩展 Task 方法，无需新建独立的 TaskStorage：

```python
class StorageAdapter(ABC):
    # ... 现有 session 方法 ...

    # ── Task 存储 ──────────────────────────────────────────────
    
    def create_task(self, session_id: str, task: Task) -> None:
        raise NotImplementedError(f"{self.__class__.__name__} 未实现 create_task")

    def load_task(self, session_id: str, task_id: str) -> Optional[Task]:
        raise NotImplementedError(f"{self.__class__.__name__} 未实现 load_task")

    def update_task(self, session_id: str, task: Task) -> None:
        raise NotImplementedError(f"{self.__class__.__name__} 未实现 update_task")

    def list_tasks(self, session_id: str) -> list[Task]:
        raise NotImplementedError(f"{self.__class__.__name__} 未实现 list_tasks")

    def get_task_counter(self, session_id: str) -> int:
        raise NotImplementedError(f"{self.__class__.__name__} 未实现 get_task_counter")

    def set_task_counter(self, session_id: str, value: int) -> None:
        raise NotImplementedError(f"{self.__class__.__name__} 未实现 set_task_counter")
```

然后在 FileStorageAdapter / MongoStorageAdapter 中提供具体实现即可。

#### 5.3.3 Agent 间通信 - SessionAgentBus

**文件**: `ccserver/agent_bus.py`

```python
import asyncio
from dataclasses import dataclass
from typing import Dict, Optional
from enum import Enum

class MessageType(Enum):
    REQUEST = "request"
    RESPONSE = "response"
    EVENT = "event"

@dataclass
class AgentMessage:
    """Agent 间消息。"""
    id: str
    from_agent_id: str
    to_agent_id: str  # "*" 表示广播
    type: MessageType
    payload: dict
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

class SessionAgentBus:
    """
    Session 级别的 Agent 消息总线。
    支持同 Session 内的 Agent 之间的消息传递。
    """
    
    def __init__(self):
        self._agents: Dict[str, asyncio.Queue] = {}
        self._bus: asyncio.Queue = asyncio.Queue()
    
    def register(self, agent_id: str, inbox: asyncio.Queue):
        """注册一个 Agent 到消息总线。"""
        self._agents[agent_id] = inbox
    
    def unregister(self, agent_id: str):
        """注销一个 Agent。"""
        self._agents.pop(agent_id, None)
    
    async def send(self, message: AgentMessage):
        """发送消息。"""
        if message.to_agent_id == "*":
            # 广播
            for agent_id, inbox in self._agents.items():
                if agent_id != message.from_agent_id:
                    await inbox.put(message)
        else:
            # 单播
            inbox = self._agents.get(message.to_agent_id)
            if inbox:
                await inbox.put(message)
    
    async def publish(self, from_agent_id: str, event_type: str, payload: dict):
        """发布事件。"""
        message = AgentMessage(
            id=str(uuid.uuid4()),
            from_agent_id=from_agent_id,
            to_agent_id="*",
            type=MessageType.EVENT,
            payload={"event_type": event_type, **payload},
        )
        await self.send(message)
```

#### 5.3.4 Session 集成

**文件**: `ccserver/session.py`

```python
class Session:
    # ... 现有字段 ...
    _scheduler: Optional["AgentScheduler"] = None
    _agent_bus: Optional["SessionAgentBus"] = None
    
    @property
    def scheduler(self) -> "AgentScheduler":
        if self._scheduler is None:
            self._scheduler = AgentScheduler(self)
        return self._scheduler
    
    @property
    def agent_bus(self) -> "SessionAgentBus":
        if self._agent_bus is None:
            self._agent_bus = SessionAgentBus()
        return self._agent_bus
```

---

## 六、实施步骤

### 6.1 Phase 1 实施步骤（建议 1-2 周）

| 步骤 | 任务 | 文件 |
|------|------|------|
| 1.1 | 添加 `AgentState` 类 | `agent.py` |
| 1.2 | 添加 `stream` 参数到 `Agent.__init__` | `agent.py` |
| 1.3 | 拆分 `_call_llm_with_retry` 为 `_call_llm_stream` 和 `_call_llm_sync` | `agent.py` |
| 1.4 | 改造 `_loop` 移除 `last_tokens`，根据 `stream` 选择调用方法 | `agent.py` |
| 1.5 | 简化 `_on_limit` 系列方法，移除 `last_tokens` 参数 | `agent.py` |
| 1.6 | 添加 `stream` 参数到 `AgentFactory.create_root` | `factory.py` |
| 1.7 | 修改 Graph AgentNode 使用 `stream=False` | `pipeline/graph.py` |
| 1.8 | 新建 `QueueEmitter` | `emitters/queue.py` |
| 1.9 | 测试：根 Agent 流式输出、Graph 节点非流式输出 | - |

### 6.2 Phase 2 实施步骤（建议 1 周）

| 步骤 | 任务 | 文件 |
|------|------|------|
| 2.1 | 新建 `BackgroundAgentHandle` | `agent_handle.py` |
| 2.2 | 添加 `Agent.spawn_background()` 方法 | `agent.py` |
| 2.3 | 新建 `AgentScheduler` | `agent_scheduler.py` |
| 2.4 | 完善 `QueueEmitter` | `emitters/queue.py` |
| 2.5 | 测试：后台 Agent 启动、查询、取消 | - |

### 6.3 Phase 3 实施步骤（建议 1 周）

| 步骤 | 任务 | 文件 |
|------|------|------|
| 3.1 | 扩展 `Task` 类字段 | `managers/tasks/manager.py` |
| 3.2 | `StorageAdapter` 基类扩展 Task 方法 | `storage/base.py` |
| 3.3 | `FileStorageAdapter` / `MongoStorageAdapter` 实现 Task 方法 | `storage/file_adapter.py` / `storage/mongo_adapter.py` |
| 3.4 | 集成 `AgentScheduler` 到 `Session` | `session.py` |
| 3.5 | 新建 `SessionAgentBus` | `agent_bus.py` |
| 3.6 | 测试：Task 持久化、Agent 绑定、通信 | - |

---

## 七、测试计划

### 7.1 单元测试

| 测试对象 | 测试内容 |
|----------|----------|
| `AgentState` | 状态转换正确 |
| `_call_llm_stream` | 实时 emit token |
| `_call_llm_sync` | 不 emit token，直接返回 |
| `spawn_background` | 后台启动返回 handle |
| `AgentScheduler` | spawn/get/cancel 正确 |
| `SessionAgentBus` | 单播/广播正确 |

### 7.2 集成测试

| 测试场景 | 预期结果 |
|----------|----------|
| 根 Agent 运行 | 用户看到实时 token 流 |
| Graph 节点运行 | 只返回最终 done 事件 |
| 后台 Agent 启动 | 立即返回 handle，异步执行 |
| 后台 Agent 取消 | 状态变为 cancelled |
| Task 绑定 Agent | Task.assigned_agent_id 正确设置 |
| Agent 间通信 | 消息正确投递 |

---

## 八、风险与注意事项

### 8.1 风险

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| stream 模式切换导致现有功能 regression | 高 | 充分测试，特别是 `stream=True` 的根 Agent |
| 后台 Agent 取消逻辑复杂 | 中 | 使用 asyncio.CancelledError 标准方式 |
| Task 持久化与现有 Session 存储冲突 | 低 | 直接复用现有 StorageAdapter，在 `{session_id}/tasks/` 下隔离存储 |

### 8.2 注意事项

1. **向后兼容**：`stream=True` 保持原有行为，`stream=False` 是新增模式
2. **资源管理**：后台 Agent 需要有超时机制，避免无限运行
3. **错误处理**：Agent 间通信需要处理目标 Agent 不存在的情况

---

## 九、文件清单

### 9.1 新增文件

| 文件 | 描述 |
|------|------|
| `ccserver/emitters/queue.py` | QueueEmitter |
| `ccserver/agent_handle.py` | BackgroundAgentHandle |
| `ccserver/agent_scheduler.py` | AgentScheduler |
| `ccserver/storage/file_adapter.py` | FileStorageAdapter 扩展 Task 方法 |
| `ccserver/agent_bus.py` | SessionAgentBus |

### 9.2 修改文件

| 文件 | 改动 |
|------|------|
| `ccserver/agent.py` | AgentState、stream 参数、LLM 调用拆分 |
| `ccserver/factory.py` | stream 参数 |
| `ccserver/pipeline/graph.py` | stream=False |
| `ccserver/session.py` | 集成 Scheduler 和 AgentBus |
| `ccserver/managers/tasks/manager.py` | Task 扩展 |

---

## 十、总结

本方案提供了 ccserver Agent 生态扩展的完整实施路径：

1. **Phase 1** 是地基，必须先完成
2. **Phase 2** 和 **Phase 3** 可根据实际需求调整优先级
3. 核心原则：**状态外显 → 非阻塞运行 → 任务绑定 → 消息通信**

如需进一步细化某个阶段的代码实现，请告知。