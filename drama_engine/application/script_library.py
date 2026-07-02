"""Built-in Drama Engine script library paths.

本模块集中管理内置游戏脚本和 preset 的目录规则，避免运行器、目录和管理台
各自硬编码不同路径。
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


DRAMA_ENGINE_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = DRAMA_ENGINE_ROOT.parent
SCRIPT_LIBRARY_ROOT = DRAMA_ENGINE_ROOT / "scripts"
PRESET_LIBRARY_ROOT = SCRIPT_LIBRARY_ROOT / "presets"


def iter_builtin_script_paths(root: str | Path | None = None) -> list[Path]:
    """Return built-in game DSL YAML paths under the script library.

    参数:
      root: 可选脚本库根目录。为空时使用 ``drama_engine/scripts``。

    返回:
      按相对路径排序后的 YAML 剧本文件列表，不包含 ``*.preset.yaml``。
    """
    base = Path(root) if root is not None else SCRIPT_LIBRARY_ROOT
    if not base.exists():
        logger.info("[ScriptLibrary] script root does not exist: %s", base)
        return []
    result = []
    for path in sorted(base.rglob("*.yaml")):
        if path.name.startswith("._") or path.name.endswith(".preset.yaml"):
            continue
        result.append(path)
    return result


def iter_builtin_preset_paths(root: str | Path | None = None) -> list[Path]:
    """Return built-in preset YAML paths under the script library.

    参数:
      root: 可选 preset 根目录。为空时使用 ``drama_engine/scripts/presets``。

    返回:
      按相对路径排序后的 ``*.preset.yaml`` 文件列表。
    """
    base = Path(root) if root is not None else PRESET_LIBRARY_ROOT
    if not base.exists():
        logger.info("[ScriptLibrary] preset root does not exist: %s", base)
        return []
    return [path for path in sorted(base.rglob("*.preset.yaml")) if not path.name.startswith("._")]
