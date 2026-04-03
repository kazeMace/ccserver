"""
storage.file_adapter — 基于本地文件系统的 StorageAdapter 实现。

目录结构：
    {base_dir}/
      {session_id}/
        meta.json
        messages.jsonl      ← append-only，一行一条
        workdir/
        transcripts/
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

    # ── 内部工具 ──────────────────────────────────────────────────────────────

    def _to_meta_dict(self, record: SessionRecord) -> dict:
        return {
            "id": record.session_id,
            "workdir": record.workdir,
            "project_root": record.project_root,
            "created_at": record.created_at.isoformat(),
            "updated_at": record.updated_at.isoformat(),
        }
