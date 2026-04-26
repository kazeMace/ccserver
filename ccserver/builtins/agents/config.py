"""
builtins/agents/config -- agents.json 配置管理。

配置文件路径：.ccserver/agents.json（相对于 CCSERVER_PROJECT_DIR）

配置格式：
{
    "agents": {
        "Explore": {
            "enabled": true,
            "auto_approve_tools": false,
            "default_model": "haiku"
        }
    }
}

AgentConfig 供 BaseAgentSpec.build_agent_def() 在构造 AgentDef 时查询运行时配置。

与 ChannelConfig 对齐：
  - 延迟加载：第一次查询时才读取文件
  - 自动创建：文件不存在时创建默认配置
  - 热重载：每次查询前检查文件 mtime，变化时重新加载
  - 全局单例：module-level agent_config 实例
"""

import json
import os
from pathlib import Path

from loguru import logger


# ---------------------------------------------------------------------------
# AgentConfig
# ---------------------------------------------------------------------------

class AgentConfig:
    """
    agents.json 配置管理器。

    负责读取、解析和提供 agent 配置。
    配置文件路径：.ccserver/agents.json（相对于 CCSERVER_PROJECT_DIR）

    Attributes:
        _config_path: Path -- 配置文件绝对路径
        _data:        dict -- 配置数据字典
        _loaded:      bool -- 是否已成功加载
        _mtime:       float -- 上次加载时的文件修改时间
    """

    DEFAULT_CONFIG: dict = {
        "agents": {}
    }

    def __init__(self, config_path: str | None = None):
        """
        初始化配置管理器。

        Args:
            config_path: 配置文件路径，默认从环境变量 CCSERVER_PROJECT_DIR
                         推导为 .ccserver/agents.json
        """
        if config_path:
            self._config_path = Path(config_path).resolve()
        else:
            project_dir = os.environ.get("CCSERVER_PROJECT_DIR", ".")
            self._config_path = Path(project_dir) / ".ccserver" / "agents.json"
        self._data: dict = {}
        self._loaded: bool = False
        self._mtime: float = 0.0

        logger.debug(
            "AgentConfig initialized | path={}",
            self._config_path,
        )

    # -- 加载 ---------------------------------------------------------------

    def load(self) -> bool:
        """
        从磁盘加载配置文件。

        如果文件不存在，创建默认配置文件并返回 False。

        Returns:
            True 如果成功加载，False 如果文件不存在或解析失败
        """
        if not self._config_path.exists():
            logger.info(
                "Agent config not found, creating default | path={}",
                self._config_path,
            )
            self._ensure_dir()
            self._data = dict(self.DEFAULT_CONFIG)
            self._save()
            self._loaded = True
            return False

        # 热重载：检查 mtime
        try:
            current_mtime = self._config_path.stat().st_mtime
            if self._loaded and current_mtime == self._mtime:
                return True
        except OSError:
            pass

        try:
            with open(self._config_path, "r", encoding="utf-8") as f:
                self._data = json.load(f)
            self._loaded = True
            try:
                self._mtime = self._config_path.stat().st_mtime
            except OSError:
                pass
            logger.info(
                "AgentConfig loaded | path={} agents={}",
                self._config_path,
                list(self._data.get("agents", {}).keys()),
            )
            return True
        except json.JSONDecodeError as e:
            logger.error(
                "AgentConfig JSON error | path={} err={}",
                self._config_path, e,
            )
            self._data = dict(self.DEFAULT_CONFIG)
            return False
        except Exception as e:
            logger.error(
                "AgentConfig load failed | path={} err={}",
                self._config_path, e,
            )
            self._data = dict(self.DEFAULT_CONFIG)
            return False

    def reload(self) -> bool:
        """
        重新加载配置文件。

        Returns:
            True 如果成功加载
        """
        logger.info("AgentConfig reloading...")
        self._loaded = False
        return self.load()

    # -- 查询 ---------------------------------------------------------------

    def is_enabled(self, agent_name: str) -> bool:
        """
        检查指定 agent 是否启用。

        Args:
            agent_name: Agent 名称，如 "Explore"

        Returns:
            True 如果 enabled=true 或该 agent 未在配置中定义（默认启用）
        """
        if not self._loaded:
            self.load()
        cfg = self._data.get("agents", {}).get(agent_name, {})
        return cfg.get("enabled", True)

    def get_agent_config(self, agent_name: str) -> dict:
        """
        获取指定 agent 的完整配置。

        Args:
            agent_name: Agent 名称

        Returns:
            agent 配置字典，如果不存在返回空字典（使用类属性默认值）
        """
        if not self._loaded:
            self.load()
        return self._data.get("agents", {}).get(agent_name, {})

    def list_enabled(self) -> list[str]:
        """
        返回所有已启用的 agent 名称列表。

        Returns:
            Agent 名称列表
        """
        if not self._loaded:
            self.load()
        return [
            name for name, cfg in self._data.get("agents", {}).items()
            if cfg.get("enabled", True)
        ]

    def list_all(self) -> list[str]:
        """
        返回配置文件中定义的所有 agent 名称列表。

        Returns:
            Agent 名称列表
        """
        if not self._loaded:
            self.load()
        return list(self._data.get("agents", {}).keys())

    # -- 修改 ---------------------------------------------------------------

    def set_agent_config(self, agent_name: str, config: dict) -> None:
        """
        设置（覆盖）指定 agent 的配置。

        Args:
            agent_name: Agent 名称
            config:     配置字典
        """
        if "agents" not in self._data:
            self._data["agents"] = {}
        self._data["agents"][agent_name] = config
        self._save()

    def set_enabled(self, agent_name: str, enabled: bool) -> None:
        """
        设置 agent 的启用状态。

        Args:
            agent_name: Agent 名称
            enabled:    是否启用
        """
        if "agents" not in self._data:
            self._data["agents"] = {}
        if agent_name not in self._data["agents"]:
            self._data["agents"][agent_name] = {}
        self._data["agents"][agent_name]["enabled"] = enabled
        self._save()

    # -- 内部 ---------------------------------------------------------------

    def _ensure_dir(self) -> None:
        """确保配置文件所在目录存在。"""
        self._config_path.parent.mkdir(parents=True, exist_ok=True)

    def _save(self) -> None:
        """将当前配置保存到磁盘。"""
        self._ensure_dir()
        try:
            with open(self._config_path, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=4, ensure_ascii=False)
        except Exception as e:
            logger.error(
                "AgentConfig save failed | path={} err={}",
                self._config_path, e,
            )

    @property
    def loaded(self) -> bool:
        """配置是否已成功加载。"""
        return self._loaded

    @property
    def config_path(self) -> Path:
        """配置文件路径。"""
        return self._config_path


# ---------------------------------------------------------------------------
# Module-level 单例
# ---------------------------------------------------------------------------

_agent_config: AgentConfig | None = None


def agent_config() -> AgentConfig:
    """
    返回全局 AgentConfig 单例（延迟初始化）。

    Returns:
        AgentConfig 实例
    """
    global _agent_config
    if _agent_config is None:
        _agent_config = AgentConfig()
    return _agent_config
