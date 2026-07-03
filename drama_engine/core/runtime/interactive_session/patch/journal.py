"""Runtime patch journal.

动态生成内容只写入 journal，不修改原始 DSL。
Generated flow/schedule changes are auditable and rollback-friendly.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class PatchRecord:
    """One stored runtime patch."""

    patch_id: str
    patch_type: str
    payload: dict[str, Any]
    created_at: float
    source: dict[str, Any] = field(default_factory=dict)


class PatchJournal:
    """Append-only patch journal."""

    def __init__(self) -> None:
        """Initialize an empty journal."""
        self._records: list[PatchRecord] = []

    def append(
        self,
        patch_type: str,
        payload: dict[str, Any],
        source: dict[str, Any] | None = None,
    ) -> PatchRecord:
        """Append one patch record."""
        assert patch_type, "patch_type 不能为空"
        assert isinstance(payload, dict), "payload 必须是 dict"
        record = PatchRecord(
            patch_id=str(uuid.uuid4()),
            patch_type=patch_type,
            payload=dict(payload),
            created_at=time.time(),
            source=dict(source or {}),
        )
        self._records.append(record)
        return record

    def all(self) -> list[PatchRecord]:
        """Return all patch records."""
        return list(self._records)

    def by_type(self, patch_type: str) -> list[PatchRecord]:
        """Return records matching one type."""
        return [record for record in self._records if record.patch_type == patch_type]

    def rollback_last(self) -> PatchRecord | None:
        """Remove and return the last patch, if any."""
        if not self._records:
            return None
        return self._records.pop()

    def snapshot(self) -> list[dict[str, Any]]:
        """Return a serializable snapshot."""
        return [
            {
                "patch_id": record.patch_id,
                "patch_type": record.patch_type,
                "payload": dict(record.payload),
                "created_at": record.created_at,
                "source": dict(record.source),
            }
            for record in self._records
        ]
