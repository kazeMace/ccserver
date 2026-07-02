"""
defaults — 配置默认值的集中视图。

真正的默认值定义在 schema.py 各 dataclass 的字段默认里（单一真相源）。
本模块把它们导出为一个 dict，供 doc_gen 渲染文档、或需要纯数据默认值的场景使用。
"""

from __future__ import annotations

from .schema import CcServerConfig


def default_config_dict() -> dict:
    """返回全默认配置的 as_dict 形态（每次调用新建，避免共享可变状态）。"""
    return CcServerConfig().as_dict()


# 只读快照（模块级，便于直接引用）。需要可变副本时调用 default_config_dict()。
DEFAULTS: dict = default_config_dict()
