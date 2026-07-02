# drama_engine/components/scope_types.py
"""
扩展 Scope 成员函数。

engine.py 的 Scope.members 是 Callable[[State], set[str]]。
本模块提供两种特殊 Scope 的成员函数工厂：

  1. self
     - 用途：忏悔室、独白等「只有一个人」的场景
     - 返回值：只有当前 actor 自己
     - 标记：GAME.__current_actor（由编译器生成的 pre_scene_hook 写入）

  2. dynamic_whisper
     - 用途：双人私聊、窃窃私语等「两个人的对话」场景
     - 返回值：当前 actor + 对话对象
     - 标记：GAME.__current_actor + GAME.__dynamic_whisper_target

临时标记约定：
  GAME.__current_actor          — 当前正在发言的 actor 名
  GAME.__dynamic_whisper_target — dynamic_whisper 的对话对象

这两个标记由编译器生成的 pre_scene_hook 写入，scene 结束后清理。
"""

from __future__ import annotations
from drama_engine.core.engine import State


def make_self_scope_members():
    """
    创建 self scope 的成员函数工厂。

    self scope 用于「只有一个人」的场景，如忏悔室独白。
    成员函数返回 GAME.__current_actor 指向的玩家集合（只有一个元素）。

    返回值：
      Callable[[State], set[str]] — 成员函数，接收 state，返回成员名集合
    """

    def members(state: State) -> set:
        """
        求 self scope 的当前成员。

        参数：
          state — 游戏状态

        返回：
          set[str] — self scope 的成员集合，如 {"P1"}；
                     如果没有设置 __current_actor，返回空集
        """
        # 读取当前 actor
        actor = state.get_attr("GAME", "__current_actor")

        # 如果没有设置 actor，返回空集
        if actor is None:
            return set()

        # 否则返回只包含当前 actor 的集合
        return {actor}

    return members


def make_dynamic_whisper_members():
    """
    创建 dynamic_whisper scope 的成员函数工厂。

    dynamic_whisper scope 用于「两个人的对话」场景，如私聊、双人协商。
    成员函数返回 GAME.__current_actor 和 GAME.__dynamic_whisper_target。

    如果 actor 或 target 其中一个为 None，则该方移出成员集合。
    如果都为 None，返回空集。

    返回值：
      Callable[[State], set[str]] — 成员函数，接收 state，返回成员名集合
    """

    def members(state: State) -> set:
        """
        求 dynamic_whisper scope 的当前成员。

        参数：
          state — 游戏状态

        返回：
          set[str] — dynamic_whisper scope 的成员集合，如 {"P1", "P2"}；
                     如果只有一方存在，返回只包含该方的集合；
                     如果都不存在，返回空集
        """
        # 读取 actor 和 target
        actor = state.get_attr("GAME", "__current_actor")
        target = state.get_attr("GAME", "__dynamic_whisper_target")

        # 构建结果集合
        result = set()

        # 如果 actor 存在，加入集合
        if actor is not None:
            result.add(actor)

        # 如果 target 存在且不同于 actor，加入集合
        # （使用 set 的特性，重复添加同一元素会自动去重）
        if target is not None:
            result.add(target)

        return result

    return members
