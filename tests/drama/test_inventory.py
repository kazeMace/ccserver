import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
from drama_engine.core.engine import State, StateWriter, SetAttr, Vocabulary
from drama_engine.core.components.inventory import InventoryManager

_EMPTY_VOCAB = Vocabulary(roles=frozenset(), factions=frozenset(), scopes=frozenset(), abilities=frozenset())
manager = InventoryManager()

def _make_state():
    state = State(_EMPTY_VOCAB)
    state.register_entity("GAME", {})
    return state

def test_init_inventory():
    state = _make_state()
    state.register_entity("P1", {"alive": True})
    writer = StateWriter(state)
    inventory_spec = [
        {
            "item": "heal_potion",
            "display_name": "解药",
            "description": "一次性道具，可在夜晚救活当晚被狼人袭击的玩家。",
            "count": 1,
        },
        {
            "item": "poison_potion",
            "display_name": "毒药",
            "description": "一次性道具，可在夜晚毒死一名存活玩家。",
            "count": 1,
        },
    ]
    manager.init_for_actor("P1", inventory_spec, state, writer)
    assert state.get_attr("P1", "inventory_heal_potion") == 1
    assert state.get_attr("P1", "inventory_poison_potion") == 1

def test_init_unlimited_inventory():
    state = _make_state()
    state.register_entity("P1", {"alive": True})
    writer = StateWriter(state)
    manager.init_for_actor("P1", [{"item": "wolf_vote", "count": "unlimited"}], state, writer)
    assert state.get_attr("P1", "inventory_wolf_vote") == "unlimited"
