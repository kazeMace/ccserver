"""Stage and scope based message routing."""

from .cast import Cast
from .models import Scope
from .state import State


class Stage:
    """
    舞台 — Scope 路由器，负责把消息投递给正确的 Actor。

    Stage 本身不知道游戏规则，只按 Scope 的成员定义投递消息。
    私密消息（如狼人密谈）在路由层就过滤掉了，不靠 [X ONLY] 文本。
    """

    def __init__(self, scopes: list, cast: Cast):
        """
        初始化舞台。

        参数：
          scopes — Scope 对象列表
          cast   — Cast（演员池）实例，用于根据名字找 Actor
        """
        # scope 名 -> Scope 对象 的字典，方便快速查找
        self._scope_map: dict = {scope.name: scope for scope in scopes}
        self._cast = cast

        # 订阅表：Actor 名 -> 订阅的 scope 名集合
        # 开场时根据角色的 scopes 字段建立
        self._subscriptions: dict = {}

    def subscribe(self, actor_name: str, scope_names: list) -> None:
        """
        让某个 Actor 订阅若干 Scope。

        通常在开场发牌时调用：Actor 穿上某个 Role 的「戏服」后，
        自动订阅该角色 scopes 字段列出的所有可见域。

        参数：
          actor_name  — Actor 名字，如 "Player1"
          scope_names — 要订阅的 scope 名列表，如 ["public", "wolf-den"]
        """
        if actor_name not in self._subscriptions:
            self._subscriptions[actor_name] = set()
        for scope_name in scope_names:
            assert scope_name in self._scope_map, (
                f"Scope '{scope_name}' 未在 Script.scopes 中定义"
            )
            self._subscriptions[actor_name].add(scope_name)

        print(f"[Stage] {actor_name} 订阅了：{scope_names}")

    def get_scope(self, name: str) -> Scope:
        """
        按名字查找 Scope。

        参数：
          name — scope 名字，如 "wolf-den"

        返回：
          Scope 实例
        """
        assert name in self._scope_map, f"Scope '{name}' 不存在"
        return self._scope_map[name]

    async def deliver(
        self,
        msg: dict,
        scope_name: str,
        state: State,
        exclude: set = None,
    ) -> None:
        """
        把消息投递给指定 Scope 的所有成员。

        流程：
          1. 查找 Scope
          2. 调用 scope.members(state) 算当前成员（拉取式，死者自动不在）
          3. 找到每个成员的 Actor 对象
          4. 调用 Actor.perceive(msg) 存入其缓冲

        参数：
          msg        — 消息字典，格式：{"scope": str, "sender": str, "text": str}
          scope_name — 要投递到哪个 Scope，如 "wolf-den"
          state      — 当前世界状态（用于 Scope.members(state) 求值）
          exclude    — 要排除的 Actor 名集合（可选），如发言人不收到自己的消息
        """
        scope = self.get_scope(scope_name)
        members = scope.members(state)         # 调用 members 函数，现场求值

        if exclude is None:
            exclude = set()

        # 把消息里的 scope 信息填进去（方便 Actor 知道这是哪个信道的消息）
        tagged_msg = dict(msg)
        tagged_msg["scope"] = scope_name

        # 根据投递策略决定如何投递
        if scope.delivery == "immediate":
            # 立刻投递：每条消息到了就 perceive
            for name in members - exclude:
                actor = self._cast.get(name)
                await actor.perceive(tagged_msg)

        elif scope.delivery == "deferred":
            # 延迟投递：收集到 Stage 的暂存区，等本幕所有发言结束后统一 perceive
            # TODO v0.1+：实现 deferred 暂存区
            # 目前先用 immediate 代替（不影响功能正确性，只影响防跟票）
            for name in members - exclude:
                actor = self._cast.get(name)
                await actor.perceive(tagged_msg)

        print(
            f"[Stage] 投递到 '{scope_name}'，成员：{members - exclude}，"
            f"消息：{msg.get('text', '')[:50]}"
        )
