"""
storage.file_adapter — 基于本地文件系统的 StorageAdapter 实现。

目录结构：
    {base_dir}/
      {session_id}/
        meta.json
        messages.jsonl      ← append-only，一行一条
        workdir/
        transcripts/
        tasks/              ← 新增：任务存储
          1.json
          2.json
          .highwatermark
"""

import json
import time
from datetime import datetime
from pathlib import Path

from loguru import logger

from .base import StorageAdapter, SessionRecord


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
        if msg_path.exists():
            for line in msg_path.read_text().splitlines():
                if line.strip():
                    messages.append(json.loads(line))

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
        with open(msg_path, "a") as f:
            f.write(json.dumps(message, default=str) + "\n")

    def rewrite_messages(self, session_id: str, messages: list) -> None:
        msg_path = self._session_dir(session_id) / "messages.jsonl"
        with open(msg_path, "w") as f:
            for msg in messages:
                f.write(json.dumps(msg, default=str) + "\n")
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
        with open(path, "a") as f:
            f.write(json.dumps(message, default=str) + "\n")
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
        with open(path, "r") as f:
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
        """将指定 ID 的消息标记为已读（重写整个 inbox 文件）。"""
        path = self._inbox_file(team_name, recipient)
        if not path.exists():
            return

        target_ids = set(message_ids)
        lines = []
        updated = 0
        with open(path, "r") as f:
            for line in f:
                raw = line.strip()
                if not raw:
                    lines.append(line)
                    continue
                msg = json.loads(raw)
                if msg.get("id") in target_ids and not msg.get("read"):
                    msg["read"] = True
                    updated += 1
                lines.append(json.dumps(msg, default=str) + "\n")

        with open(path, "w") as f:
            f.writelines(lines)

        logger.debug(
            "FileAdapter: inbox marked read | team={} recipient={} updated={}",
            team_name,
            recipient,
            updated,
        )
