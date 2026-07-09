"""目录插件扫描器 — 自动扫描 plugins/ 目录中的 .py 文件。

扫描规则：
  - 遍历 plugins/ 目录下所有 .py 文件（非递归）
  - 跳过 __init__.py 和以 _ 开头的文件
  - 检测文件中是否定义了 register 函数
  - 有 register 函数的文件 → 生成 PluginSpec(source="directory_file")
"""

from __future__ import annotations

import ast
import logging
from pathlib import Path

from drama_engine.core.script_loader.base import BasePluginScanner
from drama_engine.core.script_loader.models import PluginSpec

logger = logging.getLogger(__name__)


class DirectoryPluginScanner(BasePluginScanner):
    """目录插件扫描器：自动扫描 .py 文件中的 register 函数。"""

    def __init__(self, register_func_name: str = "register") -> None:
        """初始化扫描器。

        参数:
            register_func_name: 要检测的注册函数名，默认 "register"
        """
        self._register_func_name = register_func_name

    async def scan(self, plugins_dir: Path) -> list[PluginSpec]:
        """扫描 plugins/ 目录，返回插件声明列表。

        参数:
            plugins_dir: plugins/ 目录路径

        返回:
            PluginSpec 列表（仅包含含 register 函数的文件）
        """
        if not plugins_dir.exists() or not plugins_dir.is_dir():
            return []

        specs: list[PluginSpec] = []

        for py_file in sorted(plugins_dir.glob("*.py")):
            # 跳过 __init__.py 和私有文件
            if py_file.name.startswith("_"):
                continue

            # 检测文件中是否有 register 函数
            if self._has_register_func(py_file):
                spec = PluginSpec(
                    source="directory_file",
                    file_path=py_file,
                    register_func=self._register_func_name,
                )
                specs.append(spec)
                logger.debug(
                    "[DirectoryPluginScanner] 发现插件: %s", py_file.name
                )
            else:
                logger.debug(
                    "[DirectoryPluginScanner] 跳过（无 %s 函数）: %s",
                    self._register_func_name,
                    py_file.name,
                )

        logger.info(
            "[DirectoryPluginScanner] 扫描完成: %s, 发现 %d 个插件",
            plugins_dir,
            len(specs),
        )
        return specs

    def _has_register_func(self, py_file: Path) -> bool:
        """通过 AST 检测文件中是否定义了 register 函数。

        使用 AST 而非 import，避免副作用。
        """
        try:
            source = py_file.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(py_file))
        except (SyntaxError, UnicodeDecodeError) as e:
            logger.warning(
                "[DirectoryPluginScanner] 解析失败: %s, 错误: %s",
                py_file,
                e,
            )
            return False

        # 检查顶层是否有名为 register_func_name 的函数定义
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name == self._register_func_name:
                    return True

        return False


__all__ = ["DirectoryPluginScanner"]
