"""Runtime config parsing for runners."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from drama_engine.core.runtime_spec.registry import RuntimeSpec

class RuntimeConfigParser:
    """Read runtime-level configuration from a script file."""

    def read_document(self, script_path: str) -> dict[str, Any]:
        """Read a YAML script document (支持单文件和包目录)."""
        assert script_path, "script_path 不能为空"
        path = Path(script_path)
        if path.is_dir():
            # 包目录：合并 manifest + roles + script
            doc: dict[str, Any] = {}
            manifest = path / "manifest.yaml"
            if manifest.exists():
                doc.update(yaml.safe_load(manifest.read_text(encoding="utf-8")) or {})
            roles_file = path / "roles.yaml"
            if roles_file.exists():
                roles_data = yaml.safe_load(roles_file.read_text(encoding="utf-8")) or {}
                if isinstance(roles_data, dict):
                    doc.update(roles_data)
                elif isinstance(roles_data, list):
                    doc["roles"] = roles_data
            script_file = path / "script.yaml"
            if script_file.exists():
                doc.update(yaml.safe_load(script_file.read_text(encoding="utf-8")) or {})
        else:
            doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        assert isinstance(doc, dict), "script YAML 顶层必须是 dict"
        return doc

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
