"""
builtins/agents/registry -- 内置 Agent 的自动发现注册表。

与 ChannelRegistry 对齐：
  - pkgutil 扫描 ccserver.builtins.agents.specs 包
  - 发现所有 BaseAgentSpec 子类
  - discover() 返回 {name: AgentDef} 字典

自动发现流程：
  specs/ 下每个 .py 文件定义一个或多个 BaseAgentSpec 子类
  -> discover() 扫描所有模块
  -> 找到 BaseAgentSpec 子类
  -> 调用 spec.build_agent_def() 转换为 AgentDef
  -> 返回完整字典

关键设计：
  - 与 ChannelRegistry 的区别：存储 AgentDef（数据类），不是类实例
  - discover() 在首次访问时由 module-level get() 函数触发
  - 支持 agents.json 配置过滤（未启用的 Agent 不注册）
"""

import importlib
import pkgutil
from typing import TYPE_CHECKING

from loguru import logger

from .base import BaseAgentSpec

if TYPE_CHECKING:
    from ccserver.managers.agents.manager import AgentDef


# ---------------------------------------------------------------------------
# AgentRegistry
# ---------------------------------------------------------------------------

class AgentRegistry:
    """
    内置 Agent 的注册表。

    采用懒加载策略：
      - discover() 扫描包内所有模块，找到 BaseAgentSpec 子类
      - 存储的是 AgentDef（数据类），不是类实例
      - 对外提供只读字典接口

    与 ChannelRegistry 的关键区别：
      - 存储 AgentDef（数据类），不是 BaseChannelAdapter 类
      - discover() 在首次访问时触发
      - 支持 agents.json 配置过滤
    """

    def __init__(self):
        """初始化空注册表。"""
        # name -> AgentDef 的映射
        self._agents: dict[str, "AgentDef"] = {}
        # 是否已执行过发现
        self._discovered: bool = False

        logger.debug("AgentRegistry initialized")

    # -- 自动发现 -----------------------------------------------------------

    def discover(self, package: str = "ccserver.builtins.agents.specs") -> int:
        """
        扫描指定包，发现所有 BaseAgentSpec 子类并注册。

        扫描逻辑：
          1. importlib.import_module(package) 导入目标包
          2. pkgutil.iter_modules() 遍历包内所有模块
          3. import 每个模块
          4. 遍历模块中的类，找到 BaseAgentSpec 的直接/间接子类
          5. 调用 build_agent_def() 转换为 AgentDef
          6. 注册到 _agents 字典

        会自动跳过未启用的 Agent（enabled=false）。
        已发现的 Agent 不会重复注册。

        Args:
            package: 要扫描的 Python 包路径，
                     默认 "ccserver.builtins.agents.specs"

        Returns:
            本次新注册的 Agent 数量
        """
        if self._discovered:
            return len(self._agents)

        registered_count = 0

        try:
            pkg = importlib.import_module(package)
        except ImportError as e:
            logger.warning(
                "AgentRegistry discover: failed to import package '{}' | {}",
                package, e,
            )
            return 0

        # 遍历包内所有模块
        for finder, module_name, is_pkg in pkgutil.iter_modules(
            pkg.__path__, pkg.__name__ + "."
        ):
            if is_pkg:
                # 递归扫描子包
                sub_count = self.discover(module_name)
                registered_count += sub_count
                continue

            try:
                module = importlib.import_module(module_name)
            except Exception as e:
                logger.warning(
                    "AgentRegistry discover: failed to import module '{}' | {}",
                    module_name, e,
                )
                continue

            # 遍历模块中的类，找到 BaseAgentSpec 子类
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if (
                    isinstance(attr, type)
                    and issubclass(attr, BaseAgentSpec)
                    and attr is not BaseAgentSpec
                    and hasattr(attr, "name")
                ):
                    try:
                        spec: BaseAgentSpec = attr
                        agent_def = spec.build_agent_def()
                        # 只注册启用的 Agent（system 非空或 name 在允许列表中）
                        if agent_def.system or spec.is_enabled():
                            self._agents[agent_def.name] = agent_def
                            registered_count += 1
                            logger.debug(
                                "AgentRegistry registered | name={} "
                                "location={} builtin={}",
                                agent_def.name,
                                agent_def.location,
                                agent_def.is_builtin,
                            )
                    except Exception as e:
                        logger.error(
                            "AgentRegistry discover: failed to build_agent_def "
                            "for '{}' | {}",
                            attr_name, e,
                        )

        self._discovered = True
        logger.info(
            "AgentRegistry discover complete | package={} registered={}",
            package, registered_count,
        )
        return registered_count

    # -- 查询 ---------------------------------------------------------------

    def get(self, name: str) -> "AgentDef | None":
        """
        按名称获取 AgentDef。

        Args:
            name: Agent 名称

        Returns:
            AgentDef 实例，不存在返回 None
        """
        if not self._discovered:
            self.discover()
        return self._agents.get(name)

    def list_all(self) -> list["AgentDef"]:
        """
        返回所有已注册的 AgentDef 列表。

        Returns:
            AgentDef 列表
        """
        if not self._discovered:
            self.discover()
        return list(self._agents.values())

    def list_names(self) -> list[str]:
        """
        返回所有已注册的 Agent 名称列表。

        Returns:
            Agent 名称列表
        """
        if not self._discovered:
            self.discover()
        return list(self._agents.keys())

    def is_registered(self, name: str) -> bool:
        """
        检查 Agent 是否已注册。

        Args:
            name: Agent 名称

        Returns:
            True 如果已注册
        """
        if not self._discovered:
            self.discover()
        return name in self._agents

    def __contains__(self, name: str) -> bool:
        """支持 'Explore' in registry 语法。"""
        return self.is_registered(name)

    def __len__(self) -> int:
        """返回已注册的 Agent 数量。"""
        if not self._discovered:
            self.discover()
        return len(self._agents)


# ---------------------------------------------------------------------------
# Module-level 单例 + 便捷函数
# ---------------------------------------------------------------------------

_registry: AgentRegistry | None = None


def agent_registry() -> AgentRegistry:
    """
    返回全局 AgentRegistry 单例（延迟初始化）。

    Returns:
        AgentRegistry 实例
    """
    global _registry
    if _registry is None:
        _registry = AgentRegistry()
    return _registry


def discover_builtin_agents() -> dict[str, "AgentDef"]:
    """
    触发内置 Agent 自动发现，返回 {name: AgentDef} 字典。

    只包含启用的 Agent（system 非空或 agents.json enabled=true）。

    供 AgentFactory / AgentLoader 在初始化时调用。

    Returns:
        {agent_name: AgentDef, ...}
    """
    reg = agent_registry()
    reg.discover()
    return dict(reg._agents)


def discover_all_agents() -> dict[str, "AgentDef"]:
    """
    触发全部 Agent 发现（内置 + 配置），返回 {name: AgentDef} 字典。

    内置 Agent 的 is_builtin=True。

    Returns:
        {agent_name: AgentDef, ...}
    """
    return discover_builtin_agents()
