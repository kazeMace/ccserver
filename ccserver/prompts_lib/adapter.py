# prompts_lib/adapter.py
#
# 启动时自动扫描本目录下所有 manifest.json，动态注册 PromptLib。
# 新增 lib 只需在对应版本目录下放 manifest.json + lib.py，无需改动此文件。
#
# manifest.json 格式：
#   { "lib_id": "cc_reverse:v2.1.81", "class": "CcReverseV2181" }

import importlib
import json
from pathlib import Path

from ccserver.prompts_lib.base import PromptLib

# 注册表：lib_id → PromptLib 实例
_REGISTRY: dict[str, PromptLib] = {}

_PROMPTS_LIB_DIR = Path(__file__).parent


def get_lib(lib_id: str) -> PromptLib:
    """按 lib_id 获取 PromptLib 实例。lib_id 不存在时抛出 ValueError。"""
    if lib_id not in _REGISTRY:
        available = list(_REGISTRY.keys())
        raise ValueError(f"未知的 prompt lib: {lib_id!r}，可用：{available}")
    return _REGISTRY[lib_id]


def register(lib_id: str, lib: PromptLib) -> None:
    """手动注册一个 PromptLib 实例。"""
    _REGISTRY[lib_id] = lib


def _auto_register():
    """扫描所有 manifest.json，动态 import lib.py 并注册。"""
    for manifest_path in sorted(_PROMPTS_LIB_DIR.rglob("manifest.json")):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        lib_id = manifest["lib_id"]
        class_name = manifest["class"]

        # 将文件系统路径转为 Python 模块路径
        # 例：ccserver/prompts_lib/cc_reverse/v2_1_81/lib.py
        #   → ccserver.prompts_lib.cc_reverse.v2_1_81.lib
        lib_file = manifest_path.parent / "lib.py"
        rel = lib_file.relative_to(Path(__file__).parent.parent.parent)
        module_path = ".".join(rel.with_suffix("").parts)

        module = importlib.import_module(module_path)
        cls = getattr(module, class_name)
        _REGISTRY[lib_id] = cls()


_auto_register()
