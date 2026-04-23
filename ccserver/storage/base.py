"""
storage.base — StorageAdapter 抽象接口。

所有存储后端（本地文件、数据库等）都必须实现此接口。
SessionManager 和 Session 只依赖此接口，不感知具体实现。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass
class SessionRecord:
    """create_session / load_session 之间传递的纯数据结构。"""
    session_id: str
    workdir: str
    project_root: str
    created_at: datetime
    updated_at: datetime
    messages: list


class StorageAdapter(ABC):

    # ── session 生命周期 ───────────────────────────────────────────────────────

    @abstractmethod
    def get_workdir(self, session_id: str) -> Path:
        """返回该 session 的工作目录路径（由 adapter 自行决定位置）。"""

    @abstractmethod
    def create_session(self, record: SessionRecord) -> None:
        """持久化一个新建的 session（meta + 空消息列表）。"""

    @abstractmethod
    def load_session(self, session_id: str) -> SessionRecord | None:
        """按 id 加载 session，不存在返回 None。"""

    @abstractmethod
    def list_sessions(self) -> list[dict]:
        """返回所有 session 的 meta 列表，按 updated_at 倒序。"""

    # ── 消息 IO ───────────────────────────────────────────────────────────────

    @abstractmethod
    def append_message(self, session_id: str, message: dict) -> None:
        """追加单条消息。"""

    @abstractmethod
    def rewrite_messages(self, session_id: str, messages: list) -> None:
        """全量覆写消息列表（压缩后使用）。"""

    @abstractmethod
    def save_transcript(self, session_id: str, messages: list) -> str:
        """归档压缩前的完整对话，返回标识符（文件路径或记录 ID）。"""

    @abstractmethod
    def update_meta(self, session_id: str, updated_at: datetime) -> None:
        """更新 session 的 updated_at 时间戳。"""

    # ── conversation 跟踪（可选，不支持的 adapter 保持默认空实现）──────────────

    def create_conversation(self, session_id: str, conversation_id: str) -> None:
        """注册一次新的 HTTP 请求对话轮次。不支持的 adapter 忽略此调用。"""

    # ── Task 存储 ─────────────────────────────────────────────────────────────

    def create_task(self, session_id: str, task_data: dict) -> None:
        """保存任务。默认未实现，由具体 adapter 覆盖。"""
        raise NotImplementedError(f"{self.__class__.__name__} 未实现 create_task")

    def load_task(self, session_id: str, task_id: str) -> dict | None:
        """加载任务。默认未实现。"""
        raise NotImplementedError(f"{self.__class__.__name__} 未实现 load_task")

    def update_task(self, session_id: str, task_data: dict) -> None:
        """更新任务。默认未实现。"""
        raise NotImplementedError(f"{self.__class__.__name__} 未实现 update_task")

    def list_tasks(self, session_id: str) -> list[dict]:
        """列出所有任务。默认未实现。"""
        raise NotImplementedError(f"{self.__class__.__name__} 未实现 list_tasks")

    def get_task_counter(self, session_id: str) -> int:
        """获取任务自增计数器。默认未实现。"""
        raise NotImplementedError(f"{self.__class__.__name__} 未实现 get_task_counter")

    def set_task_counter(self, session_id: str, value: int) -> None:
        """设置任务自增计数器。默认未实现。"""
        raise NotImplementedError(f"{self.__class__.__name__} 未实现 set_task_counter")

    # ── Team 存储 ──────────────────────────────────────────────────────────────

    def save_team(self, team_data: dict) -> None:
        """保存或更新团队数据。默认未实现。"""
        raise NotImplementedError(f"{self.__class__.__name__} 未实现 save_team")

    def load_team(self, team_name: str) -> dict | None:
        """加载团队数据。默认未实现。"""
        raise NotImplementedError(f"{self.__class__.__name__} 未实现 load_team")

    def delete_team(self, team_name: str) -> None:
        """删除团队数据。默认未实现。"""
        raise NotImplementedError(f"{self.__class__.__name__} 未实现 delete_team")

    def list_teams(self) -> list[dict]:
        """列出所有团队数据。默认未实现。"""
        raise NotImplementedError(f"{self.__class__.__name__} 未实现 list_teams")

    # ── Mailbox 存储（Agent Team 跨进程通信）────────────────────────────────────

    def append_inbox_message(self, team_name: str, recipient: str, message: dict) -> None:
        """
        向指定团队的某个收件人追加一条 mailbox 消息。
        默认未实现。
        """
        raise NotImplementedError(f"{self.__class__.__name__} 未实现 append_inbox_message")

    def fetch_inbox_messages(
        self,
        team_name: str,
        recipient: str,
        unread_only: bool = False,
        limit: int = 100,
    ) -> list[dict]:
        """
        获取指定收件人的 mailbox 消息列表。
        默认未实现。
        """
        raise NotImplementedError(f"{self.__class__.__name__} 未实现 fetch_inbox_messages")

    def mark_inbox_read(self, team_name: str, recipient: str, message_ids: list[str]) -> None:
        """
        将指定消息标记为已读。
        默认未实现。
        """
        raise NotImplementedError(f"{self.__class__.__name__} 未实现 mark_inbox_read")
