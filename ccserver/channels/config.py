"""
channels/config — channels.json 配置管理。

设计目标
────────
- 读取 .ccserver/channels.json 配置文件
- 提供统一的配置访问接口
- 支持默认值和配置校验
- 与 ChannelGateway 联动：自动启动 enabled + auto_start 的 channel

配置文件格式
────────────
.cserver/channels.json:
    {
        "channels": {
            "discord": {
                "enabled": true,
                "auto_start": true,
                "accounts": {
                    "default": {
                        "token": "BOT_TOKEN_HERE"
                    }
                }
            },
            "imessage": {
                "enabled": false,
                "auto_start": false
            }
        }
    }

与 OpenClaw 的对应关系
──────────────────────
ChannelConfig        → OpenClaw 的 channel 配置结构
"""

import json
import os
from pathlib import Path
from typing import Optional

from loguru import logger


# ── ChannelConfig ─────────────────────────────────────────────────────────────


class ChannelConfig:
    """
    channels.json 配置管理器。

    负责读取、解析和提供 channel 配置。
    配置文件路径：.ccserver/channels.json（相对于项目目录）

    Attributes:
        _config_path: 配置文件绝对路径
        _data:        配置数据字典
        _loaded:      是否已成功加载
    """

    DEFAULT_CONFIG = {
        "channels": {}
    }

    def __init__(self, config_path: Optional[str] = None):
        """
        初始化配置管理器。

        Args:
            config_path: 配置文件路径，默认从环境变量 CCSERVER_PROJECT_DIR
                         推导为 .ccserver/channels.json
        """
        if config_path:
            self._config_path = Path(config_path).resolve()
        else:
            # 从环境变量推导
            project_dir = os.environ.get("CCSERVER_PROJECT_DIR", ".")
            self._config_path = Path(project_dir) / ".ccserver" / "channels.json"

        self._data: dict = {}
        self._loaded = False

        logger.debug(
            "ChannelConfig initialized | path={}",
            self._config_path,
        )

    # ── 加载 ────────────────────────────────────────────────────────────────────

    def load(self) -> bool:
        """
        从磁盘加载配置文件。

        如果文件不存在，创建默认配置文件并返回 False。

        Returns:
            True 如果成功加载，False 如果文件不存在或解析失败
        """
        if not self._config_path.exists():
            logger.info(
                "Channel config not found, creating default | path={}",
                self._config_path,
            )
            self._ensure_dir()
            self._data = dict(self.DEFAULT_CONFIG)
            self._save()
            self._loaded = True
            return False

        try:
            with open(self._config_path, "r", encoding="utf-8") as f:
                self._data = json.load(f)
            self._loaded = True
            logger.info(
                "Channel config loaded | path={} channels={}",
                self._config_path,
                list(self._data.get("channels", {}).keys()),
            )
            return True
        except json.JSONDecodeError as e:
            logger.error(
                "Channel config JSON error | path={} err={}",
                self._config_path, e,
            )
            self._data = dict(self.DEFAULT_CONFIG)
            return False
        except Exception as e:
            logger.error(
                "Channel config load failed | path={} err={}",
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
        logger.info("Channel config reloading...")
        self._loaded = False
        return self.load()

    # ── 查询 ────────────────────────────────────────────────────────────────────

    def get_channel_config(self, channel_id: str) -> dict:
        """
        获取指定 channel 的配置。

        Args:
            channel_id: channel ID，如 "discord"

        Returns:
            channel 配置字典，如果不存在返回空字典
        """
        return self._data.get("channels", {}).get(channel_id, {})

    def is_enabled(self, channel_id: str) -> bool:
        """
        检查 channel 是否启用。

        默认：enabled 不存在时视为 False（安全默认）

        Args:
            channel_id: channel ID

        Returns:
            True 如果 enabled=true
        """
        cfg = self.get_channel_config(channel_id)
        return bool(cfg.get("enabled", False))

    def should_auto_start(self, channel_id: str) -> bool:
        """
        检查 channel 是否应该自动启动。

        条件：enabled=True 且 auto_start=True（或 auto_start 不存在时默认 True）

        Args:
            channel_id: channel ID

        Returns:
            True 如果应该自动启动
        """
        cfg = self.get_channel_config(channel_id)
        if not cfg.get("enabled", False):
            return False
        return cfg.get("auto_start", True)

    def get_accounts(self, channel_id: str) -> dict:
        """
        获取指定 channel 的所有账户配置。

        Args:
            channel_id: channel ID

        Returns:
            account_id -> config 的映射字典
        """
        cfg = self.get_channel_config(channel_id)
        return cfg.get("accounts", {})

    def get_account_config(self, channel_id: str, account_id: str) -> dict:
        """
        获取指定 channel + account 的配置。

        Args:
            channel_id: channel ID
            account_id: 账户标识，如 "default"

        Returns:
            账户配置字典，如果不存在返回空字典
        """
        accounts = self.get_accounts(channel_id)
        return accounts.get(account_id, {})

    def list_channels(self) -> list[str]:
        """
        列出配置文件中定义的所有 channel ID。

        Returns:
            channel ID 列表
        """
        return list(self._data.get("channels", {}).keys())

    def list_auto_start(self) -> list[tuple[str, str, dict]]:
        """
        列出所有应该自动启动的 channel + account。

        Returns:
            [(channel_id, account_id, config), ...]
        """
        result = []
        for channel_id in self.list_channels():
            if not self.should_auto_start(channel_id):
                continue
            accounts = self.get_accounts(channel_id)
            if not accounts:
                # 没有配置账户，跳过
                continue
            for account_id, account_cfg in accounts.items():
                result.append((channel_id, account_id, account_cfg))
        return result

    # ── 修改 ────────────────────────────────────────────────────────────────────

    def set_channel_config(self, channel_id: str, config: dict) -> None:
        """
        设置（覆盖）指定 channel 的配置。

        Args:
            channel_id: channel ID
            config:     配置字典
        """
        if "channels" not in self._data:
            self._data["channels"] = {}
        self._data["channels"][channel_id] = config
        self._save()

    def set_account_config(self, channel_id: str, account_id: str, config: dict) -> None:
        """
        设置指定 channel + account 的配置。

        Args:
            channel_id: channel ID
            account_id: 账户标识
            config:     账户配置字典
        """
        if "channels" not in self._data:
            self._data["channels"] = {}
        if channel_id not in self._data["channels"]:
            self._data["channels"][channel_id] = {}
        if "accounts" not in self._data["channels"][channel_id]:
            self._data["channels"][channel_id]["accounts"] = {}
        self._data["channels"][channel_id]["accounts"][account_id] = config
        self._save()

    # ── 内部 ────────────────────────────────────────────────────────────────────

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
                "Channel config save failed | path={} err={}",
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
