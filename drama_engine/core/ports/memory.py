"""Runtime memory ports and backends."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

@runtime_checkable
class RuntimeMemoryBackend(Protocol):
    """Cross-session long-term memory backend protocol."""

    def append(self, namespace: str, event: dict[str, Any]) -> None:
        """Persist one long-term memory event."""

    def query(self, namespace: str, limit: int | None = None) -> list[dict[str, Any]]:
        """Return long-term memory events for one namespace."""


class NullRuntimeMemoryBackend:
    """No-op long-term memory backend used when no external store is configured."""

    def append(self, namespace: str, event: dict[str, Any]) -> None:
        """Ignore one long-term memory event."""
        assert namespace, "memory namespace 不能为空"
        assert isinstance(event, dict), "memory event 必须是 dict"

    def query(self, namespace: str, limit: int | None = None) -> list[dict[str, Any]]:
        """Return no external memories."""
        assert namespace, "memory namespace 不能为空"
        if limit == 0:
            return []
        return []


class InMemoryRuntimeMemoryBackend:
    """Process-local long-term memory backend for tests and lightweight deployments."""

    def __init__(self) -> None:
        self._events: dict[str, list[dict[str, Any]]] = {}

    def append(self, namespace: str, event: dict[str, Any]) -> None:
        """Persist one event under a namespace."""
        assert namespace, "memory namespace 不能为空"
        assert isinstance(event, dict), "memory event 必须是 dict"
        self._events.setdefault(namespace, []).append(dict(event))

    def query(self, namespace: str, limit: int | None = None) -> list[dict[str, Any]]:
        """Return events from one namespace."""
        assert namespace, "memory namespace 不能为空"
        events = self._events.get(namespace, [])
        if limit == 0:
            return []
        selected = events[-limit:] if limit is not None else events
        return [dict(event) for event in selected]


class JsonlRuntimeMemoryBackend:
    """JSONL-backed long-term memory backend.

    Each line is one record with ``namespace`` and ``event`` fields. The backend
    keeps the RuntimeMemoryBackend protocol small: append events and query by
    namespace. More advanced database or vector-memory adapters can implement
    the same port without changing runners.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        assert str(self.path), "memory backend path 不能为空"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, namespace: str, event: dict[str, Any]) -> None:
        """Persist one event as a JSONL record."""
        assert namespace, "memory namespace 不能为空"
        assert isinstance(event, dict), "memory event 必须是 dict"
        record = {"namespace": namespace, "event": dict(event)}
        with self.path.open("a", encoding="utf-8") as file_obj:
            file_obj.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")

    def query(self, namespace: str, limit: int | None = None) -> list[dict[str, Any]]:
        """Return events from one namespace in append order."""
        assert namespace, "memory namespace 不能为空"
        if not self.path.exists():
            return []
        events: list[dict[str, Any]] = []
        with self.path.open(encoding="utf-8") as file_obj:
            for line_number, raw_line in enumerate(file_obj, start=1):
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning(
                        "[JsonlRuntimeMemoryBackend] 跳过损坏 JSONL 行：path=%s line=%s",
                        self.path,
                        line_number,
                    )
                    continue
                if record.get("namespace") != namespace:
                    continue
                event = record.get("event")
                if isinstance(event, dict):
                    events.append(dict(event))
        if limit == 0:
            return []
        selected = events[-limit:] if limit is not None else events
        return [dict(event) for event in selected]


class RuntimeMemoryStore:
    """Session-level memory store shared by runners.

    `_buckets` 保存本局运行记忆；`backend` 是跨局/外部长期记忆端口。
    Runner 和 policy 只依赖这个 store，不直接知道外部存储实现。
    """

    def __init__(self, backend: RuntimeMemoryBackend | None = None) -> None:
        self._buckets: dict[str, list[dict[str, Any]]] = {}
        self._backend: RuntimeMemoryBackend = backend or NullRuntimeMemoryBackend()

    @property
    def backend(self) -> RuntimeMemoryBackend:
        """Return the configured long-term memory backend."""
        return self._backend

    def bind_backend(self, backend: RuntimeMemoryBackend) -> None:
        """Attach a long-term memory backend."""
        assert isinstance(backend, RuntimeMemoryBackend), "backend 必须实现 RuntimeMemoryBackend"
        self._backend = backend

    def append(self, bucket: str, event: dict[str, Any]) -> None:
        """Append one event to a named memory bucket."""
        assert bucket, "bucket 不能为空"
        assert isinstance(event, dict), "memory event 必须是 dict"
        self._buckets.setdefault(bucket, []).append(dict(event))

    def remember_long_term(self, namespace: str, event: dict[str, Any]) -> None:
        """Persist one event to the long-term memory backend."""
        assert namespace, "memory namespace 不能为空"
        assert isinstance(event, dict), "memory event 必须是 dict"
        self._backend.append(namespace, dict(event))

    def recall_long_term(self, namespace: str, limit: int | None = None) -> list[dict[str, Any]]:
        """Read long-term memories from the backend."""
        assert namespace, "memory namespace 不能为空"
        return self._backend.query(namespace, limit=limit)

    def list(self, bucket: str, limit: int | None = None) -> list[dict[str, Any]]:
        """Return events from a named memory bucket."""
        assert bucket, "bucket 不能为空"
        events = self._buckets.get(bucket, [])
        if limit == 0:
            return []
        selected = events[-limit:] if limit is not None else events
        return [dict(event) for event in selected]

    def latest(self, bucket: str) -> dict[str, Any] | None:
        """Return the latest event from a named bucket."""
        assert bucket, "bucket 不能为空"
        events = self._buckets.get(bucket, [])
        return dict(events[-1]) if events else None

    def clear(self, bucket: str | None = None) -> None:
        """Clear one bucket or the whole store."""
        if bucket is None:
            self._buckets.clear()
            return
        assert bucket, "bucket 不能为空"
        self._buckets.pop(bucket, None)

    def snapshot(self) -> dict[str, list[dict[str, Any]]]:
        """Return a serializable memory snapshot."""
        return {
            bucket: [dict(event) for event in events]
            for bucket, events in self._buckets.items()
        }

    def load(self, snapshot: dict[str, Any]) -> None:
        """Load a memory snapshot into this store."""
        assert isinstance(snapshot, dict), "memory snapshot 必须是 dict"
        self._buckets.clear()
        for bucket, events in snapshot.items():
            assert bucket, "memory bucket 不能为空"
            assert isinstance(events, list), "memory bucket events 必须是 list"
            self._buckets[str(bucket)] = [dict(event) for event in events]

def configure_runtime_memory_backend(memory_store: Any, config: dict[str, Any]) -> RuntimeMemoryBackend | None:
    """Bind a configured long-term memory backend to a RuntimeMemoryStore.

    Supported config shapes:

    ```yaml
    runtime:
      config:
        memory_backend:
          type: jsonl
          path: .runtime/group_memory.jsonl
    ```

    or:

    ```yaml
    runtime:
      config:
        memory:
          backend:
            type: jsonl
            path: .runtime/group_memory.jsonl
    ```
    """
    assert memory_store is not None, "memory_store 不能为空"
    assert isinstance(config, dict), "runtime config 必须是 dict"
    backend_spec = config.get("memory_backend")
    memory_spec = config.get("memory")
    if backend_spec is None and isinstance(memory_spec, dict):
        backend_spec = memory_spec.get("backend")
    if backend_spec in (None, "", False):
        return None
    if isinstance(backend_spec, str):
        backend_spec = {"type": "jsonl", "path": backend_spec}
    assert isinstance(backend_spec, dict), "memory_backend 必须是字符串或对象"
    backend_type = str(backend_spec.get("type") or "jsonl")
    if backend_type != "jsonl":
        raise ValueError(f"不支持的 memory_backend.type: {backend_type}")
    path = backend_spec.get("path")
    assert isinstance(path, str) and path.strip(), "memory_backend.path 不能为空"
    backend = JsonlRuntimeMemoryBackend(path)
    memory_store.bind_backend(backend)
    return backend
