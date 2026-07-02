"""
session — Session data model and SessionManager persistence layer.
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from loguru import logger

from .managers.tasks import TaskManager
from .managers.skills import SkillLoader
from .managers.agents import AgentLoader
from .managers.hooks import HookLoader
from .managers.commands import CommandLoader
from .agent_scheduler import AgentScheduler
from .event_bus import EventBus
from .managers.cron import CronScheduler
from .tasks import ShellTaskRegistry, AgentTaskRegistry
from .mcp import MCPManager
from .configuration import CcServerConfig, resolve_session, get_process_config
from .storage import StorageAdapter, SessionRecord, FileStorageAdapter
from .team import TeamRegistry
from .channels.output_target import OutputTarget


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
    _process_config: Any = field(default=None, repr=False)  # 进程级共享底座 CcServerConfig（由 SessionManager 注入）
    _config: Any = field(default=None, repr=False)           # SESSION 作用域解析后的完整 CcServerConfig
    _scheduler: Any = field(default=None, repr=False)
    _cron_scheduler: Any = field(default=None, repr=False)  # CronScheduler，定时任务调度器
    _event_bus: Any = field(default=None, repr=False)    # EventBus（fan-out 广播总线）
    _shell_tasks: Any = field(default=None, repr=False)  # ShellTaskRegistry，后台 shell 任务注册表
    _agent_tasks: Any = field(default=None, repr=False)  # AgentTaskRegistry，后台 Agent 任务注册表
    _team_registry: Any = field(default=None, repr=False)  # TeamRegistry，Agent Team 注册表（可选）
    _root_agent: Any = field(default=None, repr=False)      # 根 Agent 引用，由 AgentFactory.create_root() 设置

    # ── 出站目标列表（新出站架构核心）────────────────────────────────────────────
    # output_targets：当前轮次的出站目标。由 Gateway.dispatch_inbound() 在每次
    #   收到入站消息时组装，轮次结束后清空。
    # default_output_targets：持久化的出站目标，由最后一次 InboundMessage 路由更新。
    #   供 Cron 触发/Background Agent 使用：没有 InboundMessage 时，仍能找到"发给谁"。
    output_targets: list[OutputTarget] = field(default_factory=list)
    default_output_targets: list[OutputTarget] = field(default_factory=list)

    def __post_init__(self):
        if self._config is None:
            # SESSION 作用域解析：进程底座 + 项目 settings.local.json
            process_cfg = self._process_config or get_process_config()
            project_file = self.project_root / ".ccserver" / "settings.local.json"
            self._config = resolve_session(process_cfg, project_file=project_file)
        if self._tasks is None:
            self._tasks = TaskManager(session_id=self.id, adapter=self.storage)
        if self._skills is None:
            self._skills = SkillLoader.from_workdir(self.project_root, self._config.infra.global_config_dir)
        if self._agents is None:
            self._agents = AgentLoader.from_workdir(self.project_root, self._config.infra.global_config_dir)
        if self._hooks is None:
            # 直接读取项目/全局 settings JSON 的原始 dict，构建 HookLoader
            # （hooks 字段不在 CcServerConfig schema 内，按原样透传给 HookLoader）
            self._hooks = self._build_hook_loader()
        if self._commands is None:
            self._commands = CommandLoader.from_project_root(self.project_root, self._config.infra.global_config_dir)
        if self._mcp is None:
            self._mcp = MCPManager.from_config(
                self.project_root / ".mcp.json",
                project_dir=self.project_root,
                enabled_servers=self._config.tools.enabled_mcp_servers,
                session=self,
            )
        if self._scheduler is None:
            self._scheduler = AgentScheduler(self)
        if self._cron_scheduler is None:
            self._cron_scheduler = CronScheduler(self)
            # 从磁盘恢复所有 durable=True 的任务
            self._cron_scheduler.load_durable_tasks()
        if self._event_bus is None:
            overflow_dir = self.workdir / "event_overflow"
            overflow_dir.mkdir(parents=True, exist_ok=True)
            self._event_bus = EventBus(overflow_dir=overflow_dir)
        if self._shell_tasks is None:
            self._shell_tasks = ShellTaskRegistry()
        if self._agent_tasks is None:
            self._agent_tasks = AgentTaskRegistry()
        if self._team_registry is None and self._config.tools.user_agent_team:
            self._team_registry = TeamRegistry(adapter=self.storage)
            logger.debug(
                "Session team registry initialized | id={} teams={}",
                self.id[:8],
                len(self._team_registry.list_teams()),
            )

    @property
    def config(self) -> CcServerConfig:
        """SESSION 作用域解析后的完整配置（新配置系统单一入口）。"""
        return self._config

    def _build_hook_loader(self):
        """
        读取项目/全局 settings JSON 的原始 dict，构建 HookLoader。

        hooks 字段（CC/ccserver 格式 + OpenClaw 控制面板）不在 CcServerConfig
        schema 内，需把原始 settings dict 透传给 HookLoader 解析。
        """
        import json
        from .managers.hooks import HookLoader

        def _read_json(path: Path):
            try:
                if path.exists():
                    return json.loads(path.read_text(encoding="utf-8"))
            except Exception as e:
                logger.error("Failed to read settings for hooks | path={} error={}", path, e)
            return None

        raw_global = _read_json(self._config.infra.global_config_dir / "settings.json")
        raw_project = _read_json(self.project_root / ".ccserver" / "settings.local.json")
        return HookLoader.from_dirs(
            project_root=self.project_root,
            project_settings=raw_project,
            global_settings=raw_global,
        )

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

    def append_message(self, message):
        """追加消息到内存列表，并通过 adapter 持久化。

        内存中保留 UnifiedMessage（S4），仅在写入 storage 的边界归一为 wire dict，
        以保证 file/sqlite/mongo 等所有适配器都拿到 dict（不会因 UnifiedMessage 下标取值崩溃）。
        """
        from ccserver.messages import unified_message_to_wire
        self.messages.append(message)
        self.updated_at = datetime.now(timezone.utc)
        if self.storage:
            self.storage.append_message(self.id, unified_message_to_wire(message))
            self.storage.update_meta(self.id, self.updated_at)

    def persist_message(self, message):
        """只将消息写入磁盘，不追加到内存列表（内存已由调用方处理）。"""
        from ccserver.messages import unified_message_to_wire
        self.updated_at = datetime.now(timezone.utc)
        if self.storage:
            self.storage.append_message(self.id, unified_message_to_wire(message))
            self.storage.update_meta(self.id, self.updated_at)

    def rewrite_messages(self, messages: list):
        """全量覆写消息（压缩后使用）。

        内存保留 UnifiedMessage，写盘前逐条归一为 wire dict。
        """
        from ccserver.messages import unified_message_to_wire
        self.messages[:] = messages
        if self.storage:
            self.storage.rewrite_messages(self.id, [unified_message_to_wire(m) for m in messages])
        logger.debug("Messages rewritten | id={} count={}", self.id[:8], len(messages))

    def save_transcript(self, messages: list) -> str:
        """归档压缩前的完整对话，返回标识符（路径或记录 ID）。"""
        from ccserver.messages import unified_message_to_wire
        if self.storage:
            return self.storage.save_transcript(self.id, [unified_message_to_wire(m) for m in messages])
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
        base_dir: Path | None = None,
        project_root: Path | None = None,
        storage: StorageAdapter | None = None,
        process_config: CcServerConfig | None = None,
    ):
        # 进程级共享底座：解析一次、跨 Session 共享（drama/graph 多 agent 复用）
        self.process_config = process_config or get_process_config()
        # project_root 用于定位 .ccserver/；默认使用 config.infra.project_dir
        self.project_root = project_root or self.process_config.infra.project_dir
        # sessions 存储根目录默认取 config.infra.sessions_base
        resolved_base = base_dir or self.process_config.infra.sessions_base
        self.storage = storage or FileStorageAdapter(resolved_base)
        self._sessions: dict[str, Session] = {}

    def create(self, session_id: str = None) -> Session:
        sid = session_id or str(uuid.uuid4())
        now = datetime.now(timezone.utc)

        workdir = self.storage.get_workdir(sid)

        # 如果没有 project_root（CCSERVER_PROJECT_DIR 未设置），
        # 为每个 session 创建独立的临时目录
        project_root = self.project_root
        if project_root is None:
            project_root = self.process_config.infra.temp_dir / f"ccserver-session-{sid}"
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
            _process_config=self.process_config,
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
            _process_config=self.process_config,
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

    def list_active_sessions(self) -> list["Session"]:
        """返回内存中所有活跃的 Session 对象列表（供 monitor 等内部组件遍历）。"""
        return list(self._sessions.values())

    def _load(self, session_id: str) -> Optional[Session]:
        record = self.storage.load_session(session_id)
        if record is None:
            logger.debug("Session not found | id={}", session_id[:8])
            return None

        # 存储侧返回的是 dict 列表，在加载边界统一转为 UnifiedMessage
        from ccserver.messages import UnifiedMessage
        loaded_messages = [UnifiedMessage.from_dict(m) for m in record.messages]
        session = Session(
            id=session_id,
            workdir=Path(record.workdir),
            project_root=Path(record.project_root),
            storage=self.storage,
            messages=loaded_messages,
            created_at=record.created_at,
            updated_at=record.updated_at,
            _process_config=self.process_config,
        )
        self._sessions[session_id] = session
        logger.info("Session loaded | id={} messages={}", session_id[:8], len(record.messages))
        return session
