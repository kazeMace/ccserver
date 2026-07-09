"""统一脚本加载模块 — ScriptLoader 体系的公开入口。

使用方式：
    from drama_engine.core.script_loader import ScriptLoader, ScriptBundle, ScriptMeta

    loader = ScriptLoader()
    bundle = await loader.load(Path("my_game/"))
"""

from drama_engine.core.script_loader.base import (
    BaseHookScanner,
    BasePackageReader,
    BasePluginScanner,
    BaseScriptDiscovery,
    BaseScriptLoader,
)
from drama_engine.core.script_loader.discovery import FileSystemDiscovery
from drama_engine.core.script_loader.hook_scanner import DirectoryHookScanner
from drama_engine.core.script_loader.loader import ScriptLoader
from drama_engine.core.script_loader.models import (
    GamePackRef,
    HookSpec,
    PluginSpec,
    RawScriptDoc,
    ScriptBundle,
    ScriptMeta,
    VALID_HOOK_EVENTS,
)
from drama_engine.core.script_loader.package_reader import YamlPackageReader
from drama_engine.core.script_loader.plugin_scanner import DirectoryPluginScanner

__all__ = [
    # ABC
    "BaseHookScanner",
    "BasePackageReader",
    "BasePluginScanner",
    "BaseScriptDiscovery",
    "BaseScriptLoader",
    # 具体实现
    "DirectoryHookScanner",
    "DirectoryPluginScanner",
    "FileSystemDiscovery",
    "ScriptLoader",
    "YamlPackageReader",
    # 数据对象
    "GamePackRef",
    "HookSpec",
    "PluginSpec",
    "RawScriptDoc",
    "ScriptBundle",
    "ScriptMeta",
    "VALID_HOOK_EVENTS",
]
