# CCServer Agent Team 架构缺口详细分析

> 分析日期：2026-04-12  
> 参考文档：Claude Code Agent Teams 源码分析  
> 分析师：Claude Code  
> 版本：v2（结合 CCServer 定位：前后端分离、服务器生产部署、Pipeline 定义、代码教学）

---

## 一、CCServer 当前架构基座梳理

在分析缺口前，先明确 CCServer **已经具备的核心能力**，这些将直接决定 Agent Team 功能的实现路径。

### 1.1 已成熟的基础模块

| 模块 | 当前能力 | 对 Agent Team 的价值 |
|------|----------|----------------------|
| `Session` + `SessionManager` | 完整的会话生命周期、workdir 隔离、`project_root` 自动加载 `.ccserver/` 配置 | Team 可建立在 Session 之上，或作为 Session 的扩展属性 |
| `Agent` + `AgentContext` | 统一的单 Agent 执行循环，支持根/子 Agent、`depth` 嵌套控制、`stream/sync` 模式 | Team 成员本质上仍是 Agent 实例，循环逻辑可复用 |
| `AgentLoader` + `AgentDef` | 从 `.ccserver/agents/*.md` 加载 Agent 定义，支持 tools/mcp/skills/round_limit 等字段 | 是 Agent Team "角色卡" 的基础设施 |
| `TaskManager` + `Task` | 支持任务创建、更新、依赖（blocked_by/blocks）、agent_id 绑定、持久化 | **任务发布池**已经有了，只差 "自动认领" 的调度触发 |
| `AgentScheduler` + `BackgroundAgentHandle` | Session 级后台 Agent 管理：spawn/get/list/cancel，含 inbox/outbox | 是 Team 调度的雏形，但执行完即销毁 |
| `SessionAgentBus` | 内存级 `asyncio.Queue` 通信总线 | 是 Team 通信的萌芽，但缺少持久化和标准协议 |
| `BaseEmitter` / `SSEEmitter` / `WSEmitter` | 前后端分离的事件推送体系，支持 `ask_user` 和 `permission_request` 双向交互 | **Team 可视化、权限桥接的天然通道** |
| `StorageAdapter` | 支持 `file` / `sqlite` / `mongo` 三种持久化后端 | **是实现持久化 Mailbox 的现成底座** |
| `Graph`（Pipeline） | 有向有环图 + 状态机，支持 `AgentNode` / `FunctionNode` / `MCPToolNode` | 适合做 "预定义团队工作流"，但缺动态调度能力 |
| `HookLoader` | 完善的消息/工具/Agent 生命周期 Hook | Team 行为可通过 Hook 扩展，无需硬编码 |

### 1.2 关键代码位置速查

- Agent 定义加载：`ccserver/managers/agents/manager.py`
- 任务管理：`ccserver/managers/tasks/manager.py`
- 后台 Agent 调度：`ccserver/agent_scheduler.py`
- Agent 间通信总线：`ccserver/agent_bus.py`
- 后台 Agent 句柄：`ccserver/agent_handle.py`
- SSE 双向交互：`ccserver/emitters/sse.py`
- Pipeline 图引擎：`ccserver/pipeline/graph.py`
- 存储适配器：`ccserver/storage/base.py`

---

## 二、Agent Team 功能缺口逐项详解

下面按照 "功能域 → 现状 → 目标 → 缺口等级 → 建议实现路径" 的格式展开。

---

### 2.1 Team 抽象层：从 "父子派生" 到 "团队成员"

#### 现状
CCServer 中只有 `Agent`（根 Agent）和 `spawn_child` / `spawn_background`（子 Agent）。子 Agent 通过 `AgentContext.depth` 区分嵌套层级，通过 `AgentContext.agent_id`（随机 UUID）标识。没有 "Team"、"Lead"、"Teammate" 的语义。

#### 目标（Claude Code 模型）
- `Team` 是一个独立的管理单元，有名称、描述、创建时间、Lead、成员列表。
- 每个成员有 **确定性 ID**：`{name}@{teamName}`（如 `researcher@auth-refactor`）。
- Team 配置持久化到磁盘（Claude Code 用 `~/.claude/teams/{team}/config.json`）。
- 成员共享 `teamAllowedPaths`（团队级可编辑路径规则）。

#### 缺口等级：高

#### 结合 CCServer 定位的建议设计

由于 CCServer 主打**服务器生产部署**，Team 抽象应该：
1. **与 Session 关联**：一个 `Session` 可以拥有一个 `Team`（`session.team: Team | None`）。这样前端通过 `GET /sessions/{id}` 就能拿到团队信息。
2. **持久化到 StorageAdapter**：在 `StorageAdapter` 接口中增加 `save_team(session_id, team_dict)` / `load_team(session_id)`，利用已有的 sqlite/mongo 后端实现多实例共享团队状态。
3. **确定性 Agent ID**：引入 `format_agent_id(name: str, team_name: str) -> str`，格式为 `{name}@{team_name}`。注意需要保证在一个 Team 内名称唯一（可以像 Claude Code 那样做 `generate_unique_teammate_name` 去重）。
4. **TeamRegistry**：在 `Session` 级别引入 `TeamRegistry`（或扩展 `AgentTaskRegistry`），用于按 team_name 查找 Team 实例。

**新增模块建议**：
- `ccserver/team/team.py`：`Team` 数据类
- `ccserver/team/registry.py`：`TeamRegistry`
- `ccserver/team/helpers.py`：`format_agent_id()`、名称去重
- `ccserver/storage/base.py`：增加 `save_team` / `load_team` / `list_teams`

---

### 2.2 团队协议定义：从 "内存 Queue" 到 "持久化 Mailbox + 标准消息协议"

#### 什么是 "团队协议"（Team Protocol）？

**团队协议**不是指某个单独的工具或文件，而是一套**规范 Agent 团队成员之间如何通信、协作、同步状态的约定**。你可以把它理解成 Agent Team 的 "TCP/IP"——没有它，每个 Agent 就是一座孤岛；有了它，它们才能构成一个有机的整体。

在 Claude Code 的设计中，团队协议包含以下 **5 个核心层面**：

**1. 寻址层（Addressing）**
- 每个团队成员有一个**确定性标识**，格式为 `{name}@{teamName}`，如 `researcher@auth-refactor`。
- 这个 ID 是全局唯一的，任何 Agent 或前端系统都可以用它来定位目标成员。

**2. 通信层（Mailbox）**
- 每个成员有一个**收件箱（inbox）**，消息被持久化存储（Claude Code 用 `~/.claude/teams/{team}/inboxes/{agent}.json`）。
- 消息是**异步、可靠、可审计的**：即使接收方当前在处理其他任务，消息也不会丢失；后续轮询时可以补收。
- 由于 CCServer 是服务器部署，应该用 `StorageAdapter`（sqlite/mongo）替代文件锁 JSON，实现更高并发的 Mailbox。

**3. 消息格式层（Message Schema）**
- 所有成员之间的消息必须遵循统一的格式，至少包含：`from`（发送者）、`text`（内容）、`timestamp`（时间戳）、`read`（是否已读）、`summary`（摘要）、`msg_type`（消息类型）。
- 这是协议最关键的部分：它定义了 "Agent 之间说什么语言"。

**4. 语义层（Message Types / 工作流程语义）**
团队协议不是只能传闲聊消息，它定义了几种**改变团队状态的关键消息类型**：

| 消息类型 | 发送方 | 接收方 | 语义 |
|----------|--------|--------|------|
| `chat` | 任意成员 | 指定成员 / 广播 | 普通工作消息，如 "我找到 bug 在 line 45" |
| `idle_notification` | Worker | Lead / 广播 | "我当前空闲/可用" 或 "我任务完成了" |
| `shutdown_request` | Lead | Worker | "请优雅退出，不要再认领新任务" |
| `permission_request` | Worker | Lead | "我要执行 Edit，请审批" |
| `permission_response` | Lead | Worker | "你的 Edit 请求已批准/拒绝" |
| `new_task` | Dispatcher | Worker | "分配给你一个新任务，请开始执行" |

这些消息类型构成了 Agent Team 的**状态机流转规则**：
- Worker 发 `idle_notification` → Dispatcher 为其分配任务 → Dispatcher 发 `new_task` → Worker 切换为 running → Worker 完成后再发 `idle_notification`...

**5. 工具接口层（LLM 可见的协议入口）**
- 协议必须暴露给 LLM，让它能通过工具来 "遵守" 协议。
- 最核心的工具是 `SendMessageTool`：LLM 调用它才能向其他 Agent 发送消息。
- 如果没有这个工具，LLM 只会把内容写在回复文本里，其他 Agent 永远看不到。

**一句话总结**：
> **团队协议 = 寻址规则 + 持久化邮箱 + 标准消息格式 + 工作流程语义 + LLM 可调用的通信工具。**

对于 CCServer 来说，建立团队协议的意义尤其大：
- 因为你是 **前后端分离 + Web 服务器**，Mailbox 可以持久化在数据库里，支持 "用户刷新页面后仍能看到团队消息流"。
- 因为你有 `SSEEmitter`，可以把 Mailbox 中的新消息实时推送到前端，实现 "团队消息看板"。
- 因为你的 `TaskManager` 已经有任务依赖体系，团队协议可以让 `Lead Agent 发消息 → 改变任务分配 → Worker Agent 收到消息后执行" 形成闭环。

#### 现状
CCServer 有 `SessionAgentBus`，它基于内存中的 `asyncio.Queue`：
```python
class SessionAgentBus:
    self._mailboxes: dict[str, asyncio.Queue]
```
这有严重限制：
- 进程重启，消息全丢。
- 不支持跨 Session 通信（Queue 绑定在 Session 实例内）。
- 消息格式是裸 `dict`，没有协议规范（无 `from`、`timestamp`、`read`、消息类型等）。
- **LLM 没有 `SendMessageTool`**，Agent 无法主动与其他 Agent 通信。

#### 目标（Claude Code 模型）
- 每个 Agent 有一个 **Mailbox**，基于持久化文件（Claude Code 用 `~/.claude/teams/{team}/inboxes/{agent}.json`）+ 文件锁防止并发写冲突。
- 标准消息结构：
  ```python
  class TeammateMessage:
      from: str           # 发送者名称
      text: str           # 内容
      timestamp: str      # ISO 时间
      read: bool
      summary: str | None # 5-10 词摘要，前端预览用
      msg_type: str       # "chat" | "idle_notification" | "permission_request" | ...
  ```
- `SendMessageTool` 是 LLM 可见的工具，参数为 `to: str`（`*` 表示广播）、`message: str`、`summary: str`。

#### 缺口等级：高

#### 现状
CCServer 有 `SessionAgentBus`，它基于内存中的 `asyncio.Queue`：
```python
class SessionAgentBus:
    self._mailboxes: dict[str, asyncio.Queue]
```
这有严重限制：
- 进程重启，消息全丢。
- 不支持跨 Session 通信（Queue 绑定在 Session 实例内）。
- 消息格式是裸 `dict`，没有协议规范（无 `from`、`timestamp`、`read`、消息类型等）。
- **LLM 没有 `SendMessageTool`**，Agent 无法主动与其他 Agent 通信。

#### 目标（Claude Code 模型）
- 每个 Agent 有一个 **Mailbox**，基于持久化文件（Claude Code 用 `~/.claude/teams/{team}/inboxes/{agent}.json`）+ 文件锁防止并发写冲突。
- 标准消息结构：
  ```python
  class TeammateMessage:
      from: str           # 发送者名称
      text: str           # 内容
      timestamp: str      # ISO 时间
      read: bool
      summary: str | None # 5-10 词摘要，前端预览用
      msg_type: str       # "chat" | "idle_notification" | "permission_request" | ...
  ```
- `SendMessageTool` 是 LLM 可见的工具，参数为 `to: str`（`*` 表示广播）、`message: str`、`summary: str`。

#### 缺口等级：高

#### 结合 CCServer 定位的建议设计

因为 CCServer 是**前后端分离、服务器部署**，直接使用文件锁邮箱（Claude Code 的方式）虽然可行，但在高并发服务器场景下效率不高。建议采用 **"存储层 Mailbox"** 方案：

**方案 A：StorageAdapter 持久化 Mailbox（推荐）**
在 `StorageAdapter` 中增加方法：
```python
async def append_inbox_message(self, team_name: str, recipient: str, message: dict) -> None
async def list_inbox_messages(self, team_name: str, recipient: str, unread_only: bool = False) -> list[dict]
async def mark_inbox_read(self, team_name: str, recipient: str, message_ids: list[str]) -> None
```
- **sqlite/mongo 后端**：利用数据库的行锁/事务，天然并发安全。
- **file 后端**：回退到文件锁（如 `filelock`）+ JSON Lines 文件。
- **多 Team 隔离**：`team_name` 作为第一级命名空间，天然支持 "同一 Session 内多个 Graph Node 各开独立 Team" 的场景（见 2.8 节）。

**方案 B：Redis Pub/Sub（高并发扩展）**
如果未来需要横向扩展多进程，可以将 `SessionAgentBus` 替换为 Redis Pub/Sub + 本地缓存。这个可以作为 Phase 2 的扩展。

**`SendMessageTool` 设计**：
- 新增 `ccserver/builtins/tools/send_message.py`
- Schema：
  ```json
  {
    "to": "string (required)",
    "message": "string (required)",
    "summary": "string (optional)"
  }
  ```
- `to="*"` 时调用 `bus.broadcast()`。
- 该工具只在 `Team` 上下文中注册给成员 Agent。是否注册由 "Team 开关" 控制（见 2.6 节）。

**消息协议扩展**：
除了纯文本 `chat`，还需要定义结构化消息类型：
```python
class IdleNotificationMessage(TeammateMessage):
    msg_type: Literal["idle_notification"]
    idle_reason: Literal["available", "interrupted", "failed"]
    completed_task_id: str | None
    completed_status: Literal["resolved", "blocked", "failed"] | None

class ShutdownRequestMessage(TeammateMessage):
    msg_type: Literal["shutdown_request"]
    reason: str | None

class ShutdownResponseMessage(TeammateMessage):
    msg_type: Literal["shutdown_response"]
    request_id: str
    approve: bool
```

---

### 2.3 权限同步机制：从 "单 Agent 内弹窗" 到 "跨 Agent 权限桥接"

#### 现状
CCServer 的权限检查在 `Agent._handle_tools()` 中完成：
- `interactive` 模式：调用 `emitter.emit_permission_request()`，通过 SSE/WSEmitter 弹窗等待用户确认。
- `auto` 模式：直接拒绝 `ask_tools` 列表中的工具。

**问题**：子 Agent（尤其是 `spawn_background` 的后台 Agent）使用的是 `QueueEmitter`，它的 `emit_permission_request()` 默认返回 `False`（见 `BaseEmitter` 默认实现）。这意味着：
- 后台子 Agent 遇到敏感工具时，在 `interactive` 模式下也无法正确把弹窗透传到前端；在 `auto` 模式下直接失败。
- 没有 "Worker Agent 向 Lead Agent 请求权限" 的桥接机制。

#### 目标（Claude Code 模型）
- Worker Agent 遇到需要审批的工具时，向 Lead Agent 的 mailbox 发送 `permission_request`。
- Lead Agent（或其前端 UI）收到请求后，用户做出决定。
- Lead Agent 向 Worker 的 mailbox 回写 `permission_response`。
- Worker poller 检测到响应后继续执行。
- In-process 模式下，可直接复用 Lead 的 `emit_permission_request()` 弹窗。

#### 缺口等级：高

#### 结合 CCServer 定位的建议设计

CCServer 的前后端分离架构在这里反而是**优势**。建议设计一个 **"权限中继"（Permission Relay）** 机制：

```
Worker Agent (subagent)
    ↓ 遇到 ask_tools 中的工具
    ↓ 构造 permission_request 消息
    ↓ 写入 TeamMailbox (recipient = "__lead__" 或 team.lead_agent_id)
         ↓
TeamPermissionPoller (一个全局/每 Team 的协程)
    ↓ 监听到新请求
    ↓ 如果是 in-process 且 Lead 是本进程：直接调用 Lead.emitter.emit_permission_request()
    ↓ 否则：通过 SSE 推送 "team_permission_request" 事件到前端
         ↓
前端用户确认 / 拒绝
    ↓ POST /teams/{team_id}/permissions/{request_id}/respond
    ↓ 写入 TeamMailbox (recipient = worker_agent_id) 作为 permission_response
         ↓
Worker Agent 的 _loop() 在下一轮工具执行前检查 mailbox
    ↓ 发现 permission_response，继续或拒绝
```

**关键新增模块**：
- `ccserver/team/permission_relay.py`：`TeamPermissionRelay`
- `ccserver/team/poller.py`：`TeamMailboxPoller`，定期轮询团队成员的 mailbox
- `server.py` 新增路由：
  - `POST /teams/{team_id}/permissions/{request_id}/respond`

**Agent._handle_tools() 需要修改**：
当 Agent 检测到自己在 Team 中且不是 Lead 时，不走 `emitter.emit_permission_request()`，而是走 `TeamPermissionRelay.request(...)`。

---

### 2.4 发布任务并自主认领：从 "父 Agent 显式 spawn" 到 "任务池 + 自动调度"

#### 现状
CCServer 的 `TaskManager` 已经是一个功能完善的任务池：
- 支持 `create()`、`update()`、`bind_agent()`、`complete()`、`fail()`
- 支持 `blocked_by` / `blocks` 依赖关系
- 支持 `can_start()` 检查依赖是否满足

但 `AgentScheduler` 只能由父 Agent **显式调用**：
```python
scheduler.spawn(prompt=..., agent_def=..., agent_name=...)
```
后台 Agent (`spawn_background`) 执行完就销毁（`done`/`error`/`cancelled`），**没有 "Idle 等待" 状态**。

#### 目标（Claude Code 模型）
- Team Lead 通过 `TaskCreate` 发布任务到任务池（已有）。
- Teammate Agent 完成一个任务后，不销毁，进入 **Idle 状态**，自动发送 `idle_notification`。
- Idle 的 Teammate 定期调用 `tryClaimNextTask()`，从任务池中查找 `pending` + `无 owner` + `依赖已满足` 的任务，自动 `bind_agent()` 并执行。
- Lead Agent 可随时通过 `SendMessageTool` 发送 `shutdown_request` 终止 Idle 的 Teammate。

#### 缺口等级：中（但业务价值高）

#### 结合 CCServer 定位的建议设计

**分两种使用场景**：

**场景 A：Pipeline 预定义工作流（当前 Graph 擅长）**
适合 "代码教学" 场景：固定步骤（如 `备课 → 出题 → 批改 → 反馈`），用 `Graph` 的 `AgentNode` + `FunctionNode` 编排即可。这部分**不需要**太多改动。

**场景 B：动态团队任务调度（Agent Team 的核心）**
适合 "生产部署中的复杂任务拆分"：Lead 分析需求后拆成多个子任务，Spawn 多个 Agent 并行执行，Agent 完成后可继续认领新任务。

**实现建议**：

1. **扩展 `BackgroundAgentHandle`**：增加 `idle_mode: bool` 字段，以及 `enter_idle()` / `is_idle` 方法。
2. **新增 `TeamTaskDispatcher`**：一个协程，监听 Team 中所有 Idle Agent，并尝试为它们 `claim_task()`。
   ```python
   class TeamTaskDispatcher:
       async def run(self):
           while self.team.is_active:
               for handle in self.team.idle_handles:
                   task = self._find_claimable_task()
                   if task:
                       await self._assign_and_wake(handle, task)
               await asyncio.sleep(POLL_INTERVAL)
   ```
3. **修改 `Agent._loop()` 的尾部逻辑**：
   当 ` Agent` 是 teammate 且 `idle_mode=True` 时，round limit 到达或任务完成后，不 emit `done`，而是：
   - 设置 `state.phase = "idle"`
   - 向 Team mailbox 发送 `idle_notification`
   - 进入等待状态（监听 inbox 或一个 `asyncio.Event`）
4. **唤醒机制**：
   - 新任务分配时，`TeamTaskDispatcher` 向 Agent 的 inbox 发送 `"new_task"` 类型消息。
   - Agent 的 `_drain_inbox_and_respond()` 检测到 `"new_task"` 后，将其内容作为新的 user message 进入下一轮 `_loop()`。

**任务绑定增强**：
在 `TaskManager.create()` 时可以指定 `agent_type`（如 `"researcher"`），`TeamTaskDispatcher` 在分配时可将任务优先分配给对应 `agent_def.name` 的 Idle Agent。

---

### 2.5 协调器模式（Coordinator Mode）：从 "Graph 静态编排" 到 "Lead 动态调度"

#### 现状
`Graph` 是一个**预定义**的有向有环图。节点和边在 `build()` 中写死，运行时按边条件流转。虽然强大，但它：
- 无法根据运行时洞察 **动态 spawn 新 Agent**
- 没有 "Research → Synthesis → Implementation → Verification" 的默认工作流语义

#### 目标（Claude Code 模型）
协调器是一个特殊的 Team Lead，它本身也是 AI 驱动，拥有专门的工具集：
- `AgentTool` → Spawn worker
- `SendMessageTool` → Continue existing worker
- `TaskStopTool` → Stop worker
协调器工作流四阶段：
1. Research（workers 并行研究代码库）
2. Synthesis（coordinator 综合理解）
3. Implementation（workers 按规范实施）
4. Verification（workers 验证）

#### 缺口等级：中

#### 结合 CCServer 定位的建议设计

对 CCServer 而言，**"协调器模式" 可以作为一个内置的 `AgentDef` 或一个特殊的 `Graph` 模板**来实现。

**方案：内置 `coordinator` Agent + `CoordinatorGraph`**

1. **内置 Agent 定义**：`builtins/agents/coordinator.md`
   - system prompt 中注入协调器工作流四阶段规范
   - tools 白名单中包含 `Agent`、`SendMessage`、`TaskCreate`、`TaskUpdate`、`TaskList`、`TaskStop`
   - `team_capable: true`（见开关设计）

2. **新增 `CoordinatorGraph` 类**（继承 `Graph`）：
   ```python
   class CoordinatorGraph(Graph):
       def build(self):
           self.entry = "coordinator"
           self.add_node(AgentNode(id="coordinator", ...))
           # coordinator 内部通过 AgentTool 动态 spawn 子节点
           # 跳出 Graph 的静态限制
   ```
   或者更简单：在 `server.py` 的 chat 路由中，当检测到请求使用 `coordinator` agent 时，走一个专门的协调器调度循环。

3. **前端可视化**：
   通过 SSE 推送 `team_member_joined`、`team_member_idle`、`team_task_assigned` 等事件，让前端展示一个 "团队看板"。

---

### 2.6 开关设计：如何控制 "是否支持 Team 功能的 Agent"

#### 需求
用户要求 "用一个开关来设置是否支持 team 功能的 agent"。这是因为：
- Agent Team 功能会改变 Agent 的行为模式（增加 idle 状态、mailbox 轮询、SendMessageTool 等）。
- 对于简单的子 Agent 调用，不应强制开启 Team 语义和开销。
- 教学/演示场景中，可能需要降级到普通子 Agent 模式。

#### 建议设计：双层开关

**第一层：全局 Feature Flag（settings.json）**
```json
{
  "userAgentTeam": true
}
```
- 默认 `false`。当 `false` 时：`Agent.spawn_child` / `spawn_background` 保持现有行为，不加载任何 Team 相关工具、不启动 mailbox poller。
- 当 `true` 时：系统初始化 `TeamRegistry`，`BTAgent` 的 schema 中可选地暴露 `team_name`、`name` 参数。

**第二层：AgentDef 级别标记（agent markdown frontmatter）**
```yaml
---
name: researcher
description: 代码研究员
is_team_capable: true   # 此 Agent 可以作为 Team 成员运行
---
```
- `is_team_capable: true` 的 AgentDef，在 spawn 时可以选择走 `teammate` 路径（注册到 Team、启用 Mailbox、添加 SendMessageTool）。
- `is_team_capable: false`（默认）的 AgentDef，只能作为普通子 Agent 运行。

**第三层：运行时的 `AgentTool` 参数控制**
修改 `BTAgent` 的 schema，增加可选参数：
```json
{
  "team_name": "string (optional)",
  "name": "string (optional)",
  "mode": "string (optional)"   // "plan" | "auto" | ...
}
```
- 当 `team_name` + `name` 同时传入且 `userAgentTeam=true` 时，触发 `spawn_teammate()` 路径。
- 当只传入 `subagent_type` 时，走现有的普通子 Agent 路径。

**实现位置**：
- `ccserver/settings.py`：`ProjectSettings.experimental_teams: bool`
- `ccserver/managers/agents/manager.py`：`AgentDef.is_team_capable: bool`
- `ccserver/builtins/tools/agent.py`：`BTAgent.params` 增加 `team_name`、`name`
- `ccserver/agent.py`：`Agent._handle_agent()` 中增加分支判断

---

### 2.7 Agent 执行后端：从 "纯 In-Process" 到 "服务器友好的多进程/容器化"

#### 现状
CCServer 的所有 Agent 都在同一个 Python 进程内以 `asyncio.Task` 运行。Claude Code 除了 in-process 外，还支持 tmux/iTerm2 分屏。

#### 结合 CCServer 定位的分析
CCServer 主打**服务器生产部署**，tmux 分屏对服务器场景几乎无用。但纯 in-process 有明确的瓶颈：
- GIL 限制 CPU 密集型任务并行
- 单点故障：一个 Agent 的未捕获异常可能导致整个进程崩溃
- 资源隔离差：一个 Agent 的内存泄漏会影响所有 Agent

#### 建议：引入 "进程池 + RPC" 后端（长期）
对于服务器部署，真正的扩展方向不是 tmux，而是：
1. **子进程执行**：通过 `multiprocessing` 或 `subprocess` 启动隔离的 Python 进程运行 Agent。
2. **容器化执行**：通过 Docker/K8s 启动独立容器（适合生产环境彻底隔离）。
3. **gRPC/HTTP 通信**：子 Agent 进程通过 gRPC 或 HTTP 调用主进程的工具服务。

**短期可做**：
- 在 `AgentDef` 中预留 `execution_backend: str = "in_process"` 字段。
- `TeamRegistry` 在设计时就把 backend 信息纳入成员元数据，为后续扩展留好接口。

---

### 2.8 Graph Node 作为 Team Lead：嵌套式 Agent Team 调度

#### 关键特性声明

这是 CCServer 区别于 Claude Code 的**核心架构特色**：

> **Graph 的 `AgentNode` 本身可以是一个开启了 `userAgentTeam` 的 Agent（Team Lead），在其内部动态 spawn teammates，并行执行子任务。**

换句话说，Pipeline 中的某个节点不只是一次简单的 LLM 调用，而是一个**微型的、自治的 Agent Team**。Claude Code 的 Team 只能在 Session 级别由用户或 Lead Agent 直接创建；而 CCServer 的 Team 可以**嵌套在 Pipeline 的任意节点内部**，作为整个工作流的一个环节。

#### 带来的架构影响与需补能力

**1. AgentNode 参数扩展**

现有的 `AgentNode`（`ccserver/pipeline/node.py`）只有 `agent_config: dict` 用于覆盖 AgentFactory 参数。但 Team 需要额外的显式字段：
- `enable_team: bool` — 该节点是否作为 Team Lead 运行。
- `team_name: str | None` — 手动指定 Team 名称；为 None 时自动生成。
- `idle_timeout: float | None` — 若 teammates 空闲超过此时间则自动 shutdown（Graph 节点需要及时收尾）。

建议直接扩展 `AgentNode` dataclass：
```python
@dataclass
class AgentNode:
    ...
    enable_team: bool = False
    team_name: str | None = None
    idle_timeout: float = 60.0   # 秒
```

**2. Team 命名空间与 Mailbox 隔离**

由于一个 Graph 内可能有多个 `AgentNode` 同时开启 Team，必须保证 Team 名称唯一，避免 Mailbox 冲突。

建议的命名规则：
```python
# 在 Graph._run_agent_node 中生成
team_name = node.team_name or f"g{graph_instance_id}__n{node.id}"
```
- `graph_instance_id`：每次 `Graph.run()` 生成一个唯一运行的图实例 ID。
- 这样同一 Graph 类多次并发执行也不会互相干扰。
- Mailbox 的 key 为 `(team_name, agent_name)`，确保同一 Session 内多个 Team 完全隔离。

**3. Session/上下文继承**

`AgentNode` 在 `Graph._run_agent_node` 中已经创建了一个 `Session`（基于 `node.agent_dir` 作为 `project_root`）。
- Team Lead 就是这个 `Session` 里创建的根 Agent。
- teammates 通过 `spawn_teammate()` 创建时，**复用同一个 Session**。
- 因此 teammates 自动继承该 Node 的 `.ccserver/` 配置、MCP 服务器、Hooks 等——这对 "代码教学" 场景极其友好：每个教学模块（Graph Node）有自己独立的环境和配置。

**4. 生命周期管理：Graph 节点收尾必须关闭 Team**

这是最大的实现陷阱之一。如果 `Graph._run_agent_node` 执行完 `agent.run(prompt)` 就直接返回 `NodeData`，其内部 spawn 的 teammates 很可能还处于 **Idle 状态** 挂起等待新任务。这不仅造成资源泄漏，也会导致同 Session 内 Team Registry 的脏数据累积。

**建议的收尾逻辑**（在 `Graph._run_agent_node` 的 `finally` 块中）：
```python
async def _run_agent_node(...):
    try:
        final_text = await agent.run(prompt)
        # ... 等待 teammates 完成或超时
    finally:
        if node.enable_team and team.is_active:
            # 1. 向所有 idle teammates 发送 shutdown_request
            await team.broadcast_shutdown(reason="node finished")
            # 2. 等待最多 idle_timeout 秒让所有队友退出
            await team.wait_for_all_members_shutdown(timeout=node.idle_timeout)
            # 3. 注销 Team，释放资源
            team_registry.unregister(team.name)
```

**5. NodeData 输出聚合**

当 `AgentNode` 内部有 Team 时，节点的 `output_key` 对应值不应只是 Lead Agent 的 `final_text`，而需要 **自动聚合** 所有 teammates 的 `output_summary` / `result`。

建议的聚合格式：
```python
{
    "lead_output": final_text,
    "tasks": [
        {"agent_name": "researcher", "status": "completed", "summary": "..."},
        {"agent_name": "coder", "status": "completed", "summary": "..."},
    ]
}
```
这样下游 `FunctionNode` 可以做进一步的路由和判断。

**6. 事件透传与 TUI 客户端渲染**

由于 `AgentNode` 是在 `Graph.run()` 中执行的（通常由 `server.py` 的 `/chat/stream` 路由调用），Team 内部产生的事件必须正确透传到 emitter：
- `team_member_joined` / `team_member_idle` / `team_task_assigned` 等新事件需要在 `BaseEmitter` 中定义 `fmt_*` 方法。
- `tui_http.py` 已经具备 `BackgroundTaskManager` 渲染底部任务栏的能力，可以扩展为 `TeamStatusManager`，在终端底部画一个 teammates 状态面板。
- `tui.py`（直接后端入口）可以通过 `TUIEmitter` 的多区域输出实现类似效果：主对话区 + 团队状态区 + 后台任务区。

#### 缺口干缺项

| 缺失项 | 影响 | 等级 |
|--------|------|------|
| `AgentNode` 缺少 `enable_team` / `team_name` / `idle_timeout` 字段 | Graph 无法声明节点为 Team Lead | 高 |
| `Graph._run_agent_node` 无 Team 收尾逻辑 | teammates 泄漏、资源不释放 | 高 |
| `TeamMailbox` 未考虑 "同一 Session 多 Team" 隔离 | Graph 多节点开 Team 时消息串扰 | 高 |
| `TaskManager` 的 task 未与 `graph_run_id` 关联 | 无法区分不同 Graph 实例的任务池 | 中 |
| `AgentTaskRegistry` 缺少按 team_name 过滤 API | 难以统计某个 Node 内部 Team 的运行状态 | 中 |
| `BaseEmitter` / `TUIEmitter` 缺少 `team_*` 事件格式 | TUI/Web 无法可视化 teammates | 中 |

---

## 三、需要新增的模块与修改的文件清单

按优先级分为 Phase 1（基础）、Phase 2（核心）、Phase 3（增强）。

### Phase 1：Team 基础抽象 + 开关（2-3 周）

| 新增/修改 | 文件路径 | 说明 |
|-----------|----------|------|
| 新增 | `ccserver/team/__init__.py` | Team 包入口 |
| 新增 | `ccserver/team/models.py` | `Team`, `TeamMember`, `TeamMessage` 等数据类 |
| 新增 | `ccserver/team/registry.py` | `TeamRegistry`（Session 级或全局） |
| 新增 | `ccserver/team/helpers.py` | `format_agent_id()`, `sanitize_name()`, `assign_color()` |
| 修改 | `ccserver/storage/base.py` | 增加 `save_team`, `load_team`, `list_teams`, `delete_team` |
| 修改 | `ccserver/storage/file_adapter.py` | 实现文件版 team 持久化 |
| 修改 | `ccserver/storage/sqlite_adapter.py` | 实现 sqlite 版 team 持久化 |
| 修改 | `ccserver/storage/mongo_adapter.py` | 实现 mongo 版 team 持久化 |
| 修改 | `ccserver/settings.py` | 增加 `user_agent_team` 字段解析 |
| 修改 | `ccserver/managers/agents/manager.py` | `AgentDef` 增加 `is_team_capable` 字段 |
| 修改 | `ccserver/builtins/tools/agent.py` | `BTAgent.params` 增加 `team_name`, `name` |
| 修改 | `ccserver/agent.py` | `Agent._handle_agent()` 增加 team 分支判断 |
| 修改 | `ccserver/session.py` | `Session` 增加 `_team_registry` 属性 |
| 修改 | `ccserver/pipeline/node.py` | `AgentNode` 增加 `enable_team`、`team_name`、`idle_timeout` 字段 |

### Phase 2：Mailbox + SendMessage + 权限桥接（3-4 周）

| 新增/修改 | 文件路径 | 说明 |
|-----------|----------|------|
| 新增 | `ccserver/team/mailbox.py` | `TeamMailbox`：基于 StorageAdapter 的持久化消息存取 |
| 新增 | `ccserver/team/poller.py` | `TeamMailboxPoller`：轮询 Agent mailbox 的协程 |
| 新增 | `ccserver/team/permission_relay.py` | `TeamPermissionRelay`：跨 Agent 权限请求桥接 |
| 新增 | `ccserver/builtins/tools/send_message.py` | `BTSendMessage` 工具 |
| 修改 | `ccserver/agent.py` | `_handle_tools()` 中增加 team 权限桥接逻辑；`_drain_inbox_and_respond()` 处理更多消息类型 |
| 修改 | `ccserver/agent_bus.py` | 扩展或重写为持久化总线（或新建 `PersistentAgentBus`） |
| 修改 | `ccserver/emitters/base.py` | 增加 `fmt_team_permission_request` / `fmt_team_message` 等事件格式 |
| 修改 | `ccserver/pipeline/graph.py` | `_run_agent_node()` 增加 Team 初始化与 `finally` 收尾 shutdown 逻辑 |
| 修改 | `server.py` | 新增 `/teams`, `/teams/{id}/inbox`, `/teams/{id}/permissions/{req_id}/respond` 等路由 |

### Phase 3：任务认领 + 协调器 + 前端看板（3-4 周）

| 新增/修改 | 文件路径 | 说明 |
|-----------|----------|------|
| 新增 | `ccserver/team/dispatcher.py` | `TeamTaskDispatcher`：Idle Agent 自动认领任务调度器 |
| 新增 | `ccserver/team/coordinator.py` | `CoordinatorEngine`：协调器模式封装 |
| 新增 | `ccserver/builtins/agents/coordinator.md` | 内置协调器 Agent 定义 |
| 修改 | `ccserver/agent_handle.py` | `BackgroundAgentHandle` 增加 idle 状态管理 |
| 修改 | `ccserver/agent_scheduler.py` | `AgentScheduler` 增加 `spawn_teammate()` 和 idle Agent 监控 |
| 修改 | `ccserver/managers/tasks/manager.py` | `TaskManager` 增加 `claim_next_available(agent_name)` 方法 |
| 修改 | `server.py` | SSE 推送 `team_member_idle`, `team_task_assigned` 等事件 |
| （前端） | `clients/tui_http.py` 或独立 Web 客户端 | 增加团队看板渲染 |

---

## 四、与 Claude Code 的关键差异（CCServer 的机遇）

| 维度 | Claude Code | CCServer 的机会 |
|------|-------------|-----------------|
| 部署形态 | 终端/TUI 应用 | **Web 服务器**，天然适合多人协作、团队看板 |
| 通信后端 | 文件 JSON inbox（本地单用户） | **StorageAdapter**（sqlite/mongo/redis）支持多实例、高并发 |
| 执行隔离 | tmux/iTerm2（本地分屏） | **进程池/容器化**（更适合生产环境） |
| UI 可视化 | 终端窗格（本地 tmux 分屏） | **多客户端并存**：<br>• Web 前端看板（任务列表、消息流、权限审批中心）<br>• `tui_http.py` 通过 SSE 接收 `team_member_idle` 等事件，在终端底部渲染团队状态栏/任务看板<br>• `tui.py` 直接后端入口，亦可通过 `TUIEmitter` 的多事件区域渲染 teammates<br>tmux 分屏对 server 模式无意义，SSE 流式事件才是 CCServer 的 team 可视化主线 |
| 工作流定义 | 以 Agent 协调器为主 | **Graph Pipeline + Agent Team 双轨**：预定义流程 + 动态调度并存 |
| 目标用户 | 个人开发者 | **企业/教育机构**：代码教学、自动化流水线、团队协作 |

这意味着 CCServer 不需要完全照搬 Claude Code 的 tmux/文件锁方案。应该**发挥服务器架构优势**，用数据库存储替代文件 inbox，用 Web 前端替代终端分屏，用 REST/SSE API 替代 CLI 参数传递。

---

## 五、总结：最核心需要补的 5 个缺口

如果把所有功能排个优先级，建议按以下顺序攻坚：

1. **Team 抽象 + 确定性 Agent ID**（骨架）
   - 没有 Team，就没有后续的任何事情。

2. **持久化 Mailbox + SendMessageTool**（血液）
   - 没有通信，Agent 之间无法协作。

3. **跨 Agent 权限桥接**（神经）
   - 没有权限上传下达，子 Agent 无法安全地使用 `Edit`/`Bash` 等核心工具。

4. **Idle 语义 + 任务自动认领**（心脏）
   - 没有这一步，Agent Team 只是 "并行跑完即销毁"，不是真正的团队。

5. **协调器 Agent + 前端团队看板**（大脑 + 五官）
   - 让 Lead Agent 能动态调度，让用户能可视化地观察、干预团队运行。

**开关控制**（`userAgentTeam` + `is_team_capable`）应作为 Phase 1 的一部分同步实现，以便在教学/演示场景中随时切换。
