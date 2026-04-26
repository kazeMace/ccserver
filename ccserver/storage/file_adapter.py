"""
storage.file_adapter — 基于本地文件系统的 StorageAdapter 实现。

目录结构：
    {base_dir}/
      {session_id}/
        meta.json
        messages.jsonl      ← append-only，一行一条
        workdir/
        transcripts/
        tasks/              ← 任务存储
          1.json
          2.json
          .highwatermark
        crontab/            ← cron 任务存储
          ct3f2a1c0.json
          ...
          .highwatermark
"""

import json
import time
from datetime import datetime
from pathlib import Path

from loguru import logger

from .base import StorageAdapter, SessionRecord, _json_default


class FileStorageAdapter(StorageAdapter):

    def __init__(self, base_dir: Path):
        self.base_dir = base_dir

    def _session_dir(self, session_id: str) -> Path:
        return self.base_dir / session_id

    def get_workdir(self, session_id: str) -> Path:
        return self._session_dir(session_id) / "workdir"

    # ── session 生命周期 ───────────────────────────────────────────────────────

    def create_session(self, record: SessionRecord) -> None:
        session_dir = self._session_dir(record.session_id)
        (session_dir / "workdir").mkdir(parents=True, exist_ok=True)
        (session_dir / "transcripts").mkdir(exist_ok=True)
        (session_dir / "meta.json").write_text(
            json.dumps(self._to_meta_dict(record), indent=2)
        )
        logger.debug("FileAdapter: session created | id={}", record.session_id[:8])

    def load_session(self, session_id: str) -> SessionRecord | None:
        session_dir = self._session_dir(session_id)
        meta_path = session_dir / "meta.json"
        if not meta_path.exists():
            return None

        meta = json.loads(meta_path.read_text())
        messages = []
        msg_path = session_dir / "messages.jsonl"
        # 限制单会话最大读取消息条数，防止大会话 OOM
        MAX_MESSAGES_PER_SESSION = 10_000
        # 直接用 try/except 打开文件，避免 TOCTOU 竞态（检查后文件可能消失）
        try:
            with open(msg_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            messages.append(json.loads(line))
                        except json.JSONDecodeError:
                            logger.warning(
                                "FileStorageAdapter: skip corrupted message line | session_id={}",
                                session_id[:8],
                            )
                    if len(messages) >= MAX_MESSAGES_PER_SESSION:
                        logger.warning(
                            "FileStorageAdapter: message count exceeds limit {} | "
                            "session_id={} truncating",
                            MAX_MESSAGES_PER_SESSION,
                            session_id[:8],
                        )
                        break
        except FileNotFoundError:
            pass  # 消息文件不存在，返回空列表

        return SessionRecord(
            session_id=session_id,
            workdir=meta["workdir"],
            project_root=meta["project_root"],
            created_at=datetime.fromisoformat(meta["created_at"]),
            updated_at=datetime.fromisoformat(meta["updated_at"]),
            messages=messages,
        )

    def list_sessions(self) -> list[dict]:
        if not self.base_dir.exists():
            return []
        result = []
        for d in self.base_dir.iterdir():
            meta_path = d / "meta.json"
            if meta_path.exists():
                result.append(json.loads(meta_path.read_text()))
        return sorted(result, key=lambda x: x["updated_at"], reverse=True)

    # ── 消息 IO ───────────────────────────────────────────────────────────────

    def append_message(self, session_id: str, message: dict) -> None:
        msg_path = self._session_dir(session_id) / "messages.jsonl"
        with open(msg_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(message, default=_json_default) + "\n")

    def rewrite_messages(self, session_id: str, messages: list) -> None:
        msg_path = self._session_dir(session_id) / "messages.jsonl"
        with open(msg_path, "w", encoding="utf-8") as f:
            for msg in messages:
                f.write(json.dumps(msg, default=_json_default) + "\n")
        logger.debug("FileAdapter: messages rewritten | id={} count={}", session_id[:8], len(messages))

    def save_transcript(self, session_id: str, messages: list) -> str:
        ts = int(time.time())
        path = self._session_dir(session_id) / "transcripts" / f"transcript_{ts}.jsonl"
        with open(path, "w") as f:
            for msg in messages:
                f.write(json.dumps(msg, default=str) + "\n")
        return str(path)

    def update_meta(self, session_id: str, updated_at: datetime) -> None:
        meta_path = self._session_dir(session_id) / "meta.json"
        meta = json.loads(meta_path.read_text())
        meta["updated_at"] = updated_at.isoformat()
        meta_path.write_text(json.dumps(meta, indent=2))

    # ── Task 存储 ─────────────────────────────────────────────────────────────

    def _tasks_dir(self, session_id: str) -> Path:
        """返回任务目录路径。"""
        return self._session_dir(session_id) / "tasks"

    def _task_file(self, session_id: str, task_id: str) -> Path:
        return self._tasks_dir(session_id) / f"{task_id}.json"

    def create_task(self, session_id: str, task_data: dict) -> None:
        """创建任务文件。"""
        path = self._task_file(session_id, task_data["id"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(task_data, indent=2, ensure_ascii=False))

    def load_task(self, session_id: str, task_id: str) -> dict | None:
        """加载任务。"""
        path = self._task_file(session_id, task_id)
        if not path.exists():
            return None
        return json.loads(path.read_text())

    def update_task(self, session_id: str, task_data: dict) -> None:
        """更新任务（直接覆盖文件）。"""
        self.create_task(session_id, task_data)

    def list_tasks(self, session_id: str) -> list[dict]:
        """列出所有任务。"""
        tasks_dir = self._tasks_dir(session_id)
        if not tasks_dir.exists():
            return []
        tasks = []
        for f in tasks_dir.glob("*.json"):
            data = json.loads(f.read_text())
            if data:
                tasks.append(data)
        # 按 ID 从小到大排序（ID 为自增整数字符串）
        return sorted(tasks, key=lambda t: int(t.get("id", "0")))

    def get_task_counter(self, session_id: str) -> int:
        """读取任务自增计数器。"""
        hw = self._tasks_dir(session_id) / ".highwatermark"
        if hw.exists():
            return int(hw.read_text())
        return 0

    def set_task_counter(self, session_id: str, value: int) -> None:
        """设置任务自增计数器。"""
        hw = self._tasks_dir(session_id)
        hw.mkdir(parents=True, exist_ok=True)
        (hw / ".highwatermark").write_text(str(value))

    # ── Cron 任务存储 ──────────────────────────────────────────────────────────

    def _crontab_dir(self, session_id: str) -> Path:
        """返回 crontab 目录路径。"""
        return self._session_dir(session_id) / "crontab"

    def _cron_file(self, session_id: str, task_id: str) -> Path:
        """返回单个 cron 任务文件路径。"""
        return self._crontab_dir(session_id) / f"{task_id}.json"

    def create_cron_task(self, session_id: str, task_data: dict) -> None:
        """
        创建或覆盖一个 cron 任务文件。

        Args:
            session_id: 所属 session ID
            task_data:  CronTask.to_dict() 序列化后的字典，必须包含 task_id
        """
        path = self._cron_file(session_id, task_data["task_id"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(task_data, indent=2, ensure_ascii=False))
        logger.debug(
            "Cron task saved | session_id={} task_id={}",
            session_id[:8], task_data["task_id"],
        )

    def load_cron_task(self, session_id: str, task_id: str) -> dict | None:
        """
        按 task_id 加载单个 cron 任务。

        Returns:
            任务字典，不存在则返回 None。
        """
        path = self._cron_file(session_id, task_id)
        if not path.exists():
            return None
        return json.loads(path.read_text())

    def update_cron_task(self, session_id: str, task_data: dict) -> None:
        """
        更新一个 cron 任务（直接覆盖文件）。
        """
        self.create_cron_task(session_id, task_data)

    def delete_cron_task(self, session_id: str, task_id: str) -> None:
        """
        删除一个 cron 任务文件。

        Args:
            session_id: 所属 session ID
            task_id:    要删除的任务 ID
        """
        path = self._cron_file(session_id, task_id)
        if path.exists():
            path.unlink()
            logger.debug("Cron task deleted | session_id={} task_id={}", session_id[:8], task_id)

    def list_cron_tasks(self, session_id: str) -> list[dict]:
        """
        列出指定 session 的所有 cron 任务。

        Returns:
            CronTask.to_dict() 字典列表，按 task_id 排序。
        """
        crontab_dir = self._crontab_dir(session_id)
        if not crontab_dir.exists():
            return []
        tasks = []
        for f in crontab_dir.glob("*.json"):
            data = json.loads(f.read_text())
            if data:
                tasks.append(data)
        return sorted(tasks, key=lambda t: t.get("task_id", ""))

    def get_cron_highwatermark(self, session_id: str) -> int:
        """
        读取 crontab 自增计数器。
        """
        hw = self._crontab_dir(session_id) / ".highwatermark"
        if hw.exists():
            return int(hw.read_text())
        return 0

    def set_cron_highwatermark(self, session_id: str, value: int) -> None:
        """
        设置 crontab 自增计数器。
        """
        d = self._crontab_dir(session_id)
        d.mkdir(parents=True, exist_ok=True)
        (d / ".highwatermark").write_text(str(value))

    # ── 内部工具 ──────────────────────────────────────────────────────────────

    def _to_meta_dict(self, record: SessionRecord) -> dict:
        return {
            "id": record.session_id,
            "workdir": record.workdir,
            "project_root": record.project_root,
            "created_at": record.created_at.isoformat(),
            "updated_at": record.updated_at.isoformat(),
        }

    # ── Team 存储 ──────────────────────────────────────────────────────────────

    def _teams_dir(self) -> Path:
        """返回团队根目录路径。"""
        return self.base_dir / "teams"

    def _team_dir(self, team_name: str) -> Path:
        """返回单个团队的目录路径。"""
        return self._teams_dir() / team_name

    def save_team(self, team_data: dict) -> None:
        """保存团队数据到 team.json。"""
        t_dir = self._team_dir(team_data["name"])
        t_dir.mkdir(parents=True, exist_ok=True)
        (t_dir / "team.json").write_text(
            json.dumps(team_data, indent=2, ensure_ascii=False)
        )
        logger.debug("FileAdapter: team saved | name={}", team_data["name"])

    def load_team(self, team_name: str) -> dict | None:
        """加载团队数据。"""
        path = self._team_dir(team_name) / "team.json"
        if not path.exists():
            return None
        return json.loads(path.read_text())

    def delete_team(self, team_name: str) -> None:
        """删除团队目录。"""
        import shutil

        t_dir = self._team_dir(team_name)
        if t_dir.exists():
            shutil.rmtree(t_dir)
            logger.debug("FileAdapter: team deleted | name={}", team_name)

    def list_teams(self) -> list[dict]:
        """列出所有团队数据。"""
        teams_dir = self._teams_dir()
        if not teams_dir.exists():
            return []
        teams = []
        for d in teams_dir.iterdir():
            team_file = d / "team.json"
            if team_file.exists():
                teams.append(json.loads(team_file.read_text()))
        # 按团队名称排序，保证列表稳定
        return sorted(teams, key=lambda t: t.get("name", ""))

    # ── Mailbox 存储 ───────────────────────────────────────────────────────────

    def _inbox_file(self, team_name: str, recipient: str) -> Path:
        """返回指定收件人的 inbox 文件路径。"""
        return self._team_dir(team_name) / "inboxes" / f"{recipient}.jsonl"

    def append_inbox_message(self, team_name: str, recipient: str, message: dict) -> None:
        """向 inbox 文件追加一条消息。"""
        path = self._inbox_file(team_name, recipient)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(message, default=_json_default) + "\n")
        logger.debug(
            "FileAdapter: inbox appended | team={} recipient={} msg_id={}",
            team_name,
            recipient,
            message.get("id", "?"),
        )

    def fetch_inbox_messages(
        self,
        team_name: str,
        recipient: str,
        unread_only: bool = False,
        limit: int = 100,
    ) -> list[dict]:
        """读取 inbox 消息列表，支持未读筛选和数量限制。"""
        path = self._inbox_file(team_name, recipient)
        if not path.exists():
            return []

        messages = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                msg = json.loads(line)
                if unread_only and msg.get("read"):
                    continue
                messages.append(msg)

        # 返回最新的 limit 条
        if limit > 0 and len(messages) > limit:
            messages = messages[-limit:]
        return messages

    def mark_inbox_read(self, team_name: str, recipient: str, message_ids: list[str]) -> None:
        """将指定 ID 的消息标记为已读（流式重写 inbox 文件，避免大会话 OOM）。"""
        path = self._inbox_file(team_name, recipient)
        # 直接用 try/except 打开，避免 TOCTOU 竞态
        try:
            with open(path, "r", encoding="utf-8") as _:
                pass
        except FileNotFoundError:
            return

        target_ids = set(message_ids)
        updated = 0
        temp_path = path.with_suffix(".tmp")

        # 流式读取 + 写入临时文件，内存占用 O(1)（只缓存当前行）
        with open(path, "r", encoding="utf-8") as src, open(temp_path, "w", encoding="utf-8") as dst:
            for line in src:
                raw = line.strip()
                if not raw:
                    dst.write(line)
                    continue
                msg = json.loads(raw)
                if msg.get("id") in target_ids and not msg.get("read"):
                    msg["read"] = True
                    updated += 1
                dst.write(json.dumps(msg, default=_json_default) + "\n")

        # 原子替换：保证写操作完整性，异常时原文件不受影响
        temp_path.replace(path)

        logger.debug(
            "FileAdapter: inbox marked read | team={} recipient={} updated={}",
            team_name,
            recipient,
            updated,
        )
