"""Cast container for runtime actors."""

from .actors import AgentActor
from .models import PlayerConfig


class Cast:
    """
    演员池 — 管理本剧目所有 Actor 实例的生命周期。

    设计原则：Cast 只做「名字 → Actor 对象」的查找，不知道规则，不管生死。
    「谁活着」「谁是狼人」是 State + 查询函数的事。

    好处：
      - 猎人死后还能开枪（不从 Cast 删除死人，State 里标记 alive=False）
      - 未来真人替演：只需替换 _actors[名字]，State/查询/Director 全不知情
    """

    def __init__(self):
        """初始化空演员池。"""
        # 演员名 -> Actor 对象 的字典
        self._actors: dict = {}

    def add(self, actor: AgentActor) -> None:
        """
        把一个 Actor 加入演员池。

        参数：
          actor — AgentActor 实例
        """
        assert actor.name not in self._actors, (
            f"演员 '{actor.name}' 已存在，不能重复添加"
        )
        self._actors[actor.name] = actor
        print(f"[Cast] 加入演员池：{actor.name}")

    def apply_player_config(self, player_config: PlayerConfig) -> None:
        """
        把剧本中的玩家席位资料挂到对应 Actor 上，供 debug/UI 展示。

        这些字段不是规则权威来源；规则仍应读 State。
        """
        for player_id in self.all_names():
            actor = self.get(player_id)
            if hasattr(actor, "set_player_profile"):
                actor.set_player_profile(
                    player_id=player_id,
                    display_name=player_config.display_names.get(player_id, player_id),
                    nickname=player_config.nicknames.get(player_id, ""),
                )

    def get(self, name: str) -> AgentActor:
        """
        按名字查找 Actor。

        参数：
          name — Actor 名字，如 "Player1"

        返回：
          AgentActor 实例

        异常：
          AssertionError — 名字不存在时
        """
        assert name in self._actors, f"演员池中找不到 '{name}'"
        return self._actors[name]

    def resolve(self, names) -> list:
        """
        把名字集合转成 Actor 对象列表。

        参数：
          names — 名字集合或列表，如 {"Player1", "Player3"}

        返回：
          AgentActor 对象列表
        """
        return [self._actors[name] for name in names if name in self._actors]

    def all_names(self) -> list:
        """返回所有演员名字列表。"""
        return list(self._actors.keys())
