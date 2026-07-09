"""YAML 包读取器 — 从路径读取并合并脚本文档。

支持两种形式：
  - 单文件 .yaml：直接读取（支持 {{param}} 模板展开）
  - 包目录：合并 manifest.yaml + roles.yaml + script.yaml
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import yaml

from drama_engine.core.script_loader.base import BasePackageReader
from drama_engine.core.script_loader.models import RawScriptDoc, ScriptMeta

logger = logging.getLogger(__name__)


class YamlPackageReader(BasePackageReader):
    """YAML 包读取器：支持单文件和目录两种形式。"""

    async def read(self, path: Path, params: dict[str, Any] | None = None) -> RawScriptDoc:
        """读取路径，返回统一的原始文档。

        参数:
            path: 脚本路径（目录或 .yaml 文件）
            params: 模板参数（用于 {{param}} 展开，仅单文件生效）

        返回:
            RawScriptDoc
        """
        if path.is_dir():
            doc = self._read_package_dir(path)
            return RawScriptDoc(doc=doc, source_path=path, is_package=True)
        else:
            doc = self._read_single_file(path, params or {})
            return RawScriptDoc(doc=doc, source_path=path, is_package=False)

    async def read_meta(self, path: Path) -> ScriptMeta:
        """轻量读取：只提取元数据（不读 script.yaml）。

        参数:
            path: 脚本路径

        返回:
            ScriptMeta
        """
        if path.is_dir():
            return self._extract_meta_from_dir(path)
        else:
            return self._extract_meta_from_file(path)

    # ─── 私有方法 ───────────────────────────────────────────

    def _read_package_dir(self, pkg_dir: Path) -> dict[str, Any]:
        """读取包目录：合并 manifest + roles + script。

        合并顺序：manifest → roles → script（后者覆盖前者同名 key）。
        """
        doc: dict[str, Any] = {}

        # 1. manifest.yaml（必填）
        manifest_path = pkg_dir / "manifest.yaml"
        assert manifest_path.exists(), f"包目录缺少 manifest.yaml: {pkg_dir}"
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
        doc.update(manifest)
        logger.debug("[YamlPackageReader] 读取 manifest: %s", manifest_path)

        # 2. roles.yaml（可选）
        roles_path = pkg_dir / "roles.yaml"
        if roles_path.exists():
            roles_data = yaml.safe_load(roles_path.read_text(encoding="utf-8"))
            if isinstance(roles_data, list):
                doc["roles"] = roles_data
            elif isinstance(roles_data, dict):
                doc.update(roles_data)
            logger.debug("[YamlPackageReader] 读取 roles: %s", roles_path)

        # 3. script.yaml（必填）
        script_path = pkg_dir / "script.yaml"
        assert script_path.exists(), f"包目录缺少 script.yaml: {pkg_dir}"
        script_data = yaml.safe_load(script_path.read_text(encoding="utf-8")) or {}
        doc.update(script_data)
        logger.debug("[YamlPackageReader] 读取 script: %s", script_path)

        return doc

    def _read_single_file(self, path: Path, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """读取单文件脚本（支持 {{param}} 模板展开）。"""
        assert path.exists(), f"脚本文件不存在: {path}"
        text = path.read_text(encoding="utf-8")

        # 模板参数展开
        if params:
            preliminary = yaml.safe_load(text) or {}
            resolved = self._resolve_params(preliminary, params)
            text = self._expand_params(text, resolved)

        doc = yaml.safe_load(text) or {}
        assert isinstance(doc, dict), f"脚本文件顶层必须是 dict: {path}"
        logger.debug("[YamlPackageReader] 读取单文件: %s", path)
        return doc

    def _resolve_params(self, doc: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
        """解析 params 块的默认值，并与 override 合并。"""
        result: dict[str, Any] = {}
        for param_def in doc.get("params", []) if isinstance(doc, dict) else []:
            if isinstance(param_def, dict) and param_def.get("name"):
                result[str(param_def["name"])] = param_def.get("default")
        result.update(override)
        return result

    def _expand_params(self, raw_text: str, params: dict[str, Any]) -> str:
        """替换 {{param}} 占位符。"""
        def replace(match: re.Match) -> str:
            name = match.group(1).strip()
            if name not in params:
                return match.group(0)
            return str(params[name])
        return re.sub(r"\{\{\s*([^}]+)\s*\}\}", replace, raw_text)

    def _extract_meta_from_dir(self, pkg_dir: Path) -> ScriptMeta:
        """从包目录提取元数据（只读 manifest.yaml）。"""
        manifest_path = pkg_dir / "manifest.yaml"
        if not manifest_path.exists():
            return self._fallback_meta(pkg_dir)
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
        return self._parse_meta(manifest, pkg_dir)

    def _extract_meta_from_file(self, path: Path) -> ScriptMeta:
        """从单文件提取元数据（读取 meta 块）。"""
        if not path.exists():
            return self._fallback_meta(path)
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return self._parse_meta(doc, path)

    def _parse_meta(self, doc: dict[str, Any], source: Path) -> ScriptMeta:
        """从 doc 中解析 ScriptMeta。"""
        meta = doc.get("meta") or {}
        stem = source.stem if source.is_file() else source.name
        return ScriptMeta(
            id=str(meta.get("id") or stem),
            name=str(meta.get("name") or stem),
            display_name=str(meta.get("display_name") or meta.get("title") or stem),
            title=str(meta.get("title") or meta.get("display_name") or stem),
            version=str(meta.get("version") or "1.0.0"),
            author=str(meta.get("author") or ""),
            description=str(meta.get("description") or ""),
            tags=list(meta.get("tags") or []),
            locale=str(meta.get("locale") or "zh-CN"),
            recommended_player_role=meta.get("recommended_player_role"),
        )

    def _fallback_meta(self, source: Path) -> ScriptMeta:
        """路径不存在时的 fallback 元数据。"""
        stem = source.stem if source.is_file() else source.name
        return ScriptMeta(id=stem, name=stem)


__all__ = ["YamlPackageReader"]
