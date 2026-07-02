"""Runtime config parsing for runners."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from drama_engine.core.runtime_spec.registry import RuntimeSpec

class RuntimeConfigParser:
    """Read runtime-level configuration from a script file."""

    def read_document(self, script_path: str) -> dict[str, Any]:
        """Read a YAML script document."""
        assert script_path, "script_path 不能为空"
        data = yaml.safe_load(Path(script_path).read_text(encoding="utf-8")) or {}
        assert isinstance(data, dict), "script YAML 顶层必须是 dict"
        return data

    def runtime_config(self, script_path: str, declaration: RuntimeSpec | None = None) -> dict[str, Any]:
        """Return runtime.config, preferring an already parsed declaration."""
        if declaration is not None and isinstance(declaration.config, dict):
            return dict(declaration.config)
        doc = self.read_document(script_path)
        runtime_spec = doc.get("runtime") or {}
        if isinstance(runtime_spec, dict):
            config = runtime_spec.get("config") or {}
            if isinstance(config, dict):
                return dict(config)
        return {}

    def script_title(self, script_path: str) -> str:
        """Return meta.title from a YAML script."""
        doc = self.read_document(script_path)
        meta = doc.get("meta") or {}
        if isinstance(meta, dict):
            return str(meta.get("title") or "")
        return ""
