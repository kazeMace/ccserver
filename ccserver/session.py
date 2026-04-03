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
from .task_manager import TaskManager
from .skills import SkillLoader
from .agents.loader import AgentLoader
from .hooks.loader import HookLoader
from .commands import CommandLoader
from .mcp import MCPManager
from .settings import ProjectSettings
from .storage import StorageAdapter, SessionRecord, FileStorageAdapter


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

    def __post_init__(self):
        if self._settings is None:
            self._settings = ProjectSettings.from_dirs(self.project_root)
        if self._tasks is None:
            self._tasks = TaskManager()
        if self._skills is None:
            self._skills = SkillLoader.from_workdir(self.project_root, GLOBAL_CONFIG_DIR)
        if self._agents is None:
            self._agents = AgentLoader.from_workdir(self.project_root, GLOBAL_CONFIG_DIR)
        if self._hooks is None:
            self._hooks = HookLoader.from_workdir(self.project_root, GLOBAL_CONFIG_DIR)
        if self._commands is None:
            self._commands = CommandLoader.from_project_root(self.project_root, GLOBAL_CONFIG_DIR)
        if self._mcp is None:
            self._mcp = MCPManager.from_config(
                self.project_root / ".mcp.json",
                project_dir=self.project_root,
                enabled_servers=self._settings.enabled_mcp_servers,
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

        record = SessionRecord(
            session_id=sid,
            workdir=str(workdir),
            project_root=str(self.project_root),
            created_at=now,
            updated_at=now,
            messages=[],
        )
        self.storage.create_session(record)

        session = Session(
            id=sid,
            workdir=workdir,
            project_root=self.project_root,
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
