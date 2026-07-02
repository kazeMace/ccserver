"""Persistent storage for Drama Engine Web sessions.

第一版使用 JSON 文件存储 session 元数据、seat 快照、事件回放和玩家 token。
它不持久化正在运行的 asyncio task / Director / LLM actor 内存；服务重启后，
可恢复 lobby/assigned/ended 等元数据和回放，running/paused 会降级为 assigned，
由 Host 决定是否重新 start。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

STORE_VERSION = 1


class JsonSessionStore:
    """基于目录的 JSON session store。"""

    def __init__(self, root: str | Path = "drama_engine/.runtime/session_store") -> None:
        """初始化存储目录。

        参数：
          root — 存储根目录。默认位于 drama_engine/.runtime/session_store，已被 .gitignore 忽略。
        """
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        logger.info("[JsonSessionStore] 使用目录：%s", self.root)

    def save_all(self, snapshot: dict[str, Any]) -> None:
        """保存完整 registry 快照。"""
        assert isinstance(snapshot, dict), "snapshot 必须是 dict"
        snapshot = dict(snapshot)
        snapshot["version"] = STORE_VERSION
        target = self.root / "registry.json"
        temp = self.root / "registry.json.tmp"
        temp.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(target)
        logger.debug("[JsonSessionStore] 已保存 registry 快照：%s", target)

    def load_all(self) -> dict[str, Any] | None:
        """读取完整 registry 快照；不存在时返回 None。"""
        target = self.root / "registry.json"
        if not target.exists():
            return None
        data = json.loads(target.read_text(encoding="utf-8"))
        assert isinstance(data, dict), "registry.json 顶层必须是 dict"
        version = int(data.get("version") or 0)
        assert version <= STORE_VERSION, f"不支持的 store version: {version}"
        return data
