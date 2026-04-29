"""
extra_loader — 从工程目录和用户目录动态加载外置 Python 工具。

允许用户/工程在不修改 ccserver 源码的前提下，将自己的工具注入 Agent。
每个工具是一个 Python 文件（或目录），需满足以下任一格式：

  格式1：单文件，内含一个 BuiltinTools 子类
    .ccserver/tools/my_tool.py
      class MyTool(BuiltinTools):
          name = "MyTool"
          ...

  格式2：目录包，内含 tool.py，其中有 BuiltinTools 子类
    .ccserver/tools/my_tool/
      └── tool.py
            class MyTool(BuiltinTools):
                name = "MyTool"
                ...

扫描路径（按优先级从高到低，高优先级覆盖同名工具）：
    {project_root}/.ccserver/tools/       ← 工程级（最高优先级）
    ~/.ccserver/tools/                    ← 用户全局（次优先级）
    {project_root}/.agents/tools/        ← OpenClaw 兼容路径

用法：
    loader = ExtraToolLoader.from_workdir(project_root)
    for tool in loader.load_all():
        tool_manager.register_custom_tool(tool)
"""

from __future__ import annotations

import importlib.util
import inspect
import sys
from pathlib import Path

from loguru import logger

from ccserver.builtins.tools.base import BuiltinTools


class ExtraToolLoader:
    """
    扫描多个目录，动态 import Python 文件，提取 BuiltinTools 子类。

    设计原则：
    - 懒加载：只在 load_all() 调用时才真正 import
    - 同名覆盖：高优先级目录中发现的工具覆盖低优先级的同名工具
    - 容错：单个工具文件 import 失败时只记录日志，不影响其他工具
    - 无副作用：import 时不执行任何副作用代码，只提取类定义
    """

    def __init__(self, *tools_dirs: Path):
        """
        按优先级顺序传入扫描目录，前面的目录优先级更高。

        Args:
            *tools_dirs: 工具目录路径列表，按优先级从高到低排列。
        """
        # 工具名 → Python 源文件路径，高优先级先注册，低优先级不覆盖
        self._tool_files: dict[str, Path] = {}

        for tools_dir in tools_dirs:
            self._scan(tools_dir)

    @classmethod
    def from_workdir(
        cls,
        project_root: Path,
        global_config_dir: Path | None = None,
    ) -> "ExtraToolLoader":
        """
        根据项目根目录自动构建标准扫描路径。

        Args:
            project_root:      项目根目录（.ccserver/ 就在这里）。
            global_config_dir: 全局配置目录，默认 ~/.ccserver。

        Returns:
            ExtraToolLoader 实例，已完成目录扫描（但尚未 import）。
        """
        global_dir = global_config_dir or Path.home() / ".ccserver"
        project_root = Path(project_root)   # 兼容 str 类型的 project_root
        # 高优先级在前
        dirs = [
            project_root / ".ccserver" / "tools",    # 工程级
            project_root / ".agents" / "tools",      # OpenClaw 兼容路径
            global_dir / "tools",                    # 用户全局
        ]
        return cls(*dirs)

    # ── 扫描 ──────────────────────────────────────────────────────────────────

    def _scan(self, tools_dir: Path) -> None:
        """
        扫描一个目录，找出所有候选工具文件，低优先级不覆盖已发现的同名工具。

        Args:
            tools_dir: 待扫描的工具目录。
        """
        if not tools_dir.exists() or not tools_dir.is_dir():
            return

        for entry in sorted(tools_dir.iterdir()):
            if entry.is_file() and entry.suffix == ".py" and not entry.name.startswith("_"):
                # 格式1：单 py 文件，工具名 = 文件名（不含 .py）
                tool_key = entry.stem
                if tool_key not in self._tool_files:
                    self._tool_files[tool_key] = entry
                    logger.debug("ExtraToolLoader 发现工具文件 | key={} path={}", tool_key, entry)
                else:
                    logger.debug(
                        "ExtraToolLoader 跳过低优先级工具文件 | key={} path={}",
                        tool_key, entry,
                    )

            elif entry.is_dir() and not entry.name.startswith("_"):
                # 格式2：目录包，必须有 tool.py
                tool_py = entry / "tool.py"
                if tool_py.exists():
                    tool_key = entry.name
                    if tool_key not in self._tool_files:
                        self._tool_files[tool_key] = tool_py
                        logger.debug(
                            "ExtraToolLoader 发现工具包 | key={} path={}", tool_key, tool_py
                        )
                    else:
                        logger.debug(
                            "ExtraToolLoader 跳过低优先级工具包 | key={} path={}",
                            tool_key, tool_py,
                        )

    # ── 加载 ──────────────────────────────────────────────────────────────────

    def load_all(self) -> list[BuiltinTools]:
        """
        Import 所有已发现的工具文件，提取 BuiltinTools 子类并实例化。

        实例化规则：
        - 无参数构造函数（__init__ 只有 self）→ 直接 MyTool()
        - 有参数构造函数 → 跳过，记录警告（需要依赖注入时请改用 Plugin 系统）

        Returns:
            BuiltinTools 实例列表，每个工具一个实例。
        """
        tools: list[BuiltinTools] = []

        for key, src_path in self._tool_files.items():
            loaded = self._load_file(key, src_path)
            tools.extend(loaded)

        logger.info("ExtraToolLoader 加载完成 | count={}", len(tools))
        return tools

    def _load_file(self, key: str, src_path: Path) -> list[BuiltinTools]:
        """
        动态 import 单个 Python 文件，提取其中所有 BuiltinTools 子类并实例化。

        Args:
            key:      工具标识（用于 module 命名空间隔离）。
            src_path: Python 源文件绝对路径。

        Returns:
            从该文件中提取到的 BuiltinTools 实例列表。
        """
        module_name = f"_ccserver_extra_tool_{key}"

        # 避免重复 import
        if module_name in sys.modules:
            module = sys.modules[module_name]
        else:
            try:
                spec = importlib.util.spec_from_file_location(module_name, src_path)
                if spec is None or spec.loader is None:
                    logger.warning("ExtraToolLoader 无法创建 spec | path={}", src_path)
                    return []
                module = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = module
                spec.loader.exec_module(module)
            except Exception as e:
                logger.error(
                    "ExtraToolLoader import 失败 | key={} path={} error={}",
                    key, src_path, e,
                )
                return []

        # 提取模块中所有 BuiltinTools 子类（排除基类本身）
        instances: list[BuiltinTools] = []
        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if (
                inspect.isclass(attr)
                and issubclass(attr, BuiltinTools)
                and attr is not BuiltinTools
                and attr.__module__ == module_name  # 只取本文件定义的类，不取 import 进来的
            ):
                tool = self._try_instantiate(attr, src_path)
                if tool is not None:
                    instances.append(tool)
                    logger.info(
                        "ExtraToolLoader 加载工具 | name={} file={}",
                        tool.name, src_path.name,
                    )

        if not instances:
            logger.warning(
                "ExtraToolLoader 未在文件中找到 BuiltinTools 子类 | path={}", src_path
            )

        return instances

    def _try_instantiate(
        self, tool_cls: type, src_path: Path
    ) -> BuiltinTools | None:
        """
        尝试无参数实例化工具类。

        有参数的构造函数（需要依赖注入）暂不支持，跳过并记录警告。

        Args:
            tool_cls: BuiltinTools 子类。
            src_path: 源文件路径（用于日志）。

        Returns:
            工具实例，或 None（实例化失败时）。
        """
        try:
            sig = inspect.signature(tool_cls.__init__)
            # 过滤掉 self，检查是否有其他必填参数
            params = [
                p for name, p in sig.parameters.items()
                if name != "self" and p.default is inspect.Parameter.empty
            ]
            if params:
                logger.warning(
                    "ExtraToolLoader 跳过有参数构造函数的工具（需要依赖注入，请改用 Plugin 系统）"
                    " | class={} params={} file={}",
                    tool_cls.__name__,
                    [p.name for p in params],
                    src_path.name,
                )
                return None
            return tool_cls()
        except Exception as e:
            logger.error(
                "ExtraToolLoader 实例化失败 | class={} error={}", tool_cls.__name__, e
            )
            return None
