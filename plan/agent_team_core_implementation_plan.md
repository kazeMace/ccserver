# CCServer Agent Team 核心功能实施计划

> 目标范围：Agent Team 抽象、团队协议、任务发布与自主认领  
> 暂不涉及：Graph Node 作为 Team Lead（后续扩展）  
> 开关控制：`userAgentTeam`（settings.json）+ `is_team_capable`（agent .md frontmatter）  
> 日期：2026-04-12

---

## 一、总体架构

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           Session (HTTP/SSE)                            │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌────────────────┐  │
│  │  TeamRegistry│  │ TaskManager │  │  AgentBus   │  │ AgentScheduler │  │
│  │   (new)      │  │  (exists)   │  │  (extends)  │  │   (extends)    │  │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘  └───────┬────────┘  │
│         │                │                │                 │          │
│  ┌──────▼────────────────▼────────────────▼─────────────────▼────────┐  │
│  │                        Team (per Session)                         │  │
│  │  lead: Agent                                                       │  │
│  │  members: [Agent | BackgroundAgentHandle]                         │  │
│  │  mailbox: TeamMailbox (持久化)                                    │  │
│  │  dispatcher: TeamTaskDispatcher (协程)                            │  │
│  │  permission_relay: TeamPermissionRelay                            │  │
│  └───────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────┘
```

一个 `Session` 内可以有 0 或 1 个 `Team`。Team 的生命周期与 Session 绑定，但状态和 Mailbox 通过 `StorageAdapter` 持久化。

---

## 二、开关设计（Phase 0 先导）

### 2.1 全局开关 `userAgentTeam`

支持两种配置方式，**环境变量优先级高于 settings.json**：

**方式 A：环境变量**
```bash
export CCSERVER_USER_AGENT_TEAM=true
```

**方式 B：settings.json**
```json
// ~/.ccserver/settings.json 或 <project>/.ccserver/settings.local.json
{
  "userAgentTeam": true
}
```

`ProjectSettings` 增加：
```python
user_agent_team: bool = False
```

优先级规则：
```python
user_agent_team = (
    os.getenv("CCSERVER_USER_AGENT_TEAM", "").lower() in ("1", "true", "yes")
    or project_data.get("userAgentTeam", False)
    or global_data.get("userAgentTeam", False)
)
```
- 环境变量为 `"true"` / `"1"` / `"yes"` 时直接开启。
- 环境变量未开启时，回退到 settings.json 的 `userAgentTeam` 字段。

逻辑：
- `false`（默认）：`Agent._handle_agent()` 保持现有行为，不暴露 `team_name`/`name`，`Session` 不初始化 `TeamRegistry`。
- `true`：`Session.__post_init__()` 初始化 `_team_registry = TeamRegistry()`；`BTAgent` 的 schema 增加 `team_name`、`name` 字段。

### 2.2 Agent 定义级别开关 `is_team_capable`

文件：`ccserver/managers/agents/manager.py`

```yaml
---
name: researcher
description: 代码研究员
is_team_capable: true
---
```

`AgentDef` 增加：
```python
is_team_capable: bool = False
```

逻辑：
- 只有 `is_team_capable=true` 的 Agent，才允许被 spawn 为 teammate（限制工具集，启用 Mailbox、SendMessageTool 等）。
- `is_team_capable=false` 的 AgentDef，即使传了 `team_name`，也只是普通子 Agent（向后兼容）。

---

## 三、Team 抽象层（Phase 1）

### 3.1 数据模型

文件：`ccserver/team/models.py`

```python
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


class MemberStatus(str, Enum):
    IDLE = "idle"           # 空闲，等待任务
    RUNNING = "running"     # 正在执行任务
    OFFLINE = "offline"     # 已关闭/未启动


class TeamMessageType(str, Enum):
    CHAT = "chat"
    IDLE_NOTIFICATION = "idle_notification"
    NEW_TASK = "new_task"
    SHUTDOWN_REQUEST = "shutdown_request"
    SHUTDOWN_RESPONSE = "shutdown_response"
    PERMISSION_REQUEST = "permission_request"
    PERMISSION_RESPONSE = "permission_response"


@dataclass
class TeamMember:
    agent_id: str           # 格式: "name@teamName"
    name: str               # 显示名称，如 "researcher"
    team_name: str
    status: MemberStatus = MemberStatus.IDLE
    joined_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    color: str = "#3B82F6"  # 默认蓝色，前端用
    current_task_id: Optional[str] = None
    agent_task_id: Optional[str] = None   # BackgroundAgentHandle 的 task id

    @property
    def is_idle(self) -> bool:
        return self.status == MemberStatus.IDLE


@dataclass
class Team:
    name: str               # 团队名称，等于 session_id 或用户自定义
    session_id: str
    lead_agent_id: str      # 根 Agent 的 agent_id
    description: Optional[str] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    members: dict[str, TeamMember] = field(default_factory=dict)   # agent_id -> Member
    is_active: bool = True

    def get_member(self, agent_id: str) -> Optional[TeamMember]:
        return self.members.get(agent_id)

    def list_idle(self) -> list[TeamMember]:
        return [m for m in self.members.values() if m.is_idle]
```

### 3.2 注册表

文件：`ccserver/team/registry.py`

```python
import asyncio
from typing import Optional
from loguru import logger

from .models import Team, TeamMember


class TeamRegistry:
    """
    Session 级别的 Team 注册表。
    一个 Session 内理论上只有一个活跃 Team（简单模型）。
    """

    def __init__(self):
        self._teams: dict[str, Team] = {}          # team_name -> Team
        self._session_team: dict[str, str] = {}    # session_id -> team_name

    def register(self, team: Team) -> None:
        self._teams[team.name] = team
        self._session_team[team.session_id] = team.name
        logger.info("Team registered | name={} session={}", team.name, team.session_id[:8])

    def get(self, team_name: str) -> Optional[Team]:
        return self._teams.get(team_name)

    def get_by_session(self, session_id: str) -> Optional[Team]:
        name = self._session_team.get(session_id)
        return self._teams.get(name) if name else None

    def unregister(self, team_name: str) -> None:
        team = self._teams.pop(team_name, None)
        if team:
            self._session_team.pop(team.session_id, None)
            logger.info("Team unregistered | name={}", team_name)

    def add_member(self, team_name: str, member: TeamMember) -> None:
        team = self._teams.get(team_name)
        if team:
            team.members[member.agent_id] = member

    def remove_member(self, team_name: str, agent_id: str) -> None:
        team = self._teams.get(team_name)
        if team:
            team.members.pop(agent_id, None)
```

### 3.3 StorageAdapter 扩展

文件：`ccserver/storage/base.py`

新增接口方法：

```python
# ── Team 持久化 ──
async def save_team(self, session_id: str, team_data: dict) -> None: ...
async def load_team(self, session_id: str) -> dict | None: ...
async def delete_team(self, session_id: str) -> None: ...

# ── Mailbox 持久化 ──
async def append_inbox_message(
    self, session_id: str, team_name: str, recipient: str, message: dict
) -> None: ...

async def list_inbox_messages(
    self, session_id: str, team_name: str, recipient: str, unread_only: bool = False
) -> list[dict]: ...

async def mark_inbox_read(
    self, session_id: str, team_name: str, recipient: str, message_ids: list[str]
) -> None: ...
```

**命名空间设计**：
- 一个 Session 可能有多个 Team（未来扩展），所以 Mailbox 的 key 是 `(session_id, team_name, recipient)`。
- 当前版本一个 Session 只支持一个 Team，但仍保留 `team_name` 字段以便未来无缝扩展。

**FileStorageAdapter 实现**：
- Team 数据：`{sessions_dir}/{session_id}/team.json`
- Mailbox：`{sessions_dir}/{session_id}/inboxes/{team_name}/{recipient}.jsonl`（JSON Lines，追加写）
- 使用 `aiofile` 或 `filelock` 保证并发安全（文件后端的 lock 只在 file adapter 内部处理）。

**SQLiteAdapter**：
- 新增 `teams` 表（session_id, team_name, data_json）
- 新增 `inbox_messages` 表（id, session_id, team_name, recipient, message_json, read, created_at）

**MongoAdapter**：
- `teams` collection 或内嵌到 `session` document。
- `inbox_messages` collection，以 `(session_id, team_name, recipient)` 建复合索引。

---

## 四、团队协议：Mailbox + SendMessageTool（Phase 2）

### 4.1 消息协议完整定义

文件：`ccserver/team/protocol.py`

```python
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Literal


@dataclass
class TeamMessage:
    """所有团队消息的统一基类。"""
    msg_id: str                     # uuid4 或时间戳+随机数
    msg_type: str
    from_agent: str                 # 发送者 agent_id
    to_agent: str                   # 接收者 agent_id；"*" 表示广播
    text: str
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    read: bool = False
    summary: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "msg_id": self.msg_id,
            "msg_type": self.msg_type,
            "from": self.from_agent,
            "to": self.to_agent,
            "text": self.text,
            "timestamp": self.timestamp,
            "read": self.read,
            "summary": self.summary,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TeamMessage":
        return cls(
            msg_id=data["msg_id"],
            msg_type=data["msg_type"],
            from_agent=data["from"],
            to_agent=data["to"],
            text=data["text"],
            timestamp=data["timestamp"],
            read=data.get("read", False),
            summary=data.get("summary"),
        )


@dataclass
class IdleNotificationMessage(TeamMessage):
    msg_type: str = "idle_notification"
    idle_reason: Literal["available", "interrupted", "failed"] = "available"
    completed_task_id: Optional[str] = None
    completed_status: Optional[Literal["resolved", "blocked", "failed"]] = None


@dataclass
class NewTaskMessage(TeamMessage):
    msg_type: str = "new_task"
    task_id: str = ""
    task_prompt: str = ""


@dataclass
class ShutdownRequestMessage(TeamMessage):
    msg_type: str = "shutdown_request"
    reason: Optional[str] = None


@dataclass
class PermissionRequestMessage(TeamMessage):
    msg_type: str = "permission_request"
    request_id: str = ""
    tool_name: str = ""
    tool_input: dict = field(default_factory=dict)
    description: str = ""        # 人类可读的描述


@dataclass
class PermissionResponseMessage(TeamMessage):
    msg_type: str = "permission_response"
    request_id: str = ""
    approved: bool = False
    feedback: Optional[str] = None
```

### 4.2 TeamMailbox 实现

文件：`ccserver/team/mailbox.py`

```python
import uuid
from typing import Optional
from loguru import logger

from ccserver.storage.base import StorageAdapter
from .protocol import TeamMessage


class TeamMailbox:
    """
    基于 StorageAdapter 的持久化邮箱。
    每个 (team_name, agent_id) 对应一个收件箱。
    """

    def __init__(self, session_id: str, team_name: str, adapter: Optional[StorageAdapter]):
        self.session_id = session_id
        self.team_name = team_name
        self.adapter = adapter

    async def send(self, message: TeamMessage) -> None:
        """发送消息到指定接收者 inbox。"""
        if self.adapter is None:
            logger.warning("Mailbox send skipped | no adapter")
            return
        await self.adapter.append_inbox_message(
            self.session_id, self.team_name, message.to_agent, message.to_dict()
        )
        logger.debug(
            "Mailbox sent | team={} to={} type={}",
            self.team_name, message.to_agent, message.msg_type
        )

    async def broadcast(self, message: TeamMessage, exclude: Optional[str] = None) -> None:
        """广播到团队成员（需要 Team 的成员列表支持，可传入成员 agent_id 列表）。"""
        # 具体实现见 Team 层封装；Mailbox 只负责单收件箱操作
        pass

    async def fetch_unread(self, recipient: str) -> list[TeamMessage]:
        """获取某 agent 的未读消息。"""
        if self.adapter is None:
            return []
        rows = await self.adapter.list_inbox_messages(
            self.session_id, self.team_name, recipient, unread_only=True
        )
        return [TeamMessage.from_dict(r) for r in rows]

    async def mark_read(self, recipient: str, msg_ids: list[str]) -> None:
        if self.adapter is None:
            return
        await self.adapter.mark_inbox_read(self.session_id, self.team_name, recipient, msg_ids)
```

### 4.3 SendMessageTool

文件：`ccserver/builtins/tools/send_message.py`

```python
from .base import BuiltinTools, ToolParam, ToolResult


class BTSendMessage(BuiltinTools):
    """
    在 Agent Team 中向指定队友发送消息。
    仅在 userAgentTeam=true 且 Agent 是 teammate 时注册到工具集。
    """
    name = "SendMessage"

    params = {
        "to": ToolParam(
            type="string",
            description='目标队友名称（如 "researcher"）或 "*" 广播',
        ),
        "message": ToolParam(
            type="string",
            description="消息正文",
        ),
        "summary": ToolParam(
            type="string",
            description="5-10 词摘要，供 UI 预览用",
            required=False,
        ),
    }

    async def run(self, to: str, message: str, summary: str = "") -> ToolResult:
        # 实际逻辑由 Agent._handle_send_message() 拦截执行
        # 此处仅提供 schema 供 LLM 调用
        return ToolResult.ok("")
```

拦截点放在 `ccserver/agent.py` 的 `_handle_tools()` 中：

```python
elif name == "SendMessage":
    result = await self._handle_send_message(input_)
```

`_handle_send_message()` 逻辑：
1. 检查当前 Agent 是否属于某个 Team（通过 `session.team_registry.get_by_agent_id(self.context.agent_id)`）。
2. 构造 `TeamMessage`，`from_agent = self.context.agent_id`。
3. `to == "*"` 时，遍历 Team.members 向每个成员 inbox 写入消息（排除自己）。
4. `to != "*"` 时，解析为 `to_agent = format_agent_id(to, team.name)`，写入目标 inbox。
5. 返回 `ToolResult.ok(f"Message sent to {to}")`。

### 4.4 Mailbox Poller

文件：`ccserver/team/poller.py`

```python
import asyncio
from loguru import logger

from .mailbox import TeamMailbox
from .protocol import TeamMessage, NewTaskMessage, ShutdownRequestMessage, PermissionResponseMessage


class TeamMailboxPoller:
    """
    为单个 Agent 轮询其 Mailbox 的协程。
    在 teammate 进入 idle 或 running 时启动，与 Agent.run() 并发运行。
    """

    POLL_INTERVAL = 2.0  # 秒

    def __init__(
        self,
        mailbox: TeamMailbox,
        agent_id: str,
        inbox_queue: asyncio.Queue,   # 把新消息注入 Agent 的 inbox Queue
    ):
        self.mailbox = mailbox
        self.agent_id = agent_id
        self.inbox_queue = inbox_queue
        self._task: asyncio.Task | None = None
        self._stopped = False

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._stopped = True
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _loop(self) -> None:
        try:
            while not self._stopped:
                messages = await self.mailbox.fetch_unread(self.agent_id)
                if messages:
                    msg_ids = [m.msg_id for m in messages]
                    await self.mailbox.mark_read(self.agent_id, msg_ids)
                    for m in messages:
                        await self.inbox_queue.put(m.to_dict())
                        logger.debug(
                            "Poller delivered | to={} type={} from={}",
                            self.agent_id, m.msg_type, m.from_agent
                        )
                await asyncio.sleep(self.POLL_INTERVAL)
        except asyncio.CancelledError:
            logger.debug("MailboxPoller cancelled | agent_id={}", self.agent_id)
        except Exception as e:
            logger.error("MailboxPoller error | agent_id={} error={}", self.agent_id, e)
```

> 此 Poller 在 `spawn_teammate()` 成功后与 Agent.run() 同时启动，负责把持久化 Mailbox 的消息实时搬运到内存中的 `BackgroundAgentHandle.inbox`。

---

## 五、任务发布与自主认领（Phase 3）

### 5.1 核心交互流程

```
1. Lead Agent 调用 TaskCreate → 写入 TaskManager（已有）
2. Lead Agent 调用 AgentTool(team_name=..., name="researcher")
    → spawn_teammate() 启动 teammate
3. teammate 完成初始 prompt 后：
    a) 若还有后续任务：直接继续（由 Lead 通过 SendMessage 分配）
    b) 若当前任务完成且无新指令：state.phase = "idle"
       → 发送 idle_notification 到 Mailbox
       → _loop() 挂起等待 inbox 新消息
4. TeamTaskDispatcher 协程检测到 idle teammate + 可认领任务
    → 调用 TaskManager.bind_agent(task_id, member.agent_id)
    → 向 teammate inbox 发送 NewTaskMessage
5. teammate 的 Poller 将 NewTaskMessage 搬到 inbox
    → _loop() 被唤醒，将 task_prompt 作为 user message 继续执行
6. teammate 完成后再次 idle... 循环直到收到 ShutdownRequestMessage
```

### 5.2 TeamTaskDispatcher

文件：`ccserver/team/dispatcher.py`

```python
import asyncio
from loguru import logger

from ccserver.managers.tasks.manager import TaskManager
from .models import Team, TeamMember, MemberStatus
from .mailbox import TeamMailbox
from .protocol import NewTaskMessage


class TeamTaskDispatcher:
    """
    Team 级别的任务调度器协程。
    定期检查 Team 的 idle members 和 TaskManager 中的可认领任务，进行匹配分配。
    """

    POLL_INTERVAL = 3.0  # 秒

    def __init__(
        self,
        team: Team,
        task_manager: TaskManager,
        mailbox: TeamMailbox,
    ):
        self.team = team
        self.task_manager = task_manager
        self.mailbox = mailbox
        self._task: asyncio.Task | None = None
        self._stopped = False

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop())
        logger.info("TeamTaskDispatcher started | team={}", self.team.name)

    async def stop(self) -> None:
        self._stopped = True
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("TeamTaskDispatcher stopped | team={}", self.team.name)

    async def _loop(self) -> None:
        try:
            while not self._stopped and self.team.is_active:
                idle_members = self.team.list_idle()
                for member in idle_members:
                    task = self._find_claimable_task(member)
                    if task:
                        await self._assign(member, task)
                await asyncio.sleep(self.POLL_INTERVAL)
        except asyncio.CancelledError:
            logger.debug("Dispatcher cancelled | team={}", self.team.name)
        except Exception as e:
            logger.error("Dispatcher error | team={} error={}", self.team.name, e)

    def _find_claimable_task(self, member: TeamMember):
        """为指定成员查找可认领任务。"""
        for task in self.task_manager.list_all():
            if task.status != "pending":
                continue
            if task.agent_id is not None:
                continue
            if not self.task_manager.can_start(task):
                continue
            # 可选：agent_type 匹配优先
            if task.agent_type and task.agent_type != member.name:
                continue
            return task
        return None

    async def _assign(self, member: TeamMember, task) -> None:
        """将任务绑定到成员，并发送 NewTaskMessage 唤醒。"""
        self.task_manager.bind_agent(task.id, member.agent_id, agent_type=member.name)
        member.status = MemberStatus.RUNNING
        member.current_task_id = task.id

        msg = NewTaskMessage(
            msg_id=f"nt-{task.id}",
            msg_type="new_task",
            from_agent=self.team.lead_agent_id,
            to_agent=member.agent_id,
            text=f"新任务分配给你：{task.subject}\n\n{task.description}",
            task_id=task.id,
            task_prompt=task.description or task.subject,
        )
        await self.mailbox.send(msg)
        logger.info(
            "Task assigned | team={} task={} member={}",
            self.team.name, task.id, member.agent_id
        )
```

### 5.3 Agent 的 Idle 语义改造

文件：`ccserver/agent.py`

`_loop()` 中需要进行两处改动：

**改动 A：_drain_inbox_and_respond 增强**

```python
async def _drain_inbox_and_respond(self, outbox: "QueueEmitter | None") -> list[dict]:
    """
    读取 inbox，处理多种消息类型。返回需要追加到 messages 的新用户消息列表。
    """
    new_messages = []
    if outbox is None:
        return new_messages

    while True:
        try:
            msg = self.context.inbox.get_nowait()
        except asyncio.QueueEmpty:
            break

        msg_type = msg.get("msg_type")

        if msg.get("type") == "status_request":
            progress = {
                "round_num": self.state.round_num,
                "max_rounds": self.round_limit,
                "phase": self.state.phase,
                "current_tool": self.state.current_tool,
            }
            await outbox.put({"type": "progress", **progress})

        elif msg_type == "new_task":
            new_messages.append({
                "role": "user",
                "content": msg.get("task_prompt", msg.get("text", "")),
                "_ccserver_team_new_task": True,
                "task_id": msg.get("task_id"),
            })

        elif msg_type == "shutdown_request":
            # 注入 system 消息提示 Agent 优雅结束
            new_messages.append({
                "role": "system",
                "content": "[Team Lead 请求你优雅退出，总结当前进度后结束。]",
                "_ccserver_team_shutdown": True,
            })

        elif msg_type == "chat":
            new_messages.append({
                "role": "user",
                "content": f"[{msg.get('from_agent')}] {msg.get('text', '')}",
            })

    return new_messages
```

**改动 B：_loop() 尾部增加 Idle 分支**

```python
# 在 _loop() 原有 return round_text 之前增加判断
if self._is_teammate_and_should_idle():
    self.state.phase = "idle"
    await self._send_idle_notification("available")

    # 挂起等待新消息（最多等一段时间，不永久阻塞）
    wait_event = asyncio.Event()

    def _wakeup(_):
        wait_event.set()

    # 监听 inbox 有新消息时唤醒
    # 简单做法：用 Poller 已经把消息搬到 inbox_queue，这里直接 await get()
    try:
        msg = await asyncio.wait_for(self.context.inbox.get(), timeout=60.0)
        self.context.inbox.put_nowait(msg)  # 放回去，下轮 _drain_inbox_and_respond 处理
    except asyncio.TimeoutError:
        # 超时后继续 idle 循环，或发送 heartbeat
        pass

    # 如果被 shutdown_request 标记，则退出 _loop
    if self._should_shutdown:
        self.state.phase = "done"
        return round_text + "\n[shutdown by lead]"

    # 否则继续下一轮 _loop（新任务已在 inbox 中）
    return await self._loop(outbox=outbox)
```

> `_is_teammate_and_should_idle()` 的判断逻辑：Agent 在 Team 中（`context.name` 和 `team_registry` 可查到）且当前没有 `shutdown` 标记。

### 5.4 任务完成后的状态回写

文件：`ccserver/agent_handle.py` 或 `ccserver/agent.py`

当 teammate 正常完成或被判定为 "任务结束" 时，需要：
1. 更新 `TaskManager` 中的任务状态（`complete()` 或 `fail()`）。
2. 更新 `TeamMember` 状态为 `IDLE`，`current_task_id = None`。

建议在 `forward_agent_events()` 中监听 `done` / `error` 事件时增加：

```python
# 在 forward_agent_events 的 done/error 分支中
async def _sync_task_on_done(handle: BackgroundAgentHandle, team, task_manager):
    member = team.get_member(handle.agent_id)
    if member and member.current_task_id:
        task = task_manager.get(member.current_task_id)
        if task and task.status == "in_progress":
            if handle.agent_task_state and handle.agent_task_state.status == "completed":
                task_manager.complete(task.id, handle.agent_task_state.result or "")
            else:
                task_manager.fail(task.id, handle.agent_task_state.error or "unknown")
        member.status = MemberStatus.IDLE
        member.current_task_id = None
```

---

## 六、权限桥接（Phase 2 同步）

### 6.1 跨 Agent 权限请求流程

```
Worker Agent 在 _handle_tools() 中遇到 ask_tools 工具
    ↓ 检查自己是否属于 Team
    ↓ 是 → 不走 emitter.emit_permission_request()
    ↓ 构造 PermissionRequestMessage
    ↓ 写入 Mailbox (to = team.lead_agent_id)
         ↓
TeamPermissionRelay (协程) 轮询 Lead 的 inbox
    ↓ 发现新的 permission_request
    ↓ 调用 Lead 的 emitter.emit_permission_request()
         ↓
前端用户批准/拒绝（SSE 弹窗）
    ↓ Lead Agent 收到响应
    ↓ TeamPermissionRelay 构造 PermissionResponseMessage
    ↓ 写入 Mailbox (to = worker_agent_id)
         ↓
Worker 的 Poller 将 PermissionResponseMessage 注入 Worker.inbox
    ↓ Worker 的 _drain_inbox_and_respond() 处理
    ↓ Worker 继续执行或拒绝
```

### 6.2 TeamPermissionRelay

文件：`ccserver/team/permission_relay.py`

```python
import asyncio
import uuid
from typing import Optional
from loguru import logger

from .models import Team
from .mailbox import TeamMailbox
from .protocol import PermissionRequestMessage, PermissionResponseMessage


class TeamPermissionRelay:
    """
    监听 Team Lead 的 Mailbox，将 permission_request 转译为前端的弹窗事件。
    """

    POLL_INTERVAL = 1.0

    def __init__(self, team: Team, mailbox: TeamMailbox, lead_emitter):
        self.team = team
        self.mailbox = mailbox
        self.lead_emitter = lead_emitter
        self._task: asyncio.Task | None = None
        self._stopped = False
        # request_id -> asyncio.Event 映射，用于等待 user 响应
        self._pending: dict[str, asyncio.Event] = {}
        self._results: dict[str, bool] = {}

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._stopped = True
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _loop(self) -> None:
        try:
            while not self._stopped and self.team.is_active:
                msgs = await self.mailbox.fetch_unread(self.team.lead_agent_id)
                for m in msgs:
                    if m.msg_type != "permission_request":
                        continue
                    await self.mailbox.mark_read(self.team.lead_agent_id, [m.msg_id])
                    asyncio.create_task(self._handle_permission_request(m))
                await asyncio.sleep(self.POLL_INTERVAL)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("PermissionRelay error | team={} error={}", self.team.name, e)

    async def _handle_permission_request(self, req_msg: PermissionRequestMessage) -> None:
        """
        调用 Lead 的 emitter 弹窗。注意：这里假设 Lead 的 emitter 支持
        emit_permission_request() 且能阻塞等待用户响应（SSEEmitter 可以）。
        """
        try:
            granted = await self.lead_emitter.emit_permission_request(
                req_msg.tool_name, req_msg.tool_input
            )
        except Exception as e:
            logger.error("PermissionRelay popup failed | error={}", e)
            granted = False

        resp = PermissionResponseMessage(
            msg_id=f"pr-{uuid.uuid4().hex[:8]}",
            msg_type="permission_response",
            from_agent=self.team.lead_agent_id,
            to_agent=req_msg.from_agent,
            text="approved" if granted else "denied",
            request_id=req_msg.request_id,
            approved=granted,
        )
        await self.mailbox.send(resp)
        logger.info(
            "PermissionRelay resolved | request_id={} approved={}",
            req_msg.request_id, granted
        )
```

### 6.3 Worker Agent 中权限处理改造

`ccserver/agent.py` 中 `_handle_tools()` 的权限检查逻辑需要增加 Team 分支：

```python
# 原有逻辑：
# if name in ask_tools:
#     if self.run_mode == "interactive":
#         granted = await self.emitter.emit_permission_request(...)

# 新增 Team 分支：
if name in ask_tools:
    team = self._get_current_team()
    if team and not self._is_team_lead(team):
        # Worker agent 通过 Mailbox 请求 Lead 审批
        req_id = f"perm-{uuid.uuid4().hex[:8]}"
        req = PermissionRequestMessage(
            msg_id=req_id,
            from_agent=self.context.agent_id,
            to_agent=team.lead_agent_id,
            text=f"请求使用 {name}",
            request_id=req_id,
            tool_name=name,
            tool_input=input_,
            description=f"Agent {self.context.name} 请求使用 {name}",
        )
        await self._send_to_mailbox(team, req)

        # 轮询等待 permission_response（最多 5 分钟）
        granted = await self._wait_permission_response(req_id, timeout=300.0)
        if not granted:
            result = ToolResult.error(f"Tool '{name}' denied by team lead.")
            results.append(result.to_api_dict(block_id))
            continue
        # granted == True 则继续执行工具
    else:
        # 非 Team 成员或 Lead 自身：走原有弹窗逻辑
        ...
```

`_wait_permission_response()` 实现：
- 每隔 1-2 秒从 Mailbox `fetch_unread` 自己的 inbox。
- 查找 `msg_type == "permission_response"` 且 `request_id == req_id` 的消息。
- 超时则默认拒绝。

> 注意：由于 Agent 的 `_loop()` 在 tool_executing 阶段，这种同步 polling 会阻塞当前 round。更优雅的方式是把这个 polling 也做成 inbox 消息驱动（通过 `TeamMailboxPoller` 注入 inbox，然后 `_drain_inbox_and_respond` 处理），但实现复杂度高。对于第一版，同步轮询 Mailbox 是简单可维护的方案。

---

## 七、spawn_teammate 入口改造

### 7.1 Agent._handle_agent 分支

文件：`ccserver/agent.py`

```python
async def _handle_agent(self, task_input: dict) -> ToolResult:
    # ... depth 检查 ...

    subagent_type = task_input.get("subagent_type", "")
    agent_name = task_input.get("description", "") or subagent_type
    model_override = task_input.get("model", "") or None
    run_in_background = bool(task_input.get("run_in_background", False))
    team_name = task_input.get("team_name", "")
    name = task_input.get("name", "")

    agent_def = self.session.agents.get(subagent_type) if subagent_type else None

    # ── Team 分支 ──
    if team_name and name and self.session.settings.user_agent_team:
        if agent_def and not agent_def.is_team_capable:
            return ToolResult.error(
                f"Agent '{subagent_type}' is not team-capable. "
                f"Set is_team_capable=true in its frontmatter."
            )
        handle = await self._spawn_teammate(
            team_name=team_name,
            name=name,
            prompt=task_input.get("prompt", ""),
            agent_def=agent_def,
            model_override=model_override,
        )
        return ToolResult.ok(
            f"Teammate '{name}' spawned in team '{team_name}' (task_id={handle.agent_task_id})"
        )

    # ── 原有非 Team 分支 ──
    if run_in_background:
        handle = self.spawn_background(...)
        return ToolResult.ok(...)
    child = self.spawn_child(...)
    summary = await child._loop()
    return ToolResult.ok(summary)
```

### 7.2 _spawn_teammate 实现

```python
async def _spawn_teammate(
    self,
    team_name: str,
    name: str,
    prompt: str,
    agent_def=None,
    model_override: str | None = None,
) -> BackgroundAgentHandle:
    """
    在 Team 上下文中启动一个 teammate。
    """
    from ccserver.team.models import Team, TeamMember, MemberStatus
    from ccserver.team.registry import TeamRegistry
    from ccserver.team.mailbox import TeamMailbox
    from ccserver.team.poller import TeamMailboxPoller
    from ccserver.team.dispatcher import TeamTaskDispatcher
    from ccserver.team.permission_relay import TeamPermissionRelay

    registry = self.session.team_registry  # 需要 session 暴露此属性

    # 查找或创建 Team
    team = registry.get(team_name)
    if team is None:
        team = Team(
            name=team_name,
            session_id=self.session.id,
            lead_agent_id=self.context.agent_id,
        )
        registry.register(team)
        # 启动 Dispatcher 和 PermissionRelay
        mailbox = TeamMailbox(self.session.id, team_name, self.session.storage)
        dispatcher = TeamTaskDispatcher(team, self.session.tasks, mailbox)
        dispatcher.start()
        relay = TeamPermissionRelay(team, mailbox, self.emitter)
        relay.start()
        # 将 dispatcher/relay 挂载到 team 或 registry 上便于后续关闭
        team._dispatcher = dispatcher
        team._relay = relay
        team._mailbox = mailbox
    else:
        mailbox = team._mailbox

    # 生成确定性 agent_id
    from ccserver.team.helpers import format_agent_id
    agent_id = format_agent_id(name, team_name)
    if registry.get_member(team_name, agent_id):
        raise RuntimeError(f"Teammate '{agent_id}' already exists in team '{team_name}'")

    # 创建 BackgroundAgentHandle（复用 spawn_background 的大部分逻辑）
    handle = self.spawn_background(
        prompt=prompt,
        agent_def=agent_def,
        agent_name=name,
        model_override=model_override,
    )
    # 覆盖 agent_id 为 team 确定性 id
    handle.agent_id = agent_id
    # 重要：子 Agent 的 context.agent_id 也需要同步
    # 但 spawn_background 已经创建了 child Agent，此时替换 id 需要额外处理
    # 更干净的做法：在 spawn_child 时传入 context.agent_id=agent_id

    # 注册成员
    member = TeamMember(
        agent_id=agent_id,
        name=name,
        team_name=team_name,
        status=MemberStatus.RUNNING,
        agent_task_id=handle.agent_task_id,
    )
    registry.add_member(team_name, member)

    # 启动 Mailbox Poller（将持久化消息注入 handle.inbox）
    poller = TeamMailboxPoller(mailbox, agent_id, handle.inbox)
    poller.start()
    handle._team_poller = poller  # 挂载到 handle 便于 cancel 时关闭

    # 给 teammate 的系统提示词追加 Team 通信规范
    child = handle._get_child_agent()  # 需要暴露引用
    if child:
        child._append_team_prompt_addendum(team_name)

    return handle
```

> 这里有个实现细节：`spawn_background()` 内部创建 child Agent 时用的 `AgentContext.agent_id` 是自动生成的 UUID。需要给 `spawn_child()` / `spawn_background()` 增加 `agent_id_override: str | None = None` 参数，或在 `AgentContext` 创建后手动覆盖。

### 7.3 teammate 的系统提示词追加

文件：`ccserver/team/prompts.py`（新增）

```python
TEAMMATE_SYSTEM_PROMPT_ADDENDUM = """
# Agent Teammate 通信规则

你当前正以 teammate 身份运行在团队 "{team_name}" 中。

 communicate 方式：
- 使用 SendMessageTool 与团队成员沟通。to 填队友名称（如 "researcher"），to="*" 表示广播。
- 普通文本回复不会被其他队友看到，必须显式调用 SendMessageTool。

任务流转：
- 完成当前任务后，你会自动进入 idle 状态等待下一个任务分配。
- 如果收到 shutdown_request 消息，请总结当前进度并优雅结束。

团队规范：
- 遇到需要审批的敏感工具时，系统会自动向 Team Lead 发起审批请求，请耐心等待。
"""
```

在 `Agent.__init__()` 中，如果 `is_teammate=True`，将这段追加到 `self.system` 末尾。

---

## 八、server.py API 路由新增

### 8.1 新增路由清单

```python
# Team 元信息
GET    /teams/{team_name}                   -> 获取 Team 状态和成员列表
DELETE /teams/{team_name}                   -> 关闭并注销 Team（广播 shutdown）

# Mailbox（前端轮询或 WebSocket 回退）
GET    /teams/{team_name}/inbox/{agent_id}  -> 获取某 agent 的未读消息
POST   /teams/{team_name}/inbox/{agent_id}/read -> 标记已读

# 权限审批（前端响应）
GET    /teams/{team_name}/permissions       -> 列出待处理的权限请求
POST   /teams/{team_name}/permissions/{request_id}/respond -> {approved: bool}

# SSE 增强事件（现有 /chat/stream 扩展）
# 新增 event type：
#   team_member_joined
#   team_member_idle
#   team_member_running
#   team_task_assigned
#   team_permission_request
```

### 8.2 SSE 事件格式扩展

`BaseEmitter` 增加：

```python
def fmt_team_member_idle(self, team_name: str, agent_id: str, name: str) -> dict:
    return self._fmt(
        "team_member_idle",
        team_name=team_name, agent_id=agent_id, name=name
    )

def fmt_team_permission_request(
    self, team_name: str, request_id: str, from_agent: str, tool_name: str, description: str
) -> dict:
    return self._fmt(
        "team_permission_request",
        team_name=team_name,
        request_id=request_id,
        from_agent=from_agent,
        tool_name=tool_name,
        description=description,
    )
```

配合 `tui_http.py` 的 `BackgroundTaskManager` 可以新增 `TeamStatusManager` 渲染底部 teammates 状态栏。

---

## 九、文件修改清单（按优先级）

### Phase 1：开关 + Team 抽象（1.5 周）

| 文件 | 操作 | 内容 |
|------|------|------|
| `ccserver/settings.py` | 修改 | 解析 `userAgentTeam` |
| `ccserver/managers/agents/manager.py` | 修改 | `AgentDef.is_team_capable` |
| `ccserver/builtins/tools/agent.py` | 修改 | `BTAgent` schema 增加 `team_name`、`name` |
| `ccserver/team/__init__.py` | 新增 | 包入口 |
| `ccserver/team/models.py` | 新增 | `Team`、`TeamMember`、`MemberStatus`、`TeamMessageType` |
| `ccserver/team/registry.py` | 新增 | `TeamRegistry` |
| `ccserver/team/helpers.py` | 新增 | `format_agent_id()`、UUID 辅助 |
| `ccserver/storage/base.py` | 修改 | `save_team` / `load_team` / `delete_team` |
| `ccserver/storage/file_adapter.py` | 修改 | 实现 |
| `ccserver/storage/sqlite_adapter.py` | 修改 | 实现 |
| `ccserver/storage/mongo_adapter.py` | 修改 | 实现 |
| `ccserver/session.py` | 修改 | `__post_init__` 中条件初始化 `TeamRegistry` |

### Phase 2：Mailbox + 协议 + SendMessageTool（1.5 周）

| 文件 | 操作 | 内容 |
|------|------|------|
| `ccserver/team/protocol.py` | 新增 | 消息类定义 |
| `ccserver/team/mailbox.py` | 新增 | `TeamMailbox` |
| `ccserver/team/poller.py` | 新增 | `TeamMailboxPoller` |
| `ccserver/builtins/tools/send_message.py` | 新增 | `BTSendMessage` |
| `ccserver/agent.py` | 修改 | `_handle_tools()` 增加 `SendMessage` 分支；`_drain_inbox_and_respond()` 增强 |
| `ccserver/storage/base.py` | 修改 | `append_inbox_message` / `list_inbox_messages` / `mark_inbox_read` |
| `ccserver/storage/*_adapter.py` | 修改 | 实现 Mailbox 接口 |

### Phase 3：权限桥接 + 任务认领 + Idle 语义（2 周）

| 文件 | 操作 | 内容 |
|------|------|------|
| `ccserver/team/permission_relay.py` | 新增 | `TeamPermissionRelay` |
| `ccserver/team/dispatcher.py` | 新增 | `TeamTaskDispatcher` |
| `ccserver/team/prompts.py` | 新增 | `TEAMMATE_SYSTEM_PROMPT_ADDENDUM` |
| `ccserver/agent.py` | 修改 | `_handle_agent()` Team 分支；`_spawn_teammate()`；`_loop()` Idle 逻辑；权限请求桥接 |
| `ccserver/agent_handle.py` | 修改 | 增加 `idle` 状态标识与 Poller 清理 |
| `ccserver/agent_scheduler.py` | 修改 | 增加 `spawn_teammate()` 封装 |
| `ccserver/managers/tasks/manager.py` | 修改 | 增加 `claim_next_available()` 或确认 `can_start()` 逻辑足够 |
| `ccserver/emitters/base.py` | 修改 | 新增 `fmt_team_*` 系列事件格式 |
| `server.py` | 修改 | 新增 `/teams/*` 路由；SSE 推送 team 事件 |
| `clients/tui_http.py` | 修改 | 扩展 `BackgroundTaskManager` 渲染 team 状态 |

---

## 十、后台任务监控设计

Agent Team 引入了大量常驻后台协程（Dispatcher、PermissionRelay、MailboxPoller、Idle Agent 自身），必须建立完善的可观测性机制：

### 10.1 监控对象

| 监控对象 | 生命周期 | 监控指标 |
|---------|---------|---------|
| `TeamTaskDispatcher` | 随 Team 创建启动，Team 注销时停止 | 调度轮次、分配成功次数、失败次数、最近一次分配时间戳 |
| `TeamPermissionRelay` | 随 Team 创建启动 | 收到请求数、已处理数、待处理数、平均处理延迟 |
| `TeamMailboxPoller` | 随 teammate spawn 启动，cancel/destroy 时停止 | 轮询次数、投递消息数、错误次数 |
| `BackgroundAgentHandle` (teammate) | spawn ~ idle/shutdown | 运行总时长、当前 phase、current_task_id、inbox 堆积深度 |

### 10.2 日志规范

所有 team 相关协程必须使用结构化日志，至少包含 `team_name`、自身标识、运行状态：

```python
logger.info(
    "TeamTaskDispatcher metrics | team={} loop_count={} assigned={} failed={}",
    self.team.name, self._loop_count, self._assigned_count, self._failed_count
)
```

### 10.3 健康检查接口

`server.py` 新增：
```python
GET /teams/{team_name}/health
```

返回：
```json
{
  "team_name": "auth-refactor",
  "is_active": true,
  "dispatcher_alive": true,
  "relay_alive": true,
  "members": [
    {"agent_id": "researcher@auth-refactor", "status": "idle", "current_task_id": null, "poller_alive": true},
    {"agent_id": "coder@auth-refactor", "status": "running", "current_task_id": "3", "poller_alive": true}
  ],
  "pending_permissions": 0,
  "idle_members": 1,
  "running_members": 1
}
```

### 10.4 异常自愈

- `TeamMailboxPoller` 异常退出时，应在 `forward_agent_events()` 中检测到 `handle._team_poller._task.done()` 后自动重启或标记 `unhealthy`。
- `TeamTaskDispatcher` / `TeamPermissionRelay` 异常退出时，应在 `TeamRegistry` 的 getter 中触发重建（lazy recovery）。
- teammate Agent 协程未捕获异常导致 `_task` 结束时，必须在 `forward_agent_events()` 的 finally 中更新 `TeamMember.status = OFFLINE` 并 `fail()` 当前任务。

---

## 十一、实施风险与注意事项

1. **Mailbox Polling 频率**：file 后端在高频轮询下可能有性能瓶颈。第一版可以设 `POLL_INTERVAL=3~5s`，sqlite/mongo 可降到 `1~2s`。
2. **Agent ID 替换时机**：`spawn_background()` 生成 child 时若强制覆盖 `agent_id`，需确保 Recorder、task registry 中 key 一致性。
3. **Team 泄漏防护**：
   - 用户断开 SSE 连接不意味着要 kill Team（后台任务应继续运行）。
   - 但 Session 被显式删除时，必须 broadcast shutdown 并清理 Team。
4. **循环依赖**：`team/` 包可能会引用 `agent.py`、`session.py`。建议通过 `TYPE_CHECKING` 延迟导入，或在 `team/` 层只定义纯数据模型，逻辑层放在 `agent.py` 的扩展方法中。
5. **权限桥接超时**：如果 Lead 的 emitter 不支持 `emit_permission_request()`（如 `CollectEmitter`）， relay 会直接返回 `False`。需在文档中说明 `userAgentTeam` 推荐配合 SSE/WSEmitter 使用。

---

**结论**：按本计划实施，CCServer 将在不改动机有单 Agent 循环核心逻辑的前提下，通过新增 `team/` 包、扩展 StorageAdapter Mailbox、改造 `_handle_agent()` 和 `_loop()` 尾部逻辑，逐步构建出一个支持**动态 spawn、持久通信、任务自动认领、权限跨 Agent 桥接**的完整 Agent Team 框架。
