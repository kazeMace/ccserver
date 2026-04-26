"""
channels/registry — Channel 适配器的注册与发现。

与 OpenClaw 的 registry.ts 对齐：
  - 采用懒加载策略：只注册元数据，不提前初始化连接
  - 支持通过 id 或 aliases 查找（不区分大小写）
  - 规范化 channel ID（normalize_channel_id）

典型用法
────────
registry = ChannelRegistry()
registry.register(DiscordAdapter)
registry.register(TelegramAdapter)

# 通过 ID 获取
adapter = registry.get_adapter("discord")

# 通过别名获取
adapter = registry.get_adapter("dc")  # 返回 DiscordAdapter 实例

# 列出所有已注册
for info in registry.list_channels():
    print(info["id"], info["aliases"])
"""

import importlib
import pkgutil
from typing import Optional, Type

from loguru import logger

from .base import BaseChannelAdapter


class ChannelRegistry:
    """
    Channel 适配器的注册表。

    采用懒加载（lazy initialization）策略：
      - register() 只记录适配器类，不创建实例
      - get_adapter() 第一次调用时才创建实例并缓存
      - 避免启动时初始化所有连接（某些平台连接耗时较长）

    与 OpenClaw 的 getActivePluginChannelRegistryFromState() 对齐。

    Attributes:
        _adapters:    channel_id -> adapter class 的映射
        _instances:   channel_id -> adapter instance 的映射（懒加载缓存）
        _aliases:     alias -> canonical channel_id 的映射
    """

    def __init__(self):
        # channel_id -> adapter class
        self._adapters: dict[str, Type[BaseChannelAdapter]] = {}
        # channel_id -> adapter instance（懒加载）
        self._instances: dict[str, BaseChannelAdapter] = {}
        # alias -> canonical channel_id
        self._aliases: dict[str, str] = {}

        logger.debug("ChannelRegistry initialized")

    # ── 注册 ──────────────────────────────────────────────────────────────────

    # ── 自动扫描发现 ────────────────────────────────────────────────────────────

    def discover(self, package: str = "ccserver.channels.adapters") -> int:
        """
        自动扫描指定包下的所有模块，发现并注册 BaseChannelAdapter 子类。

        扫描逻辑：
          1. 使用 pkgutil 遍历包内所有模块
          2. import 每个模块
          3. 遍历模块中的类，找到 BaseChannelAdapter 的直接/间接子类
          4. 自动调用 register() 注册

        会自动跳过已注册的 channel（避免重复）。

        Args:
            package: 要扫描的 Python 包路径，默认 "ccserver.channels.adapters"

        Returns:
            本次新注册的适配器数量

        Example:
            registry = ChannelRegistry()
            count = registry.discover()  # 自动发现所有适配器
            logger.info(f"Discovered {count} channels")
        """
        registered_count = 0

        try:
            # import 目标包
            pkg = importlib.import_module(package)
        except ImportError as e:
            logger.warning(
                "Channel discover: failed to import package '{}' | err={}",
                package, e,
            )
            return 0

        # 遍历包内所有模块
        for finder, module_name, is_pkg in pkgutil.iter_modules(pkg.__path__, pkg.__name__ + "."):
            if is_pkg:
                # 递归扫描子包
                sub_count = self.discover(module_name)
                registered_count += sub_count
                continue

            try:
                module = importlib.import_module(module_name)
            except Exception as e:
                logger.warning(
                    "Channel discover: failed to import module '{}' | err={}",
                    module_name, e,
                )
                continue

            # 遍历模块中的类，找到 BaseChannelAdapter 子类
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                # 过滤：必须是类、继承 BaseChannelAdapter、不是 BaseChannelAdapter 本身、有 channel_id
                if (
                    isinstance(attr, type)
                    and issubclass(attr, BaseChannelAdapter)
                    and attr is not BaseChannelAdapter
                    and getattr(attr, "channel_id", "")
                ):
                    try:
                        self.register(attr)
                        registered_count += 1
                    except ValueError as e:
                        # 已注册，跳过
                        logger.debug(
                            "Channel discover: skip '{}' | {}",
                            attr.__name__, e,
                        )
                    except Exception as e:
                        logger.error(
                            "Channel discover: failed to register '{}' | err={}",
                            attr.__name__, e,
                        )

        logger.info(
            "Channel discover complete | package={} registered={}",
            package, registered_count,
        )
        return registered_count

    def register(self, adapter_cls: Type[BaseChannelAdapter]) -> None:
        """
        注册一个 channel 适配器类。

        只记录元数据，不创建实例。实例在第一次 get_adapter() 时创建。

        Args:
            adapter_cls: 继承 BaseChannelAdapter 的类（不是实例）。
                         必须定义 channel_id 类属性。

        Raises:
            TypeError:  如果 adapter_cls 不是 BaseChannelAdapter 的子类
            ValueError: 如果 channel_id 为空或已被注册

        Example:
            registry.register(DiscordAdapter)
            registry.register(TelegramAdapter)
        """
        # 类型检查
        if not issubclass(adapter_cls, BaseChannelAdapter):
            raise TypeError(
                f"Expected BaseChannelAdapter subclass, got {adapter_cls.__name__}"
            )

        channel_id = adapter_cls.channel_id
        if not channel_id:
            raise ValueError(
                f"{adapter_cls.__name__}.channel_id is empty"
            )

        # 规范化：小写
        canonical = channel_id.lower().strip()

        # 检查是否已注册
        if canonical in self._adapters:
            existing = self._adapters[canonical].__name__
            raise ValueError(
                f"Channel '{canonical}' already registered by {existing}"
            )

        # 注册适配器类
        self._adapters[canonical] = adapter_cls

        # 注册别名
        for alias in adapter_cls.aliases:
            alias_key = alias.lower().strip()
            if alias_key in self._aliases:
                logger.warning(
                    "Alias '{}' already mapped to '{}', overwriting with '{}'",
                    alias_key, self._aliases[alias_key], canonical,
                )
            self._aliases[alias_key] = canonical

        logger.info(
            "Channel registered | id={} aliases={} class={}",
            canonical, adapter_cls.aliases, adapter_cls.__name__,
        )

    def unregister(self, channel_id: str) -> None:
        """
        注销一个 channel 适配器。

        会同时清理实例缓存和别名映射。

        Args:
            channel_id: channel ID 或别名
        """
        canonical = self.normalize_channel_id(channel_id)
        if not canonical:
            logger.warning("Unregister: unknown channel '{}'", channel_id)
            return

        # 清理实例
        if canonical in self._instances:
            del self._instances[canonical]
            logger.debug("Instance cache cleared | channel_id={}", canonical)

        # 获取适配器类，清理别名
        adapter_cls = self._adapters.get(canonical)
        if adapter_cls:
            for alias in adapter_cls.aliases:
                alias_key = alias.lower().strip()
                if self._aliases.get(alias_key) == canonical:
                    del self._aliases[alias_key]

        # 注销类
        del self._adapters[canonical]
        logger.info("Channel unregistered | id={}", canonical)

    # ── 查找 ──────────────────────────────────────────────────────────────────

    def normalize_channel_id(self, name: str) -> Optional[str]:
        """
        规范化 channel ID。

        与 OpenClaw 的 normalizeChannelId() 对齐。
        支持通过别名查找。

        查找顺序：
          1. 直接匹配（不区分大小写）
          2. 别名匹配

        Args:
            name: channel ID 或别名，如 "discord" 或 "dc"

        Returns:
            规范化的 channel_id（小写），如果找不到则返回 None

        Example:
            registry.normalize_channel_id("discord") -> "discord"
            registry.normalize_channel_id("dc")     -> "discord"
            registry.normalize_channel_id("unknown") -> None
        """
        if not name or not isinstance(name, str):
            return None

        key = name.lower().strip()

        # 直接匹配
        if key in self._adapters:
            return key

        # 别名匹配
        return self._aliases.get(key)

    def get_adapter(self, channel_id: str) -> Optional[BaseChannelAdapter]:
        """
        获取适配器实例（懒加载）。

        第一次获取时创建实例并缓存。后续调用返回缓存实例。

        Args:
            channel_id: channel ID 或别名

        Returns:
            适配器实例，如果 channel_id 不存在则返回 None

        Example:
            adapter = registry.get_adapter("discord")
            adapter2 = registry.get_adapter("dc")  # 返回同一个实例
        """
        canonical = self.normalize_channel_id(channel_id)
        if not canonical:
            logger.warning(
                "get_adapter: unknown channel '{}' | known: {}",
                channel_id, list(self._adapters.keys()),
            )
            return None

        # 检查缓存
        if canonical in self._instances:
            return self._instances[canonical]

        # 懒加载：创建实例
        adapter_cls = self._adapters.get(canonical)
        if not adapter_cls:
            return None

        try:
            instance = adapter_cls()
            self._instances[canonical] = instance
            logger.info(
                "Adapter instance created | channel_id={} class={}",
                canonical, adapter_cls.__name__,
            )
            return instance
        except Exception as e:
            logger.error(
                "Failed to create adapter instance | channel_id={} err={}",
                canonical, e,
            )
            return None

    def get_adapter_class(self, channel_id: str) -> Optional[Type[BaseChannelAdapter]]:
        """
        获取适配器类（不创建实例）。

        Args:
            channel_id: channel ID 或别名

        Returns:
            适配器类，如果不存在则返回 None
        """
        canonical = self.normalize_channel_id(channel_id)
        if not canonical:
            return None
        return self._adapters.get(canonical)

    # ── 查询 ──────────────────────────────────────────────────────────────────

    def list_channels(self) -> list[dict]:
        """
        列出所有已注册的 channel 元数据。

        Returns:
            每个 channel 的信息字典列表：
            [
                {
                    "id": "discord",
                    "aliases": ["dc"],
                    "capabilities": {
                        "chat_types": ["direct", "group"],
                        "supports_media": True,
                        ...
                    }
                },
                ...
            ]
        """
        result = []
        for channel_id, adapter_cls in self._adapters.items():
            result.append({
                "id": channel_id,
                "aliases": adapter_cls.aliases,
                "capabilities": {
                    "chat_types": adapter_cls.meta.chat_types,
                    "supports_media": adapter_cls.meta.supports_media,
                    "supports_reactions": adapter_cls.meta.supports_reactions,
                    "supports_reply": adapter_cls.meta.supports_reply,
                    "supports_edit": adapter_cls.meta.supports_edit,
                    "supports_delete": adapter_cls.meta.supports_delete,
                    "markdown_capable": adapter_cls.meta.markdown_capable,
                    "max_text_length": adapter_cls.meta.max_text_length,
                },
            })
        return result

    def is_registered(self, channel_id: str) -> bool:
        """
        检查 channel 是否已注册。

        Args:
            channel_id: channel ID 或别名

        Returns:
            True 如果已注册
        """
        return self.normalize_channel_id(channel_id) is not None

    def __len__(self) -> int:
        """返回已注册的 channel 数量。"""
        return len(self._adapters)

    def __contains__(self, channel_id: str) -> bool:
        """支持 'discord' in registry 语法。"""
        return self.is_registered(channel_id)
