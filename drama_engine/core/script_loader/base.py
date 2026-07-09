"""脚本加载器抽象基类。

定义 ScriptLoader 体系的所有抽象接口。
具体实现可替换（如远程脚本源、数据库存储等）。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from drama_engine.core.script_loader.models import (
    HookSpec,
    PluginSpec,
    RawScriptDoc,
    ScriptBundle,
    ScriptMeta,
)


class BaseScriptDiscovery(ABC):
    """脚本发现器基类 — 从来源枚举可用脚本路径。"""

    @abstractmethod
    async def discover(self) -> list[Path]:
        """异步发现所有可用脚本路径。

        返回:
            脚本路径列表（目录或单文件）
        """
        ...


class BasePackageReader(ABC):
    """包读取器基类 — 从路径读取原始文档。"""

    @abstractmethod
    async def read(self, path: Path, params: dict | None = None) -> RawScriptDoc:
        """读取路径（目录或单文件），返回统一的原始文档。

        参数:
            path: 脚本路径（目录或 .yaml 文件）
            params: 模板参数（用于 {{param}} 展开）

        返回:
            RawScriptDoc（合并后的完整 dict + 元信息）
        """
        ...

    @abstractmethod
    async def read_meta(self, path: Path) -> ScriptMeta:
        """轻量读取：只提取元数据。

        参数:
            path: 脚本路径

        返回:
            ScriptMeta
        """
        ...


class BasePluginScanner(ABC):
    """插件扫描器基类 — 从目录发现插件声明。"""

    @abstractmethod
    async def scan(self, plugins_dir: Path) -> list[PluginSpec]:
        """扫描目录，返回插件声明列表。

        参数:
            plugins_dir: plugins/ 目录路径

        返回:
            PluginSpec 列表
        """
        ...


class BaseHookScanner(ABC):
    """Hook 扫描器基类 — 从目录发现 hook 声明。"""

    @abstractmethod
    async def scan(self, hooks_dir: Path) -> list[HookSpec]:
        """扫描目录，返回 hook 声明列表。

        参数:
            hooks_dir: hooks/ 目录路径

        返回:
            HookSpec 列表
        """
        ...


class BaseScriptLoader(ABC):
    """脚本加载器基类 — 统一入口，产出 ScriptBundle。"""

    @abstractmethod
    async def load(self, path: Path, params: dict | None = None) -> ScriptBundle:
        """完整加载脚本包，返回统一 ScriptBundle。

        参数:
            path: 脚本路径（目录或单文件）
            params: 模板参数

        返回:
            ScriptBundle
        """
        ...

    @abstractmethod
    async def load_meta(self, path: Path) -> ScriptMeta:
        """轻量加载：只返回元数据（供 Catalog 使用）。

        参数:
            path: 脚本路径

        返回:
            ScriptMeta
        """
        ...

    @abstractmethod
    async def discover_and_load_all(self, root: Path) -> list[ScriptBundle]:
        """发现并加载根目录下所有脚本。

        参数:
            root: 脚本根目录

        返回:
            ScriptBundle 列表
        """
        ...


__all__ = [
    "BaseHookScanner",
    "BasePackageReader",
    "BasePluginScanner",
    "BaseScriptDiscovery",
    "BaseScriptLoader",
]
