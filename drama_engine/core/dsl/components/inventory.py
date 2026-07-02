# drama_engine/components/inventory.py
"""
道具系统：初始化玩家道具栏。

道具数量存储约定：
  - 属性名格式：inventory_{item_name}
  - 属性值类型：
    - int  ：可用数量
    - str "unlimited" ：无限数量
"""

from __future__ import annotations
from drama_engine.core.engine import State, StateWriter, SetAttr


class InventoryManager:
    """
    道具系统：把角色 inventory 声明写入 State。

    约定：道具数量存储为 state.get_attr(entity, f"inventory_{item_name}")
         值类型：
           - int（可用数量）
           - "unlimited"（无限，表示无限可用）
    """

    def init_for_actor(
        self,
        actor: str,
        inventory_spec: list,
        state: State,
        writer: StateWriter
    ) -> None:
        """
        根据角色 inventory 声明初始化玩家道具栏。

        参数：
          actor           — 角色实体名
          inventory_spec  — 道具列表，每项为 {"item": name, "count": int|"unlimited", ...}
          state           — 当前游戏状态
          writer          — StateWriter 实例，用于应用状态变更

        示例：
          inventory_spec = [
            {"item": "heal_potion", "display_name": "解药", "count": 1},
            {"item": "poison_potion", "display_name": "毒药", "count": 1},
            {"item": "wolf_vote", "count": "unlimited"},
          ]
          manager.init_for_actor("Player_1", inventory_spec, state, writer)
          # 结果：
          #   state.get_attr("Player_1", "inventory_heal_potion") == 1
          #   state.get_attr("Player_1", "inventory_poison_potion") == 1
          #   state.get_attr("Player_1", "inventory_wolf_vote") == "unlimited"
        """
        for item_spec in inventory_spec:
            item = item_spec["item"]
            count = item_spec.get("count", 1)
            attr_name = f"inventory_{item}"
            writer.apply(SetAttr(actor, attr_name, count))
