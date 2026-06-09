"""
storage.interfaces — 按领域拆分的细粒度存储接口（ISP 接口隔离原则）。

背景：
  原 StorageAdapter 是一个含 29 个方法、横跨 6 个领域的"胖接口"，
  导致只关心单一领域的后端（如 MongoStorageAdapter 只实现 session 读写、
  CachedStorageAdapter 只拦截缓存）被迫继承全部方法。

设计：
  将存储能力按领域拆为多个细接口，每个接口只聚焦一件事：
    - SessionStore        会话生命周期 + 消息 IO（核心，唯一全 abstractmethod）
    - ConversationStore   HTTP 对话轮次跟踪
    - TaskStore           Agent 任务存储
    - TeamStore           团队数据存储
    - MailboxStore        团队跨进程 mailbox
    - CronStore           cron 定时任务存储

  各后端可只实现自己支持的接口。StorageAdapter（见 base.py）继承全部细接口，
  作为"全能聚合接口"保留向后兼容：现有依赖 StorageAdapter 的代码无需改动，
  而新代码可针对最小的细接口编程（如只接收 SessionStore）。

  约定：除 SessionStore 外，其余接口的方法均提供 NotImplementedError 默认实现，
  后端按需覆盖即可——这与重构前的行为完全一致。
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


class SessionStore(ABC):
    """会话生命周期 + 消息 IO。这是存储的核心能力，方法全部为抽象。"""

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


class ConversationStore(ABC):
    """HTTP 请求对话轮次跟踪（可选能力，默认未实现）。"""

    def create_conversation(self, session_id: str, conversation_id: str) -> None:
        """注册一次新的 HTTP 请求对话轮次。默认未实现。"""
        raise NotImplementedError(
            f"{self.__class__.__name__} 未实现 create_conversation"
        )


class TaskStore(ABC):
    """Agent 任务存储（可选能力，默认未实现）。"""

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


class TeamStore(ABC):
    """团队数据存储（可选能力，默认未实现）。"""

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


class MailboxStore(ABC):
    """团队跨进程 mailbox 存储（可选能力，默认未实现）。"""

    def append_inbox_message(self, team_name: str, recipient: str, message: dict) -> None:
        """向指定团队的某个收件人追加一条 mailbox 消息。默认未实现。"""
        raise NotImplementedError(f"{self.__class__.__name__} 未实现 append_inbox_message")

    def fetch_inbox_messages(
        self,
        team_name: str,
        recipient: str,
        unread_only: bool = False,
        limit: int = 100,
    ) -> list[dict]:
        """获取指定收件人的 mailbox 消息列表。默认未实现。"""
        raise NotImplementedError(f"{self.__class__.__name__} 未实现 fetch_inbox_messages")

    def mark_inbox_read(self, team_name: str, recipient: str, message_ids: list[str]) -> None:
        """将指定消息标记为已读。默认未实现。"""
        raise NotImplementedError(f"{self.__class__.__name__} 未实现 mark_inbox_read")


class CronStore(ABC):
    """cron 定时任务存储（可选能力，默认未实现）。"""

    def create_cron_task(self, session_id: str, task_data: dict) -> None:
        """创建或覆盖一个 cron 任务。默认未实现。"""
        raise NotImplementedError(f"{self.__class__.__name__} 未实现 create_cron_task")

    def load_cron_task(self, session_id: str, task_id: str) -> dict | None:
        """按 task_id 加载单个 cron 任务，不存在返回 None。默认未实现。"""
        raise NotImplementedError(f"{self.__class__.__name__} 未实现 load_cron_task")

    def update_cron_task(self, session_id: str, task_data: dict) -> None:
        """更新一个 cron 任务（持久化 next_run_at / trigger_count 等）。默认未实现。"""
        raise NotImplementedError(f"{self.__class__.__name__} 未实现 update_cron_task")

    def delete_cron_task(self, session_id: str, task_id: str) -> None:
        """删除一个 cron 任务。默认未实现。"""
        raise NotImplementedError(f"{self.__class__.__name__} 未实现 delete_cron_task")

    def list_cron_tasks(self, session_id: str) -> list[dict]:
        """列出指定 session 的所有 cron 任务。默认未实现。"""
        raise NotImplementedError(f"{self.__class__.__name__} 未实现 list_cron_tasks")

    def get_cron_highwatermark(self, session_id: str) -> int:
        """获取 crontab 目录的自增计数器（下一个可用的纯数字 task_id）。默认未实现。"""
        raise NotImplementedError(f"{self.__class__.__name__} 未实现 get_cron_highwatermark")

    def set_cron_highwatermark(self, session_id: str, value: int) -> None:
        """设置 crontab 目录的自增计数器。默认未实现。"""
        raise NotImplementedError(f"{self.__class__.__name__} 未实现 set_cron_highwatermark")
