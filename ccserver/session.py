"""
session — Session data model and SessionManager persistence layer.
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from loguru import logger

from .config import SESSIONS_BASE, PROJECT_DIR, GLOBAL_CONFIG_DIR
from .managers.tasks import TaskManager
from .managers.skills import SkillLoader
from .managers.agents import AgentLoader
from .managers.hooks import HookLoader
from .managers.commands import CommandLoader
from .agent_scheduler import AgentScheduler
from .agent_bus import SessionAgentBus
from .event_bus import EventBus
from .managers.cron import CronScheduler
from .tasks import ShellTaskRegistry, AgentTaskRegistry
from .mcp import MCPManager
from .settings import ProjectSettings
from .storage import StorageAdapter, SessionRecord, FileStorageAdapter
from .team import TeamRegistry


# ─── Session ──────────────────────────────────────────────────────────────────


@dataclass
class Session:
    id: str
    workdir: Path           # agent 沙箱目录：sessions/{id}/workdir/
    project_root: Path      # 项目根目录，.ccserver/ 就在这里
    storage: StorageAdapter = field(default=None, repr=False)  # 存储适配器
    messages: list = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    _tasks: Any = field(default=None, repr=False)
    _skills: Any = field(default=None, repr=False)
    _agents: Any = field(default=None, repr=False)
    _hooks: Any = field(default=None, repr=False)
    _commands: Any = field(default=None, repr=False)
    _mcp: Any = field(default=None, repr=False)
    _settings: Any = field(default=None, repr=False)
    _scheduler: Any = field(default=None, repr=False)
    _cron_scheduler: Any = field(default=None, repr=False)  # CronScheduler，定时任务调度器
    _bus: Any = field(default=None, repr=False)          # SessionAgentBus（旧点对点总线，保留兼容）
    _event_bus: Any = field(default=None, repr=False)    # EventBus（新 fan-out 广播总线）
    _shell_tasks: Any = field(default=None, repr=False)  # ShellTaskRegistry，后台 shell 任务注册表
    _agent_tasks: Any = field(default=None, repr=False)  # AgentTaskRegistry，后台 Agent 任务注册表
    _team_registry: Any = field(default=None, repr=False)  # TeamRegistry，Agent Team 注册表（可选）
    _root_agent: Any = field(default=None, repr=False)      # 根 Agent 引用，由 AgentFactory.create_root() 设置

    def __post_init__(self):
        if self._settings is None:
            self._settings = ProjectSettings.from_dirs(self.project_root)
        if self._tasks is None:
            self._tasks = TaskManager(session_id=self.id, adapter=self.storage)
        if self._skills is None:
            self._skills = SkillLoader.from_workdir(self.project_root, GLOBAL_CONFIG_DIR)
        if self._agents is None:
            self._agents = AgentLoader.from_workdir(self.project_root, GLOBAL_CONFIG_DIR)
        if self._hooks is None:
            # 通过 settings.build_hook_loader() 构建，而非 HookLoader.from_workdir()
            # 这样 settings 里的 hooks 字段（CC/ccserver 格式 + OpenClaw 控制面板）
            # 都能被正确传给 HookLoader
            self._hooks = self._settings.build_hook_loader(self.project_root)
        if self._commands is None:
            self._commands = CommandLoader.from_project_root(self.project_root, GLOBAL_CONFIG_DIR)
        if self._mcp is None:
            self._mcp = MCPManager.from_config(
                self.project_root / ".mcp.json",
                project_dir=self.project_root,
                enabled_servers=self._settings.enabled_mcp_servers,
                session=self,
            )
        if self._scheduler is None:
            self._scheduler = AgentScheduler(self)
        if self._cron_scheduler is None:
            self._cron_scheduler = CronScheduler(self)
            # 从磁盘恢复所有 durable=True 的任务
            self._cron_scheduler.load_durable_tasks()
        if self._bus is None:
            self._bus = SessionAgentBus()
        if self._event_bus is None:
            self._event_bus = EventBus()
        if self._shell_tasks is None:
            self._shell_tasks = ShellTaskRegistry()
        if self._agent_tasks is None:
            self._agent_tasks = AgentTaskRegistry()
        if self._team_registry is None and self._settings.user_agent_team:
            self._team_registry = TeamRegistry(adapter=self.storage)
            logger.debug(
                "Session team registry initialized | id={} teams={}",
                self.id[:8],
                len(self._team_registry.list_teams()),
            )

    @property
    def settings(self) -> ProjectSettings:
        return self._settings

    @property
    def tasks(self) -> TaskManager:
        return self._tasks

    @property
    def skills(self) -> SkillLoader:
        return self._skills

    @property
    def agents(self) -> AgentLoader:
        return self._agents

    @property
    def hooks(self) -> HookLoader:
        return self._hooks

    @property
    def commands(self) -> CommandLoader:
        return self._commands

    @property
    def mcp(self) -> MCPManager:
        return self._mcp

    @property
    def scheduler(self) -> AgentScheduler:
        return self._scheduler

    @property
    def cron_scheduler(self) -> "CronScheduler":
        """Session 级别的定时任务调度器。"""
        return self._cron_scheduler

    @property
    def bus(self) -> SessionAgentBus:
        """旧的点对点 Agent 邮箱总线，保留兼容。新代码请使用 event_bus。"""
        return self._bus

    @property
    def event_bus(self) -> EventBus:
        """Session 级广播事件总线，支持 fan-out 多订阅者。"""
        return self._event_bus

    @property
    def shell_tasks(self) -> ShellTaskRegistry:
        """Session 级别的后台 Shell 任务注册表。所有 run_in_background=True 的 Bash 调用均注册于此。"""
        return self._shell_tasks

    @property
    def agent_tasks(self) -> AgentTaskRegistry:
        """Session 级别的后台 Agent 任务注册表。所有 spawn_background() 启动的 Agent 均注册于此。"""
        return self._agent_tasks

    @property
    def team_registry(self) -> TeamRegistry | None:
        """Session 级别的 TeamRegistry。仅当 userAgentTeam=True 时初始化。"""
        return self._team_registry

    @property
    def root_agent(self) -> Any | None:
        """根 Agent 实例引用，由 AgentFactory.create_root() 设置。"""
        return self._root_agent

    # ── 消息持久化（委托给 storage adapter）──────────────────────────────────

    def append_message(self, message: dict):
        """追加消息到内存列表，并通过 adapter 持久化。"""
        self.messages.append(message)
        self.updated_at = datetime.now(timezone.utc)
        if self.storage:
            self.storage.append_message(self.id, message)
            self.storage.update_meta(self.id, self.updated_at)

    def persist_message(self, message: dict):
        """只将消息写入磁盘，不追加到内存列表（内存已由调用方处理）。"""
        self.updated_at = datetime.now(timezone.utc)
        if self.storage:
            self.storage.append_message(self.id, message)
            self.storage.update_meta(self.id, self.updated_at)

    def rewrite_messages(self, messages: list):
        """全量覆写消息（压缩后使用）。"""
        self.messages[:] = messages
        if self.storage:
            self.storage.rewrite_messages(self.id, messages)
        logger.debug("Messages rewritten | id={} count={}", self.id[:8], len(messages))

    def save_transcript(self, messages: list) -> str:
        """归档压缩前的完整对话，返回标识符（路径或记录 ID）。"""
        if self.storage:
            return self.storage.save_transcript(self.id, messages)
        return ""

    def to_meta(self) -> dict:
        return {
            "id": self.id,
            "workdir": str(self.workdir),
            "project_root": str(self.project_root),
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


# ─── SessionManager ───────────────────────────────────────────────────────────


class SessionManager:
    """
    Session 的生命周期管理器：创建、查找、列出。
    具体的存储操作由 StorageAdapter 负责。
    """

    def __init__(
        self,
        base_dir: Path = SESSIONS_BASE,
        project_root: Path | None = None,
        storage: StorageAdapter | None = None,
    ):
        # project_root 用于定位 .ccserver/；默认使用 config.PROJECT_DIR
        self.project_root = project_root or PROJECT_DIR
        self.storage = storage or FileStorageAdapter(base_dir)
        self._sessions: dict[str, Session] = {}

    def create(self, session_id: str = None) -> Session:
        sid = session_id or str(uuid.uuid4())
        now = datetime.now(timezone.utc)

        workdir = self.storage.get_workdir(sid)

        # 如果没有 project_root（CCSERVER_PROJECT_DIR 未设置），
        # 为每个 session 创建独立的临时目录
        project_root = self.project_root
        if project_root is None:
            import tempfile
            project_root = Path(tempfile.gettempdir()) / f"ccserver-session-{sid}"
            project_root.mkdir(parents=True, exist_ok=True)
            logger.info(
                "Session using temporary project_root | id={} path={}",
                sid[:8], project_root,
            )

        record = SessionRecord(
            session_id=sid,
            workdir=str(workdir),
            project_root=str(project_root),
            created_at=now,
            updated_at=now,
            messages=[],
        )
        self.storage.create_session(record)

        session = Session(
            id=sid,
            workdir=workdir,
            project_root=project_root,
            storage=self.storage,
            created_at=now,
            updated_at=now,
        )
        self._sessions[sid] = session
        logger.info("Session created | id={} workdir={}", sid[:8], workdir)
        return session

    def create_for_project(
        self,
        project_root: Path,
        session_id: str = None,
    ) -> Session:
        """
        创建以指定目录为 project_root 的 Session。

        与 create() 的区别：project_root 不再固定为 self.project_root，
        而是由调用方传入。Pipeline 节点使用此方法以支持多 agent 目录。
        """
        sid = session_id or str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        workdir = self.storage.get_workdir(sid)

        record = SessionRecord(
            session_id=sid,
            workdir=str(workdir),
            project_root=str(project_root),
            created_at=now,
            updated_at=now,
            messages=[],
        )
        self.storage.create_session(record)

        session = Session(
            id=sid,
            workdir=workdir,
            project_root=project_root,
            storage=self.storage,
            created_at=now,
            updated_at=now,
        )
        self._sessions[sid] = session
        logger.info(
            "Session created | id={} project_root={} workdir={}",
            sid[:8], project_root, workdir,
        )
        return session

    def get(self, session_id: str) -> Optional[Session]:
        if session_id in self._sessions:
            return self._sessions[session_id]
        return self._load(session_id)

    def list_all(self) -> list[dict]:
        return self.storage.list_sessions()

    def _load(self, session_id: str) -> Optional[Session]:
        record = self.storage.load_session(session_id)
        if record is None:
            logger.debug("Session not found | id={}", session_id[:8])
            return None

        session = Session(
            id=session_id,
            workdir=Path(record.workdir),
            project_root=Path(record.project_root),
            storage=self.storage,
            messages=record.messages,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )
        self._sessions[session_id] = session
        logger.info("Session loaded | id={} messages={}", session_id[:8], len(record.messages))
        return session
