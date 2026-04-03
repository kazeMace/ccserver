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
