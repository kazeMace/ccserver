"""目录 Hook 扫描器 — 自动扫描 hooks/ 目录中的 .py 文件。

扫描规则：
  - 遍历 hooks/ 目录下所有 .py 文件（非递归）
  - 跳过 __init__.py 和以 _ 开头的文件
  - 从文件名推断事件名（如 on_session_start.py → "on_session_start"）
  - 校验事件名合法性（必须在 VALID_HOOK_EVENTS 中）
  - 检测文件中是否定义了 handle 函数
"""

from __future__ import annotations

import ast
import logging
from pathlib import Path

from drama_engine.core.script_loader.base import BaseHookScanner
from drama_engine.core.script_loader.models import HookSpec, VALID_HOOK_EVENTS

logger = logging.getLogger(__name__)


class DirectoryHookScanner(BaseHookScanner):
    """目录 Hook 扫描器：从文件名推断事件名，检测 handle 函数。"""

    HANDLE_FUNC_NAME = "handle"

    async def scan(self, hooks_dir: Path) -> list[HookSpec]:
        """扫描 hooks/ 目录，返回 hook 声明列表。

        参数:
            hooks_dir: hooks/ 目录路径

        返回:
            HookSpec 列表
        """
        if not hooks_dir.exists() or not hooks_dir.is_dir():
            return []

        specs: list[HookSpec] = []

        for py_file in sorted(hooks_dir.glob("*.py")):
            # 跳过 __init__.py 和私有文件
            if py_file.name.startswith("_"):
                continue

            # 从文件名推断事件名
            event_name = py_file.stem

            # 校验事件名合法性
            if event_name not in VALID_HOOK_EVENTS:
                logger.warning(
                    "[DirectoryHookScanner] 未知事件名 '%s'（文件: %s），跳过。"
                    " 合法值: %s",
                    event_name,
                    py_file.name,
                    sorted(VALID_HOOK_EVENTS),
                )
                continue

            # 检测 handle 函数
            if not self._has_handle_func(py_file):
                logger.warning(
                    "[DirectoryHookScanner] 文件 %s 缺少 %s 函数，跳过",
                    py_file.name,
                    self.HANDLE_FUNC_NAME,
                )
                continue

            spec = HookSpec(
                event=event_name,
                source="file",
                file_path=py_file,
            )
            specs.append(spec)
            logger.debug(
                "[DirectoryHookScanner] 发现 hook: %s → %s",
                py_file.name,
                event_name,
            )

        logger.info(
            "[DirectoryHookScanner] 扫描完成: %s, 发现 %d 个 hook",
            hooks_dir,
            len(specs),
        )
        return specs

    def _has_handle_func(self, py_file: Path) -> bool:
        """通过 AST 检测文件中是否定义了 handle 函数。"""
        try:
            source = py_file.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(py_file))
        except (SyntaxError, UnicodeDecodeError) as e:
            logger.warning(
                "[DirectoryHookScanner] 解析失败: %s, 错误: %s",
                py_file,
                e,
            )
            return False

        for node in ast.iter_child_nodes(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name == self.HANDLE_FUNC_NAME:
                    return True

        return False


__all__ = ["DirectoryHookScanner"]
