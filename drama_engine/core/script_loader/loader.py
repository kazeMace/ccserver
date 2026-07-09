"""脚本加载器 — 组合各子组件，产出统一 ScriptBundle。

使用方式：
    loader = ScriptLoader()
    bundle = await loader.load(Path("my_game/"))
    bundles = await loader.discover_and_load_all(Path("scripts/"))

依赖注入：所有子组件可替换（通过构造参数传入自定义实现）。
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any

from drama_engine.core.script_loader.base import (
    BaseHookScanner,
    BasePackageReader,
    BasePluginScanner,
    BaseScriptDiscovery,
    BaseScriptLoader,
)
from drama_engine.core.script_loader.models import (
    GamePackRef,
    ScriptBundle,
    ScriptMeta,
)

logger = logging.getLogger(__name__)


class ScriptLoader(BaseScriptLoader):
    """默认脚本加载器 — 组合 Discovery/Reader/Scanner 完成完整加载。

    依赖注入：
        reader: BasePackageReader — 读取 YAML 文档
        plugin_scanner: BasePluginScanner — 扫描 plugins/ 目录
        hook_scanner: BaseHookScanner — 扫描 hooks/ 目录
    """

    def __init__(
        self,
        reader: BasePackageReader | None = None,
        plugin_scanner: BasePluginScanner | None = None,
        hook_scanner: BaseHookScanner | None = None,
    ) -> None:
        """初始化加载器，使用默认实现或注入自定义实现。

        参数:
            reader: 包读取器（默认 YamlPackageReader）
            plugin_scanner: 插件扫描器（默认 DirectoryPluginScanner）
            hook_scanner: Hook 扫描器（默认 DirectoryHookScanner）
        """
        from drama_engine.core.script_loader.hook_scanner import DirectoryHookScanner
        from drama_engine.core.script_loader.package_reader import YamlPackageReader
        from drama_engine.core.script_loader.plugin_scanner import DirectoryPluginScanner

        self._reader = reader or YamlPackageReader()
        self._plugin_scanner = plugin_scanner or DirectoryPluginScanner()
        self._hook_scanner = hook_scanner or DirectoryHookScanner()

    async def load(self, path: Path) -> ScriptBundle:
        """完整加载脚本包，返回统一 ScriptBundle。

        流程：
          1. reader.read(path) → RawScriptDoc
          2. 提取 meta / roles / game_packs
          3. plugin_scanner.scan(plugins/) → list[PluginSpec]
          4. hook_scanner.scan(hooks/) → list[HookSpec]
          5. 组装 ScriptBundle

        参数:
            path: 脚本路径（目录或单文件）

        返回:
            ScriptBundle
        """
        # 1. 读取原始文档
        raw_doc_obj = await self._reader.read(path)
        doc = raw_doc_obj.doc
        logger.debug("[ScriptLoader] 读取完成: %s", path)

        # 2. 提取元数据
        meta = self._extract_meta(doc, path)

        # 3. 提取 roles
        roles = self._extract_roles(doc)

        # 4. 提取 game_packs 引用
        game_packs = self._extract_game_packs(doc)

        # 5. 扫描 plugins/（仅包目录有效）
        plugin_specs = []
        if path.is_dir():
            plugins_dir = path / "plugins"
            plugin_specs = await self._plugin_scanner.scan(plugins_dir)

        # 6. 扫描 hooks/（仅包目录有效）
        hook_specs = []
        if path.is_dir():
            hooks_dir = path / "hooks"
            hook_specs = await self._hook_scanner.scan(hooks_dir)

        # 7. 组装 ScriptBundle
        bundle = ScriptBundle(
            bundle_id=str(uuid.uuid4()),
            source_path=path,
            meta=meta,
            raw_doc=doc,
            roles=roles,
            game_packs=game_packs,
            plugin_specs=plugin_specs,
            hook_specs=hook_specs,
        )
        logger.info(
            "[ScriptLoader] 加载完成: %s (id=%s, plugins=%d, hooks=%d)",
            meta.name,
            bundle.bundle_id[:8],
            len(plugin_specs),
            len(hook_specs),
        )
        return bundle

    async def load_meta(self, path: Path) -> ScriptMeta:
        """轻量加载：只返回元数据（供 Catalog 使用）。

        参数:
            path: 脚本路径

        返回:
            ScriptMeta
        """
        return await self._reader.read_meta(path)

    async def discover_and_load_all(self, root: Path) -> list[ScriptBundle]:
        """发现并加载根目录下所有脚本。

        参数:
            root: 脚本根目录

        返回:
            ScriptBundle 列表
        """
        from drama_engine.core.script_loader.discovery import FileSystemDiscovery

        discovery = FileSystemDiscovery(root)
        paths = await discovery.discover()
        logger.info("[ScriptLoader] 发现 %d 个脚本，开始加载...", len(paths))

        bundles: list[ScriptBundle] = []
        for path in paths:
            try:
                bundle = await self.load(path)
                bundles.append(bundle)
            except Exception as e:
                logger.error("[ScriptLoader] 加载失败: %s, 错误: %s", path, e)

        logger.info("[ScriptLoader] 全部加载完成: %d/%d 成功", len(bundles), len(paths))
        return bundles

    # ─── 私有方法 ───────────────────────────────────────────

    def _extract_meta(self, doc: dict[str, Any], source: Path) -> ScriptMeta:
        """从文档 dict 提取 ScriptMeta。"""
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

    def _extract_roles(self, doc: dict[str, Any]) -> list[dict[str, Any]]:
        """从文档提取 roles 列表。"""
        roles = doc.get("roles")
        if roles is None:
            return []
        if isinstance(roles, list):
            return roles
        return []

    def _extract_game_packs(self, doc: dict[str, Any]) -> list[GamePackRef]:
        """从文档提取 game_pack 引用列表。"""
        refs: list[GamePackRef] = []

        # runtime.game_pack 单个引用
        runtime = doc.get("runtime") or {}
        gp = runtime.get("game_pack")
        if gp:
            if isinstance(gp, str):
                refs.append(GamePackRef(plugin_id=gp))
            elif isinstance(gp, dict):
                refs.append(GamePackRef(
                    plugin_id=gp.get("id", ""),
                    config=gp.get("config") or {},
                ))

        # runtime.game_packs 多个引用
        gps = runtime.get("game_packs")
        if isinstance(gps, list):
            for item in gps:
                if isinstance(item, str):
                    refs.append(GamePackRef(plugin_id=item))
                elif isinstance(item, dict):
                    refs.append(GamePackRef(
                        plugin_id=item.get("id", ""),
                        config=item.get("config") or {},
                    ))

        return refs


__all__ = ["ScriptLoader"]
