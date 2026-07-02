"""Script repository for Drama Engine admin service.

剧本仓库负责管理管理端上传的草稿剧本，以及暴露内置 approved 剧本。
It does not run games and does not modify ccserver core Agent code.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from drama_engine.application.script_library import SCRIPT_LIBRARY_ROOT, iter_builtin_script_paths

logger = logging.getLogger(__name__)

SCRIPT_STATUS_DRAFT = "draft"
SCRIPT_STATUS_VALID = "valid"
SCRIPT_STATUS_APPROVED = "approved"
SCRIPT_STATUS_ARCHIVED = "archived"
_VALID_STATUSES = {SCRIPT_STATUS_DRAFT, SCRIPT_STATUS_VALID, SCRIPT_STATUS_APPROVED, SCRIPT_STATUS_ARCHIVED}


@dataclass(slots=True)
class ScriptRecord:
    """Metadata for one script.

    path 使用绝对路径，方便不同服务入口稳定读取。
    """

    script_id: str
    name: str
    path: str
    status: str = SCRIPT_STATUS_DRAFT
    description: str = ""
    source: str = "uploaded"
    created_at: str = ""
    updated_at: str = ""
    last_validation_summary: dict[str, int] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        assert self.script_id, "script_id 不能为空"
        assert self.name, "name 不能为空"
        assert self.status in _VALID_STATUSES, f"无效状态: {self.status}"
        assert self.path, "path 不能为空"

    def to_dict(self) -> dict[str, Any]:
        """Return JSON-friendly metadata."""
        return asdict(self)


class ScriptRepository:
    """Manage built-in and uploaded scripts for the admin dev console."""

    def __init__(self, data_root: str | Path | None = None, builtin_root: str | Path | None = None) -> None:
        package_root = Path(__file__).resolve().parents[1]
        if data_root is None:
            data_root = package_root / ".runtime" / "admin_scripts"
        if builtin_root is None:
            builtin_root = SCRIPT_LIBRARY_ROOT
        self.data_root = Path(data_root)
        self.builtin_root = Path(builtin_root)
        self.drafts_dir = self.data_root / "drafts"
        self.approved_dir = self.data_root / "approved"
        self.catalog_path = self.data_root / "catalog.json"
        self._ensure_dirs()

    def _ensure_dirs(self) -> None:
        """Create repository directories if needed."""
        self.drafts_dir.mkdir(parents=True, exist_ok=True)
        self.approved_dir.mkdir(parents=True, exist_ok=True)
        if not self.catalog_path.exists():
            self._write_catalog({"scripts": []})

    def list_scripts(self, include_builtin: bool = True) -> list[ScriptRecord]:
        """List uploaded scripts plus optional built-in scripts."""
        records = self._load_uploaded_records()
        if include_builtin:
            records = self._builtin_records() + records
        return sorted(records, key=lambda item: (item.source != "builtin", item.name, item.script_id))

    def get_script(self, script_id: str) -> ScriptRecord:
        """Return script metadata by id."""
        assert script_id, "script_id 不能为空"
        for record in self.list_scripts(include_builtin=True):
            if record.script_id == script_id:
                return record
        raise KeyError(f"script not found: {script_id}")

    def read_script_text(self, script_id: str) -> str:
        """Read script YAML text."""
        record = self.get_script(script_id)
        return Path(record.path).read_text(encoding="utf-8")

    def create_script_from_text(self, name: str, content: str, description: str = "") -> ScriptRecord:
        """Create a draft script from raw YAML text."""
        assert name.strip(), "name 不能为空"
        assert isinstance(content, str) and content.strip(), "content 不能为空"
        now = _now_iso()
        slug = _slugify(name)
        script_id = f"draft_{slug}_{uuid4().hex[:8]}"
        path = self.drafts_dir / f"{script_id}.yaml"
        path.write_text(content, encoding="utf-8")
        record = ScriptRecord(
            script_id=script_id,
            name=name.strip(),
            description=description.strip(),
            path=str(path),
            status=SCRIPT_STATUS_DRAFT,
            source="uploaded",
            created_at=now,
            updated_at=now,
        )
        records = self._load_uploaded_records()
        records.append(record)
        self._save_uploaded_records(records)
        logger.info("[ScriptRepository] created draft script: %s", script_id)
        return record

    def create_script_from_file(self, name: str, source_path: str | Path, description: str = "") -> ScriptRecord:
        """Create a draft script by copying an uploaded file."""
        path = Path(source_path)
        assert path.exists(), f"source file not found: {path}"
        return self.create_script_from_text(name=name, content=path.read_text(encoding="utf-8"), description=description)

    def update_script_text(self, script_id: str, content: str) -> ScriptRecord:
        """Update an uploaded draft/valid script.

        Built-in scripts are read-only to keep source presets safe.
        """
        assert content.strip(), "content 不能为空"
        record = self.get_script(script_id)
        if record.source == "builtin":
            raise PermissionError("内置剧本是只读的，请先复制为草稿再编辑。")
        record.status = SCRIPT_STATUS_DRAFT
        record.updated_at = _now_iso()
        Path(record.path).write_text(content, encoding="utf-8")
        self._replace_record(record)
        logger.info("[ScriptRepository] updated script: %s", script_id)
        return record

    def update_validation_summary(self, script_id: str, summary: dict[str, int]) -> ScriptRecord:
        """Persist latest validation summary for uploaded scripts."""
        record = self.get_script(script_id)
        if record.source == "builtin":
            record.last_validation_summary = summary
            return record
        record.last_validation_summary = dict(summary)
        if summary.get("fatal", 0) == 0 and summary.get("error", 0) == 0:
            record.status = SCRIPT_STATUS_VALID if record.status == SCRIPT_STATUS_DRAFT else record.status
        else:
            record.status = SCRIPT_STATUS_DRAFT
        record.updated_at = _now_iso()
        self._replace_record(record)
        return record

    def promote(self, script_id: str, force: bool = False) -> ScriptRecord:
        """Promote an uploaded script to approved status.

        force 只允许带 warning 发布；fatal/error 应由调用方先校验并阻止。
        """
        record = self.get_script(script_id)
        if record.source == "builtin":
            return record
        if record.last_validation_summary.get("fatal", 0) or record.last_validation_summary.get("error", 0):
            raise ValueError("存在 fatal/error，不能发布。")
        if record.last_validation_summary.get("warning", 0) and not force:
            raise ValueError("存在 warning，需要 force=true 才能发布。")
        src = Path(record.path)
        dst = self.approved_dir / src.name
        if src != dst:
            shutil.copyfile(src, dst)
            record.path = str(dst)
        record.status = SCRIPT_STATUS_APPROVED
        record.updated_at = _now_iso()
        self._replace_record(record)
        logger.info("[ScriptRepository] promoted script: %s", script_id)
        return record

    def delete_script(self, script_id: str) -> None:
        """Delete an uploaded script. Built-in scripts cannot be deleted."""
        record = self.get_script(script_id)
        if record.source == "builtin":
            raise PermissionError("内置剧本不能删除。")
        records = [item for item in self._load_uploaded_records() if item.script_id != script_id]
        path = Path(record.path)
        if path.exists():
            path.unlink()
        self._save_uploaded_records(records)
        logger.info("[ScriptRepository] deleted script: %s", script_id)

    def _builtin_records(self) -> list[ScriptRecord]:
        """Expose scripts shipped with drama_engine/scripts as approved read-only records."""
        records: list[ScriptRecord] = []
        for path in iter_builtin_script_paths(self.builtin_root):
            script_id = f"builtin_{path.stem}"
            records.append(ScriptRecord(
                script_id=script_id,
                name=path.stem.replace("_", " "),
                description="内置剧本 / built-in script",
                path=str(path),
                status=SCRIPT_STATUS_APPROVED,
                source="builtin",
                created_at="",
                updated_at="",
            ))
        return records

    def _load_uploaded_records(self) -> list[ScriptRecord]:
        data = self._read_catalog()
        result: list[ScriptRecord] = []
        for item in data.get("scripts", []):
            try:
                result.append(ScriptRecord(**item))
            except TypeError as exc:
                logger.warning("[ScriptRepository] skip invalid catalog record: %s", exc)
        return result

    def _save_uploaded_records(self, records: list[ScriptRecord]) -> None:
        self._write_catalog({"scripts": [record.to_dict() for record in records]})

    def _replace_record(self, record: ScriptRecord) -> None:
        records = self._load_uploaded_records()
        replaced = False
        for index, item in enumerate(records):
            if item.script_id == record.script_id:
                records[index] = record
                replaced = True
                break
        if not replaced:
            records.append(record)
        self._save_uploaded_records(records)

    def _read_catalog(self) -> dict[str, Any]:
        try:
            return json.loads(self.catalog_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return {"scripts": []}

    def _write_catalog(self, data: dict[str, Any]) -> None:
        self.data_root.mkdir(parents=True, exist_ok=True)
        tmp = self.catalog_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.catalog_path)


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _slugify(value: str) -> str:
    text = value.strip().lower()
    text = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "_", text)
    text = text.strip("_")
    return text or "script"
