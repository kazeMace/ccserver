"""文件系统脚本发现器。

扫描指定根目录，按约定识别脚本包（含 manifest.yaml 的目录）
和独立脚本文件（.yaml），返回路径列表。
"""

from __future__ import annotations

import logging
from pathlib import Path

from drama_engine.core.script_loader.base import BaseScriptDiscovery

logger = logging.getLogger(__name__)


class FileSystemDiscovery(BaseScriptDiscovery):
    """文件系统发现器：扫描指定根目录下的所有脚本。

    发现规则：
      1. 含 manifest.yaml 的目录 → 包目录（整个目录作为一条脚本）
      2. 不在包目录内的 .yaml 文件（排除 .preset.yaml）→ 独立脚本
      3. 按路径排序
    """

    def __init__(self, root: Path) -> None:
        """初始化发现器。

        参数:
            root: 脚本根目录
        """
        assert root.is_dir(), f"脚本根目录不存在: {root}"
        self._root = root

    async def discover(self) -> list[Path]:
        """异步发现所有可用脚本路径。

        返回:
            脚本路径列表（目录或单文件），按路径排序
        """
        package_dirs: set[Path] = set()
        scripts: list[Path] = []

        # 第一遍：找出所有包目录
        for manifest in self._root.rglob("manifest.yaml"):
            if manifest.name.startswith("._"):
                continue
            package_dirs.add(manifest.parent)

        # 包目录本身作为脚本条目
        for pkg_dir in sorted(package_dirs):
            scripts.append(pkg_dir)
            logger.debug("[FileSystemDiscovery] 发现包目录: %s", pkg_dir)

        # 第二遍：找出独立 yaml 文件（不在包目录内，不是 .preset.yaml）
        for yaml_file in sorted(self._root.rglob("*.yaml")):
            if yaml_file.name.startswith("._"):
                continue
            if yaml_file.name.endswith(".preset.yaml"):
                continue
            # 跳过包目录内的文件
            if any(self._is_inside(yaml_file, pkg) for pkg in package_dirs):
                continue
            scripts.append(yaml_file)
            logger.debug("[FileSystemDiscovery] 发现独立脚本: %s", yaml_file)

        return sorted(scripts)

    def _is_inside(self, path: Path, directory: Path) -> bool:
        """检查 path 是否在 directory 内。"""
        try:
            path.relative_to(directory)
            return True
        except ValueError:
            return False


__all__ = ["FileSystemDiscovery"]
