"""
tests/test_storage.py — FileStorageAdapter 和 SQLiteStorageAdapter 单元测试

覆盖：
  FileStorageAdapter:
    - create_session(): 创建目录结构 + meta.json
    - load_session(): 读取 meta.json + messages.jsonl；不存在返回 None
    - list_sessions(): 按 updated_at 倒序；空目录返回 []
    - append_message(): 追加到 messages.jsonl
    - rewrite_messages(): 全量覆写
    - save_transcript(): 归档文件创建
    - update_meta(): 更新 updated_at 时间戳

  SQLiteStorageAdapter:
    - 初始化建表
    - create_session() / load_session() 往返
    - load_session() 不存在返回 None
    - list_sessions() 倒序
    - append_message() / load_session() 消息往返
    - rewrite_messages() 软删除 + 插入新消息
    - save_transcript() 返回 transcript:<id>
    - update_meta() 更新时间戳
    - create_conversation() / list_conversations()
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ccserver.storage.base import SessionRecord
from ccserver.storage.file_adapter import FileStorageAdapter
from ccserver.storage.sqlite_adapter import SQLiteStorageAdapter


# ─── 工具函数 ──────────────────────────────────────────────────────────────────


def _make_record(session_id: str = "sess-001") -> SessionRecord:
    now = datetime.now(timezone.utc)
    return SessionRecord(
        session_id=session_id,
        workdir=f"/tmp/{session_id}/workdir",
        project_root=f"/tmp/{session_id}",
        created_at=now,
        updated_at=now,
        messages=[],
    )


def _make_message(role: str = "user", content: str = "hello") -> dict:
    return {"role": role, "content": content}


# ══════════════════════════════════════════════════════════════════════════════
# FileStorageAdapter
# ══════════════════════════════════════════════════════════════════════════════


@pytest.fixture
def file_adapter(tmp_path):
    return FileStorageAdapter(base_dir=tmp_path)


def test_file_create_session_creates_dirs(file_adapter, tmp_path):
    rec = _make_record()
    file_adapter.create_session(rec)
    session_dir = tmp_path / rec.session_id
    assert (session_dir / "workdir").is_dir()
    assert (session_dir / "transcripts").is_dir()
    assert (session_dir / "meta.json").exists()


def test_file_create_session_meta_contents(file_adapter, tmp_path):
    rec = _make_record("abc-123")
    file_adapter.create_session(rec)
    meta = json.loads((tmp_path / "abc-123" / "meta.json").read_text())
    assert meta["workdir"] == rec.workdir
    assert meta["project_root"] == rec.project_root


def test_file_load_session_roundtrip(file_adapter):
    rec = _make_record()
    file_adapter.create_session(rec)
    loaded = file_adapter.load_session(rec.session_id)
    assert loaded is not None
    assert loaded.session_id == rec.session_id
    assert loaded.workdir == rec.workdir
    assert loaded.messages == []


def test_file_load_session_nonexistent_returns_none(file_adapter):
    assert file_adapter.load_session("ghost-999") is None


def test_file_append_message_persists(file_adapter):
    rec = _make_record()
    file_adapter.create_session(rec)
    file_adapter.append_message(rec.session_id, _make_message("user", "hi"))
    file_adapter.append_message(rec.session_id, _make_message("assistant", "hello"))
    loaded = file_adapter.load_session(rec.session_id)
    assert len(loaded.messages) == 2
    assert loaded.messages[0]["role"] == "user"
    assert loaded.messages[1]["role"] == "assistant"


def test_file_rewrite_messages(file_adapter):
    rec = _make_record()
    file_adapter.create_session(rec)
    file_adapter.append_message(rec.session_id, _make_message("user", "original"))
    new_msgs = [_make_message("user", "summary")]
    file_adapter.rewrite_messages(rec.session_id, new_msgs)
    loaded = file_adapter.load_session(rec.session_id)
    assert len(loaded.messages) == 1
    assert loaded.messages[0]["content"] == "summary"


def test_file_save_transcript_creates_file(file_adapter, tmp_path):
    rec = _make_record()
    file_adapter.create_session(rec)
    msgs = [_make_message("user", "msg1"), _make_message("assistant", "msg2")]
    path_str = file_adapter.save_transcript(rec.session_id, msgs)
    path = Path(path_str)
    assert path.exists()
    lines = path.read_text().splitlines()
    assert len(lines) == 2


def test_file_update_meta_changes_timestamp(file_adapter, tmp_path):
    rec = _make_record()
    file_adapter.create_session(rec)
    new_time = datetime(2030, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    file_adapter.update_meta(rec.session_id, new_time)
    meta = json.loads((tmp_path / rec.session_id / "meta.json").read_text())
    assert "2030" in meta["updated_at"]


def test_file_list_sessions_empty_dir(file_adapter, tmp_path):
    result = file_adapter.list_sessions()
    assert result == []


def test_file_list_sessions_returns_sorted(file_adapter):
    # 创建两个 session，updated_at 不同
    r1 = _make_record("sess-001")
    r2 = _make_record("sess-002")
    r2.updated_at = datetime(2030, 6, 1, tzinfo=timezone.utc)
    file_adapter.create_session(r1)
    file_adapter.create_session(r2)
    result = file_adapter.list_sessions()
    # 按 updated_at 倒序，sess-002 应排第一
    assert result[0]["id"] == "sess-002"


def test_file_get_workdir(file_adapter, tmp_path):
    wd = file_adapter.get_workdir("test-session")
    assert str(wd) == str(tmp_path / "test-session" / "workdir")


# ══════════════════════════════════════════════════════════════════════════════
# SQLiteStorageAdapter
# ══════════════════════════════════════════════════════════════════════════════


@pytest.fixture
def sqlite_adapter(tmp_path):
    return SQLiteStorageAdapter(db_path=tmp_path / "test.db")


def test_sqlite_create_session_roundtrip(sqlite_adapter):
    rec = _make_record("sqlite-001")
    sqlite_adapter.create_session(rec)
    loaded = sqlite_adapter.load_session("sqlite-001")
    assert loaded is not None
    assert loaded.session_id == "sqlite-001"
    assert loaded.workdir == rec.workdir
    assert loaded.messages == []


def test_sqlite_load_session_nonexistent_returns_none(sqlite_adapter):
    assert sqlite_adapter.load_session("ghost-session") is None


def test_sqlite_append_and_load_messages(sqlite_adapter):
    rec = _make_record("sqlite-002")
    sqlite_adapter.create_session(rec)
    sqlite_adapter.append_message("sqlite-002", _make_message("user", "hello"))
    sqlite_adapter.append_message("sqlite-002", _make_message("assistant", "hi there"))
    loaded = sqlite_adapter.load_session("sqlite-002")
    assert len(loaded.messages) == 2
    assert loaded.messages[0]["role"] == "user"
    assert loaded.messages[0]["content"] == "hello"


def test_sqlite_rewrite_messages_soft_delete(sqlite_adapter):
    rec = _make_record("sqlite-003")
    sqlite_adapter.create_session(rec)
    sqlite_adapter.append_message("sqlite-003", _make_message("user", "old"))
    # 覆写为摘要消息
    sqlite_adapter.rewrite_messages("sqlite-003", [_make_message("user", "summary")])
    loaded = sqlite_adapter.load_session("sqlite-003")
    # 只有 is_active=1 的消息（摘要）
    assert len(loaded.messages) == 1
    assert loaded.messages[0]["content"] == "summary"


def test_sqlite_rewrite_messages_full_history_preserved(sqlite_adapter):
    """软删除后，get_full_history 仍能看到旧消息（is_active=0）。"""
    rec = _make_record("sqlite-004")
    sqlite_adapter.create_session(rec)
    sqlite_adapter.append_message("sqlite-004", _make_message("user", "original"))
    sqlite_adapter.rewrite_messages("sqlite-004", [_make_message("user", "compact")])
    full = sqlite_adapter.get_full_history("sqlite-004")
    assert len(full) == 2
    inactive = [m for m in full if not m["is_active"]]
    assert len(inactive) == 1
    assert inactive[0]["content"] == "original"


def test_sqlite_save_transcript_returns_id(sqlite_adapter):
    rec = _make_record("sqlite-005")
    sqlite_adapter.create_session(rec)
    ref = sqlite_adapter.save_transcript("sqlite-005", [_make_message("user", "x")])
    assert ref.startswith("transcript:")


def test_sqlite_update_meta(sqlite_adapter):
    rec = _make_record("sqlite-006")
    sqlite_adapter.create_session(rec)
    new_time = datetime(2035, 1, 1, tzinfo=timezone.utc)
    sqlite_adapter.update_meta("sqlite-006", new_time)
    loaded = sqlite_adapter.load_session("sqlite-006")
    assert loaded.updated_at.year == 2035


def test_sqlite_list_sessions_sorted(sqlite_adapter):
    r1 = _make_record("sqlite-sort-a")
    r2 = _make_record("sqlite-sort-b")
    r2.updated_at = datetime(2035, 1, 1, tzinfo=timezone.utc)
    sqlite_adapter.create_session(r1)
    sqlite_adapter.create_session(r2)
    result = sqlite_adapter.list_sessions()
    assert result[0]["id"] == "sqlite-sort-b"


def test_sqlite_create_and_list_conversations(sqlite_adapter):
    rec = _make_record("sqlite-conv-001")
    sqlite_adapter.create_session(rec)
    sqlite_adapter.create_conversation("sqlite-conv-001", "conv-aaa")
    sqlite_adapter.create_conversation("sqlite-conv-001", "conv-bbb")
    convs = sqlite_adapter.list_conversations("sqlite-conv-001")
    assert len(convs) == 2
    ids = {c["conversation_id"] for c in convs}
    assert "conv-aaa" in ids and "conv-bbb" in ids


def test_sqlite_set_conversation_switches_active(sqlite_adapter):
    rec = _make_record("sqlite-switch-001")
    sqlite_adapter.create_session(rec)
    sqlite_adapter.create_conversation("sqlite-switch-001", "conv-x")
    sqlite_adapter.create_conversation("sqlite-switch-001", "conv-y")
    sqlite_adapter.set_conversation("sqlite-switch-001", "conv-x")
    # 当前活跃 conv 是 conv-x
    sqlite_adapter.append_message("sqlite-switch-001", _make_message("user", "in conv-x"))
    # 验证消息关联了正确的 conv
    full = sqlite_adapter.get_full_history("sqlite-switch-001")
    assert full[0]["conversation_id"] == "conv-x"


def test_sqlite_db_file_created(tmp_path):
    db_path = tmp_path / "subdir" / "test.db"
    adapter = SQLiteStorageAdapter(db_path=db_path)
    assert db_path.exists()


def test_sqlite_get_workdir_returns_path(sqlite_adapter):
    wd = sqlite_adapter.get_workdir("any-session")
    assert isinstance(wd, Path)
    assert "any-session" in str(wd)
