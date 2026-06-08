# Task Manager 改进方案

> 基于 Claude Code 设计灵感：持久化存储 + 后台执行 + Agent 绑定 + 任务依赖
> 目标：支持多存储后端、任务类型区分、Agent 绑定

---

## 一、现有问题分析

### 当前实现 (ccserver/managers/tasks/manager.py)

| 维度 | 现状 | 问题 |
|------|------|------|
| 存储 | 内存 | 重启丢失，无法跨会话 |
| 任务类型 | 无 | 全部视为通用任务 |
| Agent 绑定 | 无 | 无法绑定具体 Agent |
| 后台执行 | 无 | 无法后台运行 |
| 依赖关系 | 无 | 无法表达阻塞关系 |
| 状态 | 简单 3 状态 | 无运行中状态 |

### Claude Code 参考设计

| 系统 | 存储 | 用途 | 关键特性 |
|------|------|------|---------|
| **任务清单** | 文件系统 | 任务清单 | 持久化、跨会话、依赖 |
| **后台任务** | 内存 | 后台执行 | 实时状态、流式输出 |

---

## 二、目标设计

### 2.1 总体架构

```
┌─────────────────────────────────────────────────────────────┐
│                      Task Manager                           │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│   ┌─────────────────────────────────────────────────────┐  │
│   │           直接复用现有 StorageAdapter                │  │
│   │   (FileStorageAdapter / MongoStorageAdapter)        │  │
│   │            新增 create_task / load_task 等          │  │
│   └─────────────────────────────────────────────────────┘  │
│                          │                                  │
│   ┌──────────────────┐    ┌───────────────────┐          │
│   │ 持久化 Task       │    │ Background Task   │          │
│   │ (跨会话)         │    │ (内存实时)         │          │
│   └──────────────────┘    └───────────────────┘          │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### 2.2 任务类型

直接使用字符串标识：

| 类型 | 描述 |
|------|------|
| `"local_agent"` | 本地子 Agent |
| `"background_agent"` | 后台 Agent |
| `"local_bash"` | 本地命令执行 |
| `"function"` | 函数执行 |
| `"mcp_tool"` | MCP 工具调用 |

### 2.3 任务状态

**持久化 Task 状态**：

| 状态 | 描述 |
|------|------|
| `pending` | 待处理 |
| `in_progress` | 处理中（已绑定 Agent��� |
| `completed` | 已完成 |
| `failed` | 失败 |
| `deleted` | 已删除 |

**后台 Task 状态**：

| 状态 | 描述 |
|------|------|
| `pending` | 等待执行 |
| `running` | 运行中 |
| `completed` | 已完成 |
| `failed` | 失败 |
| `killed` | 已终止 |

---

## 三、核心数据结构

### 3.1 Storage 结构（复用现有 storage 模块）

```
方案 A：在现有 session 目录下扩展
    {base_dir}/
      {session_id}/
        meta.json
        messages.jsonl
        tasks/              ← 新增
          1.json
          2.json

方案 B：独立目录（推荐）
    ~/.config/ccserver/tasks/{session_id}/
      1.json
      2.json
```

### 3.2 Task 存储（直接利用现有 StorageAdapter）

现有 `StorageAdapter` 只提供了 session 的存取方法，缺乏通用的 Task 存储接口。

**最佳方案：直接扩展 `StorageAdapter`**

```python
class StorageAdapter(ABC):
    # ... 现有方法 ...

    # ── Task 存储 ──────────────────────────────────────────────
    
    def create_task(self, session_id: str, task: "Task") -> None:
        """创建任务。默认未实现，需要各 adapter 自行覆盖。"""
        raise NotImplementedError(f"{self.__class__.__name__} 未实现 create_task")

    def load_task(self, session_id: str, task_id: str) -> Optional["Task"]:
        """加载任务。"""
        raise NotImplementedError(f"{self.__class__.__name__} 未实现 load_task")

    def update_task(self, session_id: str, task: "Task") -> None:
        """更新任务。"""
        raise NotImplementedError(f"{self.__class__.__name__} 未实现 update_task")

    def list_tasks(self, session_id: str) -> list["Task"]:
        """列出所有任务。"""
        raise NotImplementedError(f"{self.__class__.__name__} 未实现 list_tasks")

    def get_task_counter(self, session_id: str) -> int:
        """获取任务自增计数器。"""
        raise NotImplementedError(f"{self.__class__.__name__} 未实现 get_task_counter")

    def set_task_counter(self, session_id: str, value: int) -> None:
        """设置任务自增计数器。"""
        raise NotImplementedError(f"{self.__class__.__name__} 未实现 set_task_counter")
```

然后在现有 adapter 中实现这些方法：

**FileStorageAdapter 扩展示例：**

```python
class FileStorageAdapter(StorageAdapter):
    # ... 现有方法 ...

    def _tasks_dir(self, session_id: str) -> Path:
        return self._session_dir(session_id) / "tasks"

    def create_task(self, session_id: str, task: "Task") -> None:
        path = self._tasks_dir(session_id) / f"{task.id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(task.to_dict(), indent=2, ensure_ascii=False))

    def load_task(self, session_id: str, task_id: str) -> Optional["Task"]:
        path = self._tasks_dir(session_id) / f"{task_id}.json"
        if not path.exists():
            return None
        data = json.loads(path.read_text())
        return Task.from_dict(data)

    def update_task(self, session_id: str, task: "Task") -> None:
        self.create_task(session_id, task)  # 直接覆盖

    def list_tasks(self, session_id: str) -> list["Task"]:
        tasks_dir = self._tasks_dir(session_id)
        if not tasks_dir.exists():
            return []
        tasks = []
        for f in tasks_dir.glob("*.json"):
            task = Task.from_dict(json.loads(f.read_text()))
            if task:
                tasks.append(task)
        return sorted(tasks, key=lambda t: int(t.id))

    def get_task_counter(self, session_id: str) -> int:
        hw = self._tasks_dir(session_id) / ".highwatermark"
        if hw.exists():
            return int(hw.read_text())
        return 0

    def set_task_counter(self, session_id: str, value: int) -> None:
        hw = self._tasks_dir(session_id)
        hw.mkdir(parents=True, exist_ok=True)
        (hw / ".highwatermark").write_text(str(value))
```

**MongoStorageAdapter 扩展示例：**

```python
class MongoStorageAdapter(StorageAdapter):
    # ... 现有 __init__ ...
    
    # tasks collection 可以在 __init__ 中注册
    # self._tasks = self._db["tasks"]

    async def init_indexes(self):
        # ... 现有索引 ...
        await self._tasks.create_index([("session_id", ASCENDING), ("id", ASCENDING)])

    def create_task(self, session_id: str, task: "Task") -> None:
        doc = {**task.to_dict(), "session_id": session_id}
        self._tasks.insert_one(doc)

    def load_task(self, session_id: str, task_id: str) -> Optional["Task"]:
        doc = self._tasks.find_one({"session_id": session_id, "id": task_id})
        if doc is None:
            return None
        doc.pop("session_id", None)
        return Task.from_dict(doc)

    def update_task(self, session_id: str, task: "Task") -> None:
        doc = {**task.to_dict(), "session_id": session_id}
        self._tasks.replace_one({"session_id": session_id, "id": task.id}, doc, upsert=True)

    def list_tasks(self, session_id: str) -> list["Task"]:
        docs = self._tasks.find({"session_id": session_id}).sort("id", ASCENDING)
        tasks = []
        for doc in docs:
            doc.pop("session_id", None)
            tasks.append(Task.from_dict(doc))
        return tasks
```

**TaskManager 直接复用现有 StorageAdapter：**

### 3.3 Task（持久化任务）

```python
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


@dataclass
class Task:
    """
    任务记录。
    支持持久化存储，跨会话恢复。
    """
    # ── 基础字段 ───────────────────────────────────
    id: str                           # 任务 ID（如 "1", "2"）
    type: str = "local_agent"         # 任务类型
    subject: str = ""                 # 任务标题
    description: str = ""            # 任务描述

    # ── 状态字段 ───��───────────────────────────────
    status: str = "pending"          # pending/in_progress/completed/failed/deleted

    # ── Agent 绑定 ─────────────────────────────────
    assigned_agent_id: Optional[str] = None  # 绑定的 Agent ID
    agent_type: Optional[str] = None          # agent 类型

    # ── 依赖关系 ───────────────────────────────────
    blocked_by: list[str] = field(default_factory=list)  # 阻塞此任务的任务 ID
    blocks: list[str] = field(default_factory=list)      # 此任务阻塞的任务 ID

    # ── 时间戳 ───────────────────────────────────
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    # ── 输出 ───────────────────────────────────────
    output_summary: Optional[str] = None   # 执行结果摘要
    output_data: Optional[dict] = None   # 结构化输出

    def to_dict(self) -> dict:
        """序列化为字典。"""
        return {
            "id": self.id,
            "type": self.type,
            "subject": self.subject,
            "description": self.description,
            "status": self.status,
            "assigned_agent_id": self.assigned_agent_id,
            "agent_type": self.agent_type,
            "blocked_by": self.blocked_by,
            "blocks": self.blocks,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "output_summary": self.output_summary,
            "output_data": self.output_data,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Task":
        """从字典反序列化。"""
        return cls(
            id=data["id"],
            type=data.get("type", "local_agent"),
            subject=data.get("subject", ""),
            description=data.get("description", ""),
            status=data.get("status", "pending"),
            assigned_agent_id=data.get("assigned_agent_id"),
            agent_type=data.get("agent_type"),
            blocked_by=data.get("blocked_by", []),
            blocks=data.get("blocks", []),
            created_at=datetime.fromisoformat(data["created_at"]) if data.get("created_at") else None,
            started_at=datetime.fromisoformat(data["started_at"]) if data.get("started_at") else None,
            completed_at=datetime.fromisoformat(data["completed_at"]) if data.get("completed_at") else None,
            output_summary=data.get("output_summary"),
            output_data=data.get("output_data"),
        )
```

### 3.4 Background Task（后台执行）

```python
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Any


@dataclass
class BackgroundTask:
    """
    后台执行任务。
    存储在内存，实时状态，流式输出。
    """
    # ── 基础字段 ───────────────────────────────────
    id: str                    # 任务 ID（如 "bg001"）
    type: str                 # 任务类型

    # ── 状态字段 ────────────��─��────────────────────
    status: str = "pending"   # pending/running/completed/failed/killed

    # ── 执行内容 ───────────────────────────────────
    command: Optional[str] = None   # local_bash：执行的命令
    prompt: Optional[str] = None      # local_agent/background_agent：prompt
    agent_def: Optional[str] = None   # agent 定义路径

    # ── 输出字段 ───────────────────────────────────
    output: Optional[str] = None    # 最终输出
    error: Optional[str] = None    # 错误信息
    exit_code: Optional[int] = None  # 进程退出码

    # ── Agent 绑定 ─────────────────────────────────
    agent_id: Optional[str] = None  # 绑定的 Agent ID

    # ── 进度跟踪 ──────────────────────────────────
    progress: Optional[dict] = None   # 进度信息
    recent_activities: list[dict] = field(default_factory=list)  # 最近活动

    # ── 时间戳 ───────────────────────────────────
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None

    # ── 控制字段 ───────────────────────────────────
    is_backgrounded: bool = True       # 是否后台运行
    abort_controller: Optional[Any] = None  # 中止控制器


class BackgroundTaskState:
    """后台任务运行时状态，用于 UI 显示。"""

    def __init__(self, task: BackgroundTask):
        self._task = task

    @property
    def id(self) -> str:
        return self._task.id

    @property
    def status(self) -> str:
        return self._task.status

    @property
    def is_running(self) -> bool:
        return self._task.status == "running"

    @property
    def is_done(self) -> bool:
        return self._task.status in ("completed", "failed", "killed")

    @property
    def description(self) -> str:
        """用于 UI 显示的描述。"""
        if self._task.type == "local_bash":
            return self._task.command or ""
        elif self._task.type in ("local_agent", "background_agent"):
            return self._task.prompt or ""
        return self._task.id
```

---

## 四、Task Manager API（复用 StorageAdapter）

```python
class TaskManager:
    """
    任务管理器。
    直接复用现有的 StorageAdapter，无需新建存储接口。
    """

    def __init__(self, session_id: str, storage: "StorageAdapter"):
        self._session_id = session_id
        self._storage = storage

    def _next_id(self) -> str:
        return str(self._storage.get_task_counter(self._session_id) + 1)

    def create(
        self,
        subject: str,
        description: str = "",
        task_type: str = "local_agent",
    ) -> Task:
        """创建新任务。"""
        task_id = self._next_id()
        task = Task(
            id=task_id,
            type=task_type,
            subject=subject,
            description=description,
        )
        self._storage.create_task(self._session_id, task)
        self._storage.set_task_counter(self._session_id, int(task_id))
        return task

    def get(self, task_id: str) -> Task:
        """获取任务。"""
        task = self._storage.load_task(self._session_id, task_id)
        if task is None:
            raise ValueError(f"Task #{task_id} not found")
        return task

    def update(
        self,
        task_id: str,
        status: str | None = None,
        subject: str | None = None,
        description: str | None = None,
    ) -> Task:
        """更新任务。"""
        task = self.get(task_id)
        if status is not None:
            task.status = status
        if subject is not None:
            task.subject = subject
        if description is not None:
            task.description = description
        self._storage.update_task(self._session_id, task)
        return task

    def list_all(self) -> list[Task]:
        """列出所有任务（除 deleted）。"""
        return [t for t in self._storage.list_tasks(self._session_id) if t.status != "deleted"]

    def bind_agent(self, task_id: str, agent_id: str, agent_type: str | None = None) -> Task:
        """绑定任务到 Agent。"""
        task = self.get(task_id)
        task.assigned_agent_id = agent_id
        if agent_type:
            task.agent_type = agent_type
        task.status = "in_progress"
        task.started_at = datetime.now(timezone.utc)
        self._storage.update_task(self._session_id, task)
        return task

    def complete(self, task_id: str, summary: str, output_data: dict | None = None) -> Task:
        """标记任务完成。"""
        task = self.get(task_id)
        task.status = "completed"
        task.completed_at = datetime.now(timezone.utc)
        task.output_summary = summary
        task.output_data = output_data
        self._storage.update_task(self._session_id, task)
        return task

    def can_start(self, task: Task) -> bool:
        """检查任务是否可以开始（所有依赖已完成）。"""
        for dep_id in task.blocked_by:
            dep = self._storage.load_task(self._session_id, dep_id)
            if dep is None or dep.status != "completed":
                return False
        return True
```

---

## 五、工具层设计

### 5.1 Task 工具

| 工具 | 功能 |
|------|------|
| `TaskCreate` | 创建任务 |
| `TaskUpdate` | 更新任务 |
| `TaskList` | 列出任务 |
| `TaskGet` | 获取单个任务 |

### 5.2 Background Task 工具

| 工具 | 功能 |
|------|------|
| `BackgroundCreate` | 创建后台任务 |
| `BackgroundList` | 列出后台任务 |
| `BackgroundKill` | 终止后台任务 |

---

## 六、集成到 Session

```python
class Session:
    # ... 现有字段 ...

    _task_manager: Optional["TaskManager"] = None

    @property
    def tasks(self) -> "TaskManager":
        if self._task_manager is None:
            self._task_manager = TaskManager(self.id)
        return self._task_manager
```

---

## 七、实现步骤

### Phase 1: 基础（必须先做）

| 步骤 | 任务 | 文件 |
|------|------|------|
| 1.1 | 定义 Task 数据类 | `managers/tasks/task.py` |
| 1.2 | 扩展 StorageAdapter 基类 | `storage/base.py` |
| 1.3 | FileStorageAdapter 增加 Task 方法 | `storage/file_adapter.py` |
| 1.4 | MongoStorageAdapter 增加 Task 方法 | `storage/mongo_adapter.py` |
| 1.5 | 实现 TaskManager（复用现有 storage） | `managers/tasks/manager.py` |
| 1.6 | 集成到 Session | `session.py` |

### Phase 2: 后台任务

| 步骤 | 任务 | 文件 |
|------|------|------|
| 2.1 | 实现 BackgroundTask | `managers/tasks/background_task.py` |
| 2.2 | 实现后台任务工具 | `tools/bt_background.py` |

### Phase 3: 高级特性

| 步骤 | 任务 | 文件 |
|------|------|------|
| 3.1 | Agent 绑定集成 | `agent.py` |
| 3.2 | 进度跟踪 | `background_task.py` |

---

## 八、总结

本方案提供：

1. **复用现有 StorageAdapter**：直接扩展 `storage/base.py` 的基类，无需新建存储接口
2. **Task 持久化**：跨会话恢复
3. **任务类型**：local_agent / background_agent / local_bash 等
4. **Agent 绑定**：Task 可绑定到具体 Agent
5. **依赖关系**：blocked_by / blocks 表达任务依赖

下一步：等待用户确认后实现。