"""H3：编译期 effect.type 静态校验测试。

覆盖：
  1. 内置 effect 拼写错误 → 编译期报错
  2. game_pack 机制 effect（tally_votes 等）纳入白名单 → 通过
  3. 声明了 game_pack 但 effect 拼错（tally_vote）→ 仍报错（不再整体豁免）
  4. 嵌套 effect（for_each.effects）内的拼写错误 → 递归校验报错
  5. 声明 plugins: 的自定义 effect → 宽容放行（编译期无法解析）
"""

from __future__ import annotations

import pytest

from drama_engine.core.runtime.interactive_session.compiler import InteractiveSessionCompiler


def _base(scene_effects: list, extra_top: dict | None = None) -> dict:
    """构造最小可编译脚本，resolution.effects 由参数注入。"""
    doc = {
        "runtime": {"type": "interactive_session"},
        "players": {"ids": ["P1"]},
        "flow": {"type": "sequence", "scenes": ["s"]},
        "scenes": {
            "s": {
                "participants": {"static": ["P1"]},
                "schedule": {"mode": "none"},
                "resolution": {"effects": scene_effects},
            }
        },
    }
    if extra_top:
        doc.update(extra_top)
    return doc


def test_misspelled_builtin_effect_rejected_at_compile_time():
    """纯脚本里拼错内置 effect（set_stat）→ 编译期报错。"""
    compiler = InteractiveSessionCompiler()
    errors = compiler.validate(_base([{"type": "set_stat", "path": "GAME.x", "value": 1}]))
    assert errors, "拼错的 set_stat 应被编译期拒绝"
    assert "set_stat" in errors[0]


def test_valid_builtin_effect_passes():
    """正确的内置 effect（set_state）→ 通过。"""
    compiler = InteractiveSessionCompiler()
    errors = compiler.validate(_base([{"type": "set_state", "path": "GAME.x", "value": 1}]))
    assert not errors


def test_game_pack_mechanism_effect_whitelisted():
    """声明 builtin.social 后，tally_votes/eliminate 纳入白名单 → 通过。"""
    compiler = InteractiveSessionCompiler()
    doc = _base(
        [{"type": "tally_votes", "field": "vote", "to": "GAME.t"}, {"type": "eliminate"}],
        extra_top={"game_pack": {"plugin": "builtin.social"}},
    )
    errors = compiler.validate(doc)
    assert not errors, f"game_pack 机制 effect 应通过，却报错: {errors}"


def test_typo_in_game_pack_script_still_rejected():
    """声明 game_pack 但拼错机制名（tally_vote）→ 仍报错，不再整体豁免。"""
    compiler = InteractiveSessionCompiler()
    doc = _base(
        [{"type": "tally_vote", "field": "vote"}],  # 少了 s
        extra_top={"game_pack": {"plugin": "builtin.social"}},
    )
    errors = compiler.validate(doc)
    assert errors, "game_pack 脚本里拼错的机制名应被拒绝（H3 核心：不再 all-or-nothing 豁免）"
    assert "tally_vote" in errors[0]


def test_nested_effect_typo_rejected():
    """for_each 嵌套子 effect 里的拼写错误 → 递归校验报错。"""
    compiler = InteractiveSessionCompiler()
    doc = _base([
        {
            "type": "for_each",
            "items": {"ref": "GAME.players"},
            "effects": [{"type": "set_stat", "path": "GAME.x", "value": 1}],  # 拼错
        }
    ])
    errors = compiler.validate(doc)
    assert errors, "嵌套 effect 里的 typo 应被递归校验抓住"
    assert "set_stat" in errors[0]


def test_plugins_declaration_enables_lenient_mode():
    """声明 plugins: 的自定义 effect → 宽容放行（编译期无法静态解析）。"""
    compiler = InteractiveSessionCompiler()
    doc = _base(
        [{"type": "my_custom_effect", "foo": 1}],
        extra_top={"plugins": [{"module": "my_game.plugins", "register": "register"}]},
    )
    errors = compiler.validate(doc)
    assert not errors, "声明 plugins 时自定义 effect 应宽容放行"
