"""Script DSL 插件注册表与通用视图事件。

本模块只定义小而稳定的扩展接口。插件可以扩展规则、条件、值解析和视图投影，
但不能直接接管 Director 主循环。会修改 State 的能力必须通过 StateWriter 执行。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations
from typing import Any, Callable

from drama_engine.core.engine import SetAttr
from drama_engine.core.dsl.components.value_resolver import ValueResolver


@dataclass
class EffectContext:
    """运行 effect handler 时可见的最小上下文。"""

    state: Any
    writer: Any
    actor: str | None
    responses: list
    scene_name: str
    extra: dict


@dataclass
class ViewContext:
    """运行 view projector 时可见的最小上下文。"""

    state: Any
    scene_name: str
    audience: str
    mutation_log: list
    script_extensions: dict = field(default_factory=dict)


@dataclass
class RuleSetContext:
    """运行 rule_set handler 时可见的最小上下文。"""

    state: Any
    writer: Any
    responses: list
    rule_set: dict
    effect: dict
    extra: dict = field(default_factory=dict)


@dataclass
class ViewEvent:
    """后端发给前端 ViewHost 的结构化展示事件。"""

    view_id: str
    view_kind: str
    title: str
    audience: str
    data: dict
    private: bool = False
    priority: int = 0
    layout: dict = field(default_factory=dict)
    meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """转为可 JSON 序列化的事件字典。"""
        assert self.view_id, "ViewEvent.view_id 不能为空"
        assert self.view_kind, "ViewEvent.view_kind 不能为空"
        assert self.audience, "ViewEvent.audience 不能为空"
        return {
            "kind": "__view__",
            "view_id": self.view_id,
            "view_kind": self.view_kind,
            "title": self.title or self.view_id,
            "audience": self.audience,
            "private": self.private,
            "priority": self.priority,
            "layout": self.layout or {},
            "data": self.data or {},
            "meta": self.meta or {},
        }


class PluginRegistry:
    """插件能力注册表。核心引擎依赖该抽象，而不是依赖具体插件。"""

    def __init__(self) -> None:
        self._effects: dict[str, Callable[[dict, EffectContext], None]] = {}
        self._conditions: dict[str, Callable[[dict, Any], bool]] = {}
        self._value_resolvers: dict[str, Callable[[str, Any], Any]] = {}
        self._view_projectors: dict[str, Callable[[dict, ViewContext], ViewEvent | dict | None]] = {}
        self._rule_set_handlers: dict[str, Callable[[RuleSetContext], dict | None]] = {}
        self._validators: list[Callable[[dict], list[str]]] = []

    def register_effect(self, name: str, handler: Callable[[dict, EffectContext], None]) -> None:
        """注册一个 effect handler。"""
        assert name and isinstance(name, str), "effect 名称必须是非空字符串"
        assert callable(handler), f"effect handler 不可调用: {name}"
        self._effects[name] = handler

    def has_effect(self, name: str) -> bool:
        """检查是否存在指定 effect。"""
        return name in self._effects

    def execute_effect(self, effect: dict, context: EffectContext) -> bool:
        """执行插件 effect。返回 True 表示已处理。"""
        effect_type = effect.get("type")
        handler = self._effects.get(effect_type)
        if handler is None:
            return False
        handler(effect, context)
        return True

    def register_condition(self, name: str, handler: Callable[[dict, Any], bool]) -> None:
        """注册一个 condition handler。"""
        assert name and isinstance(name, str), "condition 名称必须是非空字符串"
        assert callable(handler), f"condition handler 不可调用: {name}"
        self._conditions[name] = handler

    def evaluate_condition(self, name: str, spec: dict, context: Any) -> bool:
        """执行插件 condition。"""
        handler = self._conditions.get(name)
        if handler is None:
            raise ValueError(f"未知 plugin condition: {name}")
        return bool(handler(spec, context))

    def has_condition(self, name: str) -> bool:
        """检查是否存在指定 condition。"""
        return name in self._conditions

    def register_value_resolver(self, prefix: str, resolver: Callable[[str, Any], Any]) -> None:
        """注册一个值解析前缀。"""
        assert prefix and isinstance(prefix, str), "resolver prefix 必须是非空字符串"
        assert callable(resolver), f"value resolver 不可调用: {prefix}"
        self._value_resolvers[prefix] = resolver

    def resolve_value(self, ref: str, context: Any) -> Any:
        """按 prefix 执行插件值解析器。"""
        prefix, sep, rest = ref.partition(":")
        if not sep:
            raise ValueError(f"插件值引用必须是 prefix:path 格式: {ref}")
        resolver = self._value_resolvers.get(prefix)
        if resolver is None:
            raise ValueError(f"未知 plugin value resolver: {prefix}")
        return resolver(rest, context)

    def has_value_resolver(self, prefix: str) -> bool:
        """检查是否存在指定值解析前缀。"""
        return prefix in self._value_resolvers

    def register_view_projector(
        self,
        name: str,
        projector: Callable[[dict, ViewContext], ViewEvent | dict | None],
    ) -> None:
        """注册一个 ViewProjector。"""
        assert name and isinstance(name, str), "view projector 名称必须是非空字符串"
        assert callable(projector), f"view projector 不可调用: {name}"
        self._view_projectors[name] = projector

    def project_view(self, spec: dict, context: ViewContext) -> dict | None:
        """把 publication.views 条目投影为 ViewEvent 字典。"""
        assert isinstance(spec, dict), "view spec 必须是字典"
        projector_name = spec.get("projector") or "core.views.inline"
        projector = self._view_projectors.get(projector_name)
        if projector is None:
            raise ValueError(f"未知 view projector: {projector_name}")
        event = projector(spec, context)
        if event is None:
            return None
        if isinstance(event, ViewEvent):
            return event.to_dict()
        assert isinstance(event, dict), f"ViewProjector 必须返回 ViewEvent 或 dict，收到 {type(event)}"
        event.setdefault("kind", "__view__")
        return event

    def register_rule_set_handler(
        self,
        plugin: str,
        handler: Callable[[RuleSetContext], dict | None],
    ) -> None:
        """注册 rule_set handler。"""
        assert plugin and isinstance(plugin, str), "rule_set plugin 必须是非空字符串"
        assert callable(handler), f"rule_set handler 不可调用: {plugin}"
        self._rule_set_handlers[plugin] = handler

    def has_rule_set_handler(self, plugin: str) -> bool:
        """检查是否存在 rule_set handler。"""
        return plugin in self._rule_set_handlers

    def apply_rule_set(self, context: RuleSetContext) -> dict | None:
        """执行 rule_set handler。"""
        plugin = context.rule_set.get("plugin") if isinstance(context.rule_set, dict) else None
        handler = self._rule_set_handlers.get(plugin)
        if handler is None:
            raise ValueError(f"未知 rule_set handler: {plugin}")
        return handler(context)

    def register_validator(self, validator: Callable[[dict], list[str]]) -> None:
        """注册一个剧本校验器。"""
        assert callable(validator), "validator 必须可调用"
        self._validators.append(validator)

    def validate(self, doc: dict) -> list[str]:
        """运行所有插件校验器。"""
        errors: list[str] = []
        for validator in self._validators:
            result = validator(doc)
            if result:
                errors.extend(result)
        return errors


class PluginApi:
    """暴露给插件的窄接口。"""

    def __init__(self, registry: PluginRegistry) -> None:
        self._registry = registry

    def register_effect(self, name: str, handler: Callable[[dict, EffectContext], None]) -> None:
        """注册 effect handler。"""
        self._registry.register_effect(name, handler)

    def register_condition(self, name: str, handler: Callable[[dict, Any], bool]) -> None:
        """注册 condition handler。"""
        self._registry.register_condition(name, handler)

    def register_value_resolver(self, prefix: str, resolver: Callable[[str, Any], Any]) -> None:
        """注册 value resolver。"""
        self._registry.register_value_resolver(prefix, resolver)

    def register_view_projector(
        self,
        name: str,
        projector: Callable[[dict, ViewContext], ViewEvent | dict | None],
    ) -> None:
        """注册 view projector。"""
        self._registry.register_view_projector(name, projector)

    def register_rule_set_handler(
        self,
        plugin: str,
        handler: Callable[[RuleSetContext], dict | None],
    ) -> None:
        """注册 rule_set handler。"""
        self._registry.register_rule_set_handler(plugin, handler)

    def register_validator(self, validator: Callable[[dict], list[str]]) -> None:
        """注册剧本校验器。"""
        self._registry.register_validator(validator)


class CoreViewsPlugin:
    """内置通用看板插件。只投影视图，不修改游戏状态。"""

    def register(self, api: PluginApi) -> None:
        """注册内置 projector。"""
        api.register_view_projector("core.views.inline", self._project_inline)
        api.register_view_projector("core.views.state_attr", self._project_state_attr)

    def _project_inline(self, spec: dict, context: ViewContext) -> ViewEvent:
        """把 view spec 中的 data 直接解析为 ViewEvent。"""
        data_spec = spec.get("data")
        if data_spec is None:
            data_spec = self._collect_convenience_data(spec)
        data = self._resolve_view_data(data_spec or {}, context)
        return self._build_event(spec, context, data if isinstance(data, dict) else {"value": data})

    def _project_state_attr(self, spec: dict, context: ViewContext) -> ViewEvent:
        """把某个 State 路径包装为 key-value 视图。"""
        resolver = ValueResolver()
        value = resolver.resolve(spec.get("source"), state=context.state, extra={"__state": context.state})
        label = spec.get("label") or spec.get("title") or "状态"
        data = {"rows": [{"label": label, "value": value if value is not None else ""}]}
        return self._build_event(spec, context, data)

    def _collect_convenience_data(self, spec: dict) -> dict:
        """兼容更短的看板写法，如直接写 rows/items/groups。"""
        data = {}
        for key in ("rows", "items", "groups", "columns", "cells", "text", "progress"):
            if key in spec:
                data[key] = spec[key]
        return data

    def _resolve_view_data(self, value: Any, context: ViewContext) -> Any:
        """
        解析视图数据中的引用。

        View data 里常有业务字段 `value`，不能把任意包含 value 的 dict 都当成
        ValueResolver 表达式；只有 `{ref: ...}` 和 `{state: ...}` 是引用。
        """
        resolver = ValueResolver()
        if isinstance(value, dict):
            if set(value.keys()) == {"ref"} or set(value.keys()) == {"state"}:
                return resolver.resolve(value, state=context.state, extra={"__state": context.state})
            return {
                key: self._resolve_view_data(item, context)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [self._resolve_view_data(item, context) for item in value]
        return value

    def _build_event(self, spec: dict, context: ViewContext, data: dict) -> ViewEvent:
        """根据通用字段构造 ViewEvent。"""
        view_id = spec.get("id") or spec.get("view_id")
        view_kind = spec.get("kind") or spec.get("view_kind") or "key-value"
        audience = spec.get("audience") or context.audience
        assert view_id, f"publication.views 条目缺少 id: {spec}"
        return ViewEvent(
            view_id=view_id,
            view_kind=view_kind,
            title=spec.get("title") or view_id,
            audience=audience,
            private=bool(spec.get("private", False)),
            priority=int(spec.get("priority", 0) or 0),
            layout=dict(spec.get("layout") or {}),
            data=data,
            meta={
                "source_plugin": spec.get("projector", "core.views.inline"),
                "scene": context.scene_name,
            },
        )



class GenericRuleSetPlugin:
    """内置 generic rule_set handler。

    该插件只证明 rule_set 调用链路，不实现具体游戏规则。
    """

    def register(self, api: PluginApi) -> None:
        """注册 generic rule_set handlers。"""
        api.register_rule_set_handler("builtin.board.generic", self._apply_generic)
        api.register_rule_set_handler("builtin.cards.generic", self._apply_generic)
        api.register_rule_set_handler("builtin.story.generic", self._apply_generic)

    def _apply_generic(self, context: RuleSetContext) -> dict:
        """返回通用 rule_set 调用结果。"""
        data = context.responses[0].get("data") if context.responses else None
        return {
            "plugin": context.rule_set.get("plugin"),
            "accepted": True,
            "action": data or {},
        }


class BuiltinPartyRuleSetPlugin:
    """内置 Lite 游戏规则插件集合。

    这些 handler 是具体游戏规则进入系统的边界。它们不接管 Director 主循环，
    只解释当前 scene 的响应数据，返回结构化结果，并把最近一次规则结果写到
    ``GAME.last_rule_set_result``，方便脚本和前端检查。
    """

    BOARD_PLUGINS = {
        "builtin.board.gomoku_lite": "gomoku_lite",
        "builtin.board.xiangqi_lite": "xiangqi_lite",
        "builtin.board.go_lite": "go_lite",
        "builtin.board.checkers_lite": "checkers_lite",
        "builtin.board.flight_chess_lite": "flight_chess_lite",
        "builtin.board.monopoly_lite": "monopoly_lite",
    }
    CARD_PLUGINS = {
        "builtin.cards.uno_lite": "uno_lite",
        "builtin.cards.exploding_kittens_lite": "exploding_kittens_lite",
        "builtin.cards.texas_holdem_party_lite": "texas_holdem_party_lite",
        "builtin.cards.card_event_party_lite": "card_event_party_lite",
    }
    STORY_PLUGINS = {
        "builtin.story.dice_map_adventure_lite": "dice_map_adventure_lite",
        "builtin.story.dnd_fixed_adventure": "dnd_fixed_adventure",
        "builtin.story.coc_fixed_mystery": "coc_fixed_mystery",
        "builtin.story.campaign_lite": "campaign_lite",
        "builtin.story.text_adventure_lite": "text_adventure_lite",
        "builtin.story.agent_dm_adventure_lite": "agent_dm_adventure_lite",
    }
    ECONOMY_PLUGINS = {
        "builtin.economy.asset_trading_lite": "asset_trading_lite",
    }

    def register(self, api: PluginApi) -> None:
        """注册所有 Lite 游戏 rule_set handler。"""
        for plugin in self.BOARD_PLUGINS:
            api.register_rule_set_handler(plugin, self._apply_board)
        for plugin in self.CARD_PLUGINS:
            api.register_rule_set_handler(plugin, self._apply_cards)
        for plugin in self.STORY_PLUGINS:
            api.register_rule_set_handler(plugin, self._apply_story)
        for plugin in self.ECONOMY_PLUGINS:
            api.register_rule_set_handler(plugin, self._apply_economy)

    def _apply_board(self, context: RuleSetContext) -> dict:
        """处理棋盘/地图类 Lite 规则。"""
        plugin = str(context.rule_set.get("plugin"))
        game = self.BOARD_PLUGINS[plugin]
        action = self._first_action(context)
        move = action.get("move") if isinstance(action.get("move"), dict) else action
        result = {
            "plugin": plugin,
            "game": game,
            "domain": "board",
            "accepted": self._has_any(move, ("position", "from", "to", "steps", "roll", "plane", "plane_id")),
            "action": action,
            "move": move or {},
            "reason": "",
        }
        if not result["accepted"]:
            result["reason"] = "棋盘动作需要 position、from/to 或 steps。"
        elif game == "gomoku_lite":
            result.update(self._apply_gomoku(context, move))
        elif game == "xiangqi_lite":
            result.update(self._apply_xiangqi(context, move))
        elif game == "go_lite":
            result.update(self._apply_go(context, move))
        elif game == "checkers_lite":
            result.update(self._apply_checkers(context, move))
        elif game == "flight_chess_lite":
            result.update(self._apply_flight_chess(context, move))
        elif game == "monopoly_lite":
            result.update(self._apply_monopoly(context, move))
        self._write_result(context, result)
        return result

    def _apply_cards(self, context: RuleSetContext) -> dict:
        """处理卡牌类 Lite 规则。"""
        plugin = str(context.rule_set.get("plugin"))
        game = self.CARD_PLUGINS[plugin]
        action = self._first_action(context)
        card_action = action.get("card_action") if isinstance(action.get("card_action"), dict) else action
        verb = str(card_action.get("type") or card_action.get("action") or card_action.get("verb") or "")
        result = {
            "plugin": plugin,
            "game": game,
            "domain": "cards",
            "accepted": bool(card_action),
            "action": action,
            "card_action": card_action or {},
            "verb": verb,
            "reason": "",
        }
        if not result["accepted"]:
            result["reason"] = "卡牌动作不能为空。"
        elif game == "uno_lite":
            result.update(self._apply_uno(context, card_action))
        elif game == "exploding_kittens_lite":
            result.update(self._apply_exploding_kittens(context, card_action))
        elif game == "texas_holdem_party_lite":
            result.update(self._apply_texas_holdem(context))
        elif game == "card_event_party_lite":
            result.update(self._apply_card_event(context, card_action))
        self._write_result(context, result)
        return result

    def _apply_story(self, context: RuleSetContext) -> dict:
        """处理剧情/跑团类 Lite 规则。"""
        plugin = str(context.rule_set.get("plugin"))
        game = self.STORY_PLUGINS[plugin]
        action = self._first_action(context)
        if game == "dice_map_adventure_lite":
            result = self._apply_dice_map_adventure(context, action)
            self._write_result(context, result)
            return result
        if game == "coc_fixed_mystery":
            result = self._apply_coc_mystery(context, action)
            self._write_result(context, result)
            return result
        if game in ("dnd_fixed_adventure", "campaign_lite", "agent_dm_adventure_lite"):
            result = self._apply_d20_story(context, game, action)
            self._write_result(context, result)
            return result
        if game == "text_adventure_lite":
            result = self._apply_text_adventure(context, action)
            self._write_result(context, result)
            return result
        roll = action.get("roll") if isinstance(action, dict) else None
        passed = None
        if isinstance(roll, int):
            passed = roll >= 12 if game != "coc_fixed_mystery" else roll <= 60
        result = {
            "plugin": plugin,
            "game": game,
            "domain": "story",
            "accepted": bool(action),
            "action": action,
            "check_passed": passed,
            "reason": "" if action else "剧情动作不能为空。",
        }
        self._write_result(context, result)
        return result

    def _apply_economy(self, context: RuleSetContext) -> dict:
        """处理经济/交易类 Lite 规则。"""
        plugin = str(context.rule_set.get("plugin"))
        game = self.ECONOMY_PLUGINS[plugin]
        action = self._first_action(context)
        trade = self._trade_data(action)
        accepted = bool(action.get("accept") or action.get("accepted") or action.get("action"))
        result = {
            "plugin": plugin,
            "game": game,
            "domain": "economy",
            "accepted": bool(action),
            "action": action,
            "trade_recorded": bool(action),
            "trade": trade,
            "settled": False,
            "reason": "" if action else "经济动作不能为空。",
        }
        if trade:
            context.writer.apply(SetAttr("GAME", "pending_trade", trade))
        if accepted and trade:
            self._settle_trade(context, trade)
            result["settled"] = True
            context.writer.apply(SetAttr("GAME", "last_trade", trade))
            context.writer.apply(SetAttr("GAME", "last_trade_accepted", True))
        self._write_result(context, result)
        return result

    def _trade_data(self, action: dict) -> dict:
        """从动作中提取资产交易字段。"""
        raw = action.get("trade") if isinstance(action.get("trade"), dict) else action
        asset = raw.get("asset") or raw.get("item")
        price = raw.get("price") or raw.get("amount")
        seller = raw.get("seller") or raw.get("from") or raw.get("actor")
        buyer = raw.get("buyer") or raw.get("to") or raw.get("target")
        if not asset or price is None:
            return {}
        return {
            "asset": str(asset),
            "price": int(price),
            "seller": str(seller) if seller else "",
            "buyer": str(buyer) if buyer else "",
        }

    def _settle_trade(self, context: RuleSetContext, trade: dict) -> None:
        """结算资产交易的现金和资产归属。"""
        seller = trade.get("seller")
        buyer = trade.get("buyer")
        asset = trade.get("asset")
        price = int(trade.get("price") or 0)
        if not seller or not buyer or not asset:
            return
        seller_assets = list(context.state.get_attr(seller, "assets", []) or [])
        buyer_assets = list(context.state.get_attr(buyer, "assets", []) or [])
        if asset in seller_assets:
            seller_assets.remove(asset)
        buyer_assets.append(asset)
        seller_cash = int(context.state.get_attr(seller, "cash", 0) or 0) + price
        buyer_cash = int(context.state.get_attr(buyer, "cash", 0) or 0) - price
        context.writer.apply(SetAttr(seller, "assets", seller_assets))
        context.writer.apply(SetAttr(buyer, "assets", buyer_assets))
        context.writer.apply(SetAttr(seller, "cash", seller_cash))
        context.writer.apply(SetAttr(buyer, "cash", buyer_cash))
        if buyer_cash < 0:
            context.writer.apply(SetAttr(buyer, "alive", False))

    def _apply_card_event(self, context: RuleSetContext, card_action: dict) -> dict:
        """执行牌堆事件抽牌和评分。"""
        actor = self._actor_from_context(context)
        verb = str(card_action.get("type") or card_action.get("action") or card_action.get("verb") or "")
        if verb == "draw" or not verb:
            deck = list(context.state.get_attr("GAME", "event_deck", []) or ["market-crash", "bonus-round", "duel"])
            event = deck.pop(0) if deck else None
            context.writer.apply(SetAttr("GAME", "event_deck", deck))
            context.writer.apply(SetAttr("GAME", "current_event", event))
            return {"accepted": True, "objective": "score_events", "event": event, "deck_remaining": len(deck)}
        if verb == "score":
            target = str(card_action.get("target") or actor)
            points = int(card_action.get("points") or card_action.get("rating") or 1)
            score = int(context.state.get_attr(target, "score", 0) or 0) + points
            context.writer.apply(SetAttr(target, "score", score))
            context.writer.apply(SetAttr("GAME", "last_scored_player", target))
            return {"accepted": True, "objective": "score_events", "target": target, "points": points, "score": score}
        result = {
            "accepted": True,
            "objective": "score_events",
            "event_action": card_action,
        }
        return result

    def _apply_dice_map_adventure(self, context: RuleSetContext, action: dict) -> dict:
        """执行骰子地图移动、补给消耗和宝藏节点胜利。"""
        actor = self._actor_from_context(context)
        move = action.get("move") if isinstance(action.get("move"), dict) else action
        current = int(context.state.get_attr(actor, "node", 0) or 0)
        roll = int(move.get("roll") or move.get("steps") or 0)
        target = int(move.get("to") if move.get("to") is not None else current + roll)
        max_node = int((context.rule_set.get("config") or {}).get("treasure_node") or 11)
        supplies = int(context.state.get_attr(actor, "supplies", 0) or 0)
        if roll > 0:
            supplies = max(0, supplies - 1)
        context.writer.apply(SetAttr(actor, "node", target))
        context.writer.apply(SetAttr(actor, "supplies", supplies))
        reached = target >= max_node
        if reached:
            context.writer.apply(SetAttr("GAME", "winner", "party"))
            context.writer.apply(SetAttr("GAME", "winning_condition", "reach_treasure"))
        return {
            "plugin": context.rule_set.get("plugin"),
            "game": "dice_map_adventure_lite",
            "domain": "story",
            "accepted": True,
            "action": action,
            "node": target,
            "supplies": supplies,
            "reached_treasure": reached,
        }

    def _apply_d20_story(self, context: RuleSetContext, game: str, action: dict) -> dict:
        """执行 DND/剧情跑团/Agent DM 的 d20 检定和进度记录。"""
        actor = self._actor_from_context(context)
        roll = int(action.get("roll") or 0)
        dc = int((context.rule_set.get("config") or {}).get("dc") or 12)
        passed = roll >= dc
        progress_key = f"{game}_successes"
        progress = int(context.state.get_attr("GAME", progress_key, 0) or 0)
        if passed:
            progress += 1
            context.writer.apply(SetAttr("GAME", progress_key, progress))
        memory = list(context.state.get_attr("GAME", "story_memory", []) or [])
        memory.append({"actor": actor, "action": action.get("action", ""), "roll": roll, "passed": passed})
        context.writer.apply(SetAttr("GAME", "story_memory", memory))
        if progress >= int((context.rule_set.get("config") or {}).get("successes_to_win") or 3):
            context.writer.apply(SetAttr("GAME", "winner", "party"))
            context.writer.apply(SetAttr("GAME", "winning_condition", "story_objective_completed"))
        return {
            "plugin": context.rule_set.get("plugin"),
            "game": game,
            "domain": "story",
            "accepted": bool(action),
            "action": action,
            "roll": roll,
            "dc": dc,
            "check_passed": passed,
            "successes": progress,
        }

    def _apply_coc_mystery(self, context: RuleSetContext, action: dict) -> dict:
        """执行 COC 调查线索和理智检定。"""
        actor = self._actor_from_context(context)
        clues = list(context.state.get_attr("GAME", "clues", []) or [])
        if action.get("location") or action.get("method"):
            clue = {"actor": actor, "location": action.get("location", ""), "method": action.get("method", "")}
            clues.append(clue)
            context.writer.apply(SetAttr("GAME", "clues", clues))
            return {
                "plugin": context.rule_set.get("plugin"),
                "game": "coc_fixed_mystery",
                "domain": "story",
                "accepted": True,
                "action": action,
                "clue_count": len(clues),
                "check_passed": None,
            }
        roll = int(action.get("roll") or 100)
        sanity = int(context.state.get_attr(actor, "sanity", 60) or 60)
        passed = roll <= sanity
        if not passed:
            sanity = max(0, sanity - 5)
            context.writer.apply(SetAttr(actor, "sanity", sanity))
        return {
            "plugin": context.rule_set.get("plugin"),
            "game": "coc_fixed_mystery",
            "domain": "story",
            "accepted": bool(action),
            "action": action,
            "roll": roll,
            "sanity": sanity,
            "check_passed": passed,
        }

    def _apply_text_adventure(self, context: RuleSetContext, action: dict) -> dict:
        """记录文字冒险观察/行动，并识别逃脱关键词。"""
        actor = self._actor_from_context(context)
        log = list(context.state.get_attr("GAME", "adventure_log", []) or [])
        entry = {"actor": actor, "target": action.get("target", ""), "action": action.get("action", "")}
        log.append(entry)
        context.writer.apply(SetAttr("GAME", "adventure_log", log))
        text = f"{entry['target']} {entry['action']}".lower()
        escaped = any(word in text for word in ("escape", "unlock", "key", "逃", "钥匙", "开锁"))
        if escaped:
            context.writer.apply(SetAttr("GAME", "winner", actor))
            context.writer.apply(SetAttr("GAME", "winning_condition", "escape_room"))
        return {
            "plugin": context.rule_set.get("plugin"),
            "game": "text_adventure_lite",
            "domain": "story",
            "accepted": bool(action),
            "action": action,
            "log_size": len(log),
            "escaped": escaped,
        }

    def _first_action(self, context: RuleSetContext) -> dict:
        """读取本幕第一个响应的数据。"""
        data = context.responses[0].get("data") if context.responses else {}
        return dict(data or {}) if isinstance(data, dict) else {"value": data}

    def _actor_from_context(self, context: RuleSetContext) -> str:
        """读取当前规则动作的 actor。"""
        if context.responses:
            actor = context.responses[0].get("actor")
            if actor:
                return str(actor)
        return "GAME"

    def _apply_gomoku(self, context: RuleSetContext, move: dict) -> dict:
        """执行五子棋落子、占位校验和五连胜负判断。"""
        actor = self._actor_from_context(context)
        position = move.get("position") if isinstance(move, dict) else None
        if not isinstance(position, list | tuple) or len(position) != 2:
            return {"accepted": False, "objective": "five_in_row", "reason": "五子棋落子需要 position=[row, col]。"}
        row = int(position[0])
        col = int(position[1])
        size = int((context.rule_set.get("config") or {}).get("board_size") or 15)
        if row < 0 or col < 0 or row >= size or col >= size:
            return {"accepted": False, "objective": "five_in_row", "reason": "落子坐标超出棋盘。"}

        board = dict(context.state.get_attr("GAME", "gomoku_board", {}) or {})
        key = f"{row},{col}"
        if key in board:
            return {"accepted": False, "objective": "five_in_row", "reason": "该位置已经有棋子。"}

        role = str(context.state.get_attr(actor, "role", "") or "")
        piece = "W" if role == "white" else "B"
        board[key] = piece
        context.writer.apply(SetAttr("GAME", "gomoku_board", board))
        context.writer.apply(SetAttr("GAME", "last_move", {"actor": actor, "position": [row, col], "piece": piece}))

        line = self._gomoku_max_line(board, row, col, piece)
        winner = actor if line >= 5 else None
        if winner:
            context.writer.apply(SetAttr("GAME", "winner", winner))
            context.writer.apply(SetAttr("GAME", "winning_condition", "five_in_row"))
        return {
            "accepted": True,
            "objective": "five_in_row",
            "position": [row, col],
            "piece": piece,
            "line": line,
            "winner": winner,
        }

    def _gomoku_max_line(self, board: dict, row: int, col: int, piece: str) -> int:
        """计算落子点四个方向上的最长连续棋子数。"""
        best = 1
        for dr, dc in ((1, 0), (0, 1), (1, 1), (1, -1)):
            total = 1
            for sign in (-1, 1):
                step = 1
                while board.get(f"{row + dr * step * sign},{col + dc * step * sign}") == piece:
                    total += 1
                    step += 1
            best = max(best, total)
        return best

    def _apply_xiangqi(self, context: RuleSetContext, move: dict) -> dict:
        """执行象棋基础走子和吃将胜利判断。"""
        actor = self._actor_from_context(context)
        source = self._point(move.get("from"))
        target = self._point(move.get("to"))
        if source is None or target is None:
            return {"accepted": False, "objective": "capture_general", "reason": "象棋走子需要 from 和 to 坐标。"}
        if not self._inside_board(target, 10, 9):
            return {"accepted": False, "objective": "capture_general", "reason": "目标坐标超出象棋棋盘。"}
        board = dict(context.state.get_attr("GAME", "xiangqi_board", {}) or {})
        source_key = self._point_key(source)
        target_key = self._point_key(target)
        role = str(context.state.get_attr(actor, "role", "") or "")
        piece = board.get(source_key) or ("R_piece" if role == "red" else "B_piece")
        captured = board.get(target_key)
        if source_key in board:
            del board[source_key]
        board[target_key] = piece
        context.writer.apply(SetAttr("GAME", "xiangqi_board", board))
        winner = actor if captured and str(captured).lower() in ("general", "king", "shuai", "jiang", "b_general", "r_general") else None
        if winner:
            context.writer.apply(SetAttr("GAME", "winner", winner))
            context.writer.apply(SetAttr("GAME", "winning_condition", "capture_general"))
        return {
            "accepted": True,
            "objective": "capture_general",
            "from": list(source),
            "to": list(target),
            "piece": piece,
            "captured": captured,
            "winner": winner,
        }

    def _apply_go(self, context: RuleSetContext, move: dict) -> dict:
        """执行 9 路围棋基础落子和无气提子。"""
        actor = self._actor_from_context(context)
        position = self._point(move.get("position") or move.get("to"))
        if position is None:
            return {"accepted": False, "objective": "territory", "reason": "围棋落子需要 position 坐标。"}
        size = int((context.rule_set.get("config") or {}).get("board_size") or 9)
        if not self._inside_board(position, size, size):
            return {"accepted": False, "objective": "territory", "reason": "落子坐标超出棋盘。"}
        board = dict(context.state.get_attr("GAME", "go_board", {}) or {})
        key = self._point_key(position)
        if key in board:
            return {"accepted": False, "objective": "territory", "reason": "该位置已经有棋子。"}
        role = str(context.state.get_attr(actor, "role", "") or "")
        stone = "W" if role == "white" else "B"
        board[key] = stone
        opponent = "B" if stone == "W" else "W"
        captured = []
        for neighbor in self._neighbors(position, size, size):
            neighbor_key = self._point_key(neighbor)
            if board.get(neighbor_key) == opponent:
                group = self._go_group(board, neighbor, opponent, size)
                if not self._go_has_liberty(board, group, size):
                    for point in group:
                        captured.append(list(point))
                        del board[self._point_key(point)]
        context.writer.apply(SetAttr("GAME", "go_board", board))
        context.writer.apply(SetAttr("GAME", "last_captured", captured))
        return {
            "accepted": True,
            "objective": "territory",
            "position": list(position),
            "stone": stone,
            "captured": captured,
            "board_count": len(board),
        }

    def _apply_checkers(self, context: RuleSetContext, move: dict) -> dict:
        """执行跳棋基础斜走、跳吃和升王判断。"""
        actor = self._actor_from_context(context)
        source = self._point(move.get("from"))
        target = self._point(move.get("to"))
        if source is None or target is None:
            return {"accepted": False, "objective": "capture_all", "reason": "跳棋移动需要 from 和 to 坐标。"}
        if not self._inside_board(target, 8, 8):
            return {"accepted": False, "objective": "capture_all", "reason": "目标坐标超出跳棋棋盘。"}
        dr = target[0] - source[0]
        dc = target[1] - source[1]
        if abs(dc) != abs(dr) or abs(dr) not in (1, 2):
            return {"accepted": False, "objective": "capture_all", "reason": "跳棋只能斜向移动一格或跳吃两格。"}
        board = dict(context.state.get_attr("GAME", "checkers_board", {}) or {})
        source_key = self._point_key(source)
        target_key = self._point_key(target)
        role = str(context.state.get_attr(actor, "role", "") or "")
        piece = board.get(source_key) or ("D" if role == "dark" else "L")
        captured = None
        if abs(dr) == 2:
            mid = ((source[0] + target[0]) // 2, (source[1] + target[1]) // 2)
            mid_key = self._point_key(mid)
            captured = board.pop(mid_key, None)
            if captured is None:
                return {"accepted": False, "objective": "capture_all", "reason": "跳吃两格时中间必须有对方棋子。"}
        if source_key in board:
            del board[source_key]
        crowned = target[0] in (0, 7)
        board[target_key] = f"{piece}K" if crowned and not str(piece).endswith("K") else piece
        context.writer.apply(SetAttr("GAME", "checkers_board", board))
        context.writer.apply(SetAttr("GAME", "last_captured", captured))
        return {
            "accepted": True,
            "objective": "capture_all",
            "from": list(source),
            "to": list(target),
            "captured": captured,
            "crowned": crowned,
        }

    def _point(self, value: Any) -> tuple[int, int] | None:
        """把 [row, col] 转为坐标 tuple。"""
        if isinstance(value, list | tuple) and len(value) == 2:
            return int(value[0]), int(value[1])
        return None

    def _point_key(self, point: tuple[int, int]) -> str:
        """坐标转状态字典 key。"""
        return f"{point[0]},{point[1]}"

    def _inside_board(self, point: tuple[int, int], rows: int, cols: int) -> bool:
        """检查坐标是否在棋盘内。"""
        return 0 <= point[0] < rows and 0 <= point[1] < cols

    def _neighbors(self, point: tuple[int, int], rows: int, cols: int) -> list[tuple[int, int]]:
        """返回上下左右邻点。"""
        result = []
        for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            next_point = (point[0] + dr, point[1] + dc)
            if self._inside_board(next_point, rows, cols):
                result.append(next_point)
        return result

    def _go_group(self, board: dict, start: tuple[int, int], stone: str, size: int) -> set[tuple[int, int]]:
        """收集同色连通棋块。"""
        pending = [start]
        group = set()
        while pending:
            point = pending.pop()
            if point in group:
                continue
            if board.get(self._point_key(point)) != stone:
                continue
            group.add(point)
            pending.extend(self._neighbors(point, size, size))
        return group

    def _go_has_liberty(self, board: dict, group: set[tuple[int, int]], size: int) -> bool:
        """判断棋块是否至少有一口气。"""
        for point in group:
            for neighbor in self._neighbors(point, size, size):
                if self._point_key(neighbor) not in board:
                    return True
        return False

    def _apply_monopoly(self, context: RuleSetContext, move: dict) -> dict:
        """执行大富翁移动、购买、收租和破产判断。"""
        actor = self._actor_from_context(context)
        config = context.rule_set.get("config") or {}
        board_size = int(config.get("board_size") or config.get("tiles") or 20)
        start_bonus = int(config.get("start_bonus") or 200)
        steps = int(move.get("steps") or 0)
        current = int(context.state.get_attr(actor, "position", 0) or 0)
        target = int(move.get("to") if move.get("to") is not None else (current + steps) % board_size)
        passed_start = steps > 0 and current + steps >= board_size
        cash = int(context.state.get_attr(actor, "cash", 0) or 0)
        if passed_start:
            cash += start_bonus
        context.writer.apply(SetAttr(actor, "position", target))

        tile = self._monopoly_tile(config, target)
        owners = dict(context.state.get_attr("GAME", "monopoly_owners", {}) or {})
        owner = owners.get(str(target))
        bought = False
        rent_paid = 0
        bankrupt = False
        if tile["kind"] == "property":
            wants_buy = bool(move.get("buy") or move.get("purchase"))
            if owner is None and wants_buy and cash >= tile["price"]:
                cash -= tile["price"]
                owners[str(target)] = actor
                owned = list(context.state.get_attr(actor, "properties", []) or [])
                owned.append(target)
                bought = True
                context.writer.apply(SetAttr(actor, "properties", owned))
                context.writer.apply(SetAttr("GAME", "monopoly_owners", owners))
            elif owner and owner != actor:
                rent_paid = tile["rent"]
                cash -= rent_paid
                owner_cash = int(context.state.get_attr(owner, "cash", 0) or 0) + rent_paid
                context.writer.apply(SetAttr(owner, "cash", owner_cash))
                bankrupt = cash < 0
        context.writer.apply(SetAttr(actor, "cash", cash))
        if bankrupt:
            context.writer.apply(SetAttr(actor, "alive", False))
            context.writer.apply(SetAttr("GAME", "last_bankrupt_player", actor))
        return {
            "accepted": True,
            "objective": "highest_net_worth",
            "position": target,
            "cash": cash,
            "tile": tile,
            "bought": bought,
            "rent_paid": rent_paid,
            "bankrupt": bankrupt,
        }

    def _monopoly_tile(self, config: dict, position: int) -> dict:
        """读取或生成大富翁地块配置。"""
        raw_tiles = config.get("tiles_config") or config.get("properties") or {}
        raw = None
        if isinstance(raw_tiles, dict):
            raw = raw_tiles.get(str(position)) or raw_tiles.get(position)
        elif isinstance(raw_tiles, list) and position < len(raw_tiles):
            raw = raw_tiles[position]
        if isinstance(raw, dict):
            return {
                "position": position,
                "kind": raw.get("kind") or "property",
                "price": int(raw.get("price") or 100),
                "rent": int(raw.get("rent") or 20),
            }
        if position == 0:
            return {"position": position, "kind": "start", "price": 0, "rent": 0}
        return {"position": position, "kind": "property", "price": 100 + position * 5, "rent": 20 + position}

    def _apply_flight_chess(self, context: RuleSetContext, move: dict) -> dict:
        """执行飞行棋起飞、移动、撞机、进终点和胜利判断。"""
        actor = self._actor_from_context(context)
        config = context.rule_set.get("config") or {}
        track_size = int(config.get("track_size") or config.get("tiles") or 52)
        plane_id = str(move.get("plane") or move.get("plane_id") or "A")
        steps = int(move.get("steps") or move.get("roll") or 0)
        planes = dict(context.state.get_attr(actor, "planes", {}) or {})
        current = planes.get(plane_id, "base")
        if current == "home":
            return {"accepted": False, "objective": "reach_home", "reason": "已到达终点的飞机不能继续移动。"}
        if current == "base" and steps != 6:
            return {
                "accepted": False,
                "objective": "reach_home",
                "reason": "飞机在基地时必须掷出 6 才能起飞。",
                "plane": plane_id,
                "roll": steps,
            }

        target = 0 if current == "base" else int(current) + steps
        arrived_home = target >= track_size
        planes[plane_id] = "home" if arrived_home else target % track_size
        context.writer.apply(SetAttr(actor, "planes", planes))

        hit_planes = []
        if not arrived_home:
            for entity in context.state.all_entities():
                if entity in ("GAME", actor):
                    continue
                other_planes = dict(context.state.get_attr(entity, "planes", {}) or {})
                for other_id, other_position in list(other_planes.items()):
                    if other_position == planes[plane_id]:
                        other_planes[other_id] = "base"
                        hit_planes.append({"actor": entity, "plane": other_id})
                if hit_planes:
                    context.writer.apply(SetAttr(entity, "planes", other_planes))

        planes_home = sum(1 for value in planes.values() if value == "home")
        context.writer.apply(SetAttr(actor, "planes_home", planes_home))
        winner = actor if planes_home >= int(config.get("planes_to_win") or 4) else None
        if winner:
            context.writer.apply(SetAttr("GAME", "winner", winner))
            context.writer.apply(SetAttr("GAME", "winning_condition", "reach_home"))
        return {
            "accepted": True,
            "objective": "reach_home",
            "plane": plane_id,
            "roll": steps,
            "position": planes[plane_id],
            "hit_planes": hit_planes,
            "planes_home": planes_home,
            "winner": winner,
        }

    def _apply_uno(self, context: RuleSetContext, card_action: dict) -> dict:
        """执行 UNO 出牌/摸牌、颜色数字匹配和功能牌效果。"""
        actor = self._actor_from_context(context)
        verb = str(card_action.get("type") or card_action.get("action") or card_action.get("verb") or "")
        top_card = str(context.state.get_attr("GAME", "uno_top_card", "") or "")
        hand = list(context.state.get_attr(actor, "hand", []) or [])
        draw_pile = list(context.state.get_attr("GAME", "uno_draw_pile", []) or [])
        direction = int(context.state.get_attr("GAME", "uno_direction", 1) or 1)
        result = {"objective": "empty_hand", "top_card": top_card, "hand_count": len(hand)}

        if verb == "draw":
            drawn = draw_pile.pop(0) if draw_pile else None
            if drawn:
                hand.append(drawn)
            context.writer.apply(SetAttr(actor, "hand", hand))
            context.writer.apply(SetAttr("GAME", "uno_draw_pile", draw_pile))
            result.update({"accepted": True, "drawn": drawn, "hand_count": len(hand)})
            return result

        card = str(card_action.get("card") or card_action.get("value") or "")
        if verb not in ("play", "card", "") or not card:
            result.update({"accepted": False, "reason": "UNO 动作需要 play card 或 draw。"})
            return result
        if card not in hand:
            result.update({"accepted": False, "reason": "玩家手牌中没有这张牌。"})
            return result
        if top_card and not self._uno_card_matches(card, top_card):
            result.update({"accepted": False, "reason": "出牌必须匹配颜色、点数或使用万能牌。"})
            return result

        hand.remove(card)
        context.writer.apply(SetAttr(actor, "hand", hand))
        context.writer.apply(SetAttr("GAME", "uno_top_card", card))
        effect = self._uno_card_effect(card)
        if effect == "reverse":
            direction *= -1
            context.writer.apply(SetAttr("GAME", "uno_direction", direction))
        elif effect in ("draw_two", "wild_draw_four"):
            context.writer.apply(SetAttr("GAME", "uno_pending_draw", 4 if effect == "wild_draw_four" else 2))
        elif effect == "skip":
            context.writer.apply(SetAttr("GAME", "uno_skip_next", True))
        winner = actor if len(hand) == 0 else None
        if winner:
            context.writer.apply(SetAttr("GAME", "winner", winner))
            context.writer.apply(SetAttr("GAME", "winning_condition", "empty_hand"))
        result.update({
            "accepted": True,
            "played": card,
            "effect": effect,
            "direction": direction,
            "hand_count": len(hand),
            "winner": winner,
        })
        return result

    def _uno_card_matches(self, card: str, top_card: str) -> bool:
        """判断 UNO 牌是否可以压过弃牌堆顶。"""
        color, rank = self._split_uno_card(card)
        top_color, top_rank = self._split_uno_card(top_card)
        return color == "wild" or color == top_color or rank == top_rank

    def _split_uno_card(self, card: str) -> tuple[str, str]:
        """解析 UNO 牌：red-5、blue-skip、wild-draw-four。"""
        parts = str(card).replace("_", "-").lower().split("-")
        if parts[0] == "wild":
            return "wild", "-".join(parts[1:] or ["wild"])
        if len(parts) >= 2:
            return parts[0], "-".join(parts[1:])
        return "", parts[0] if parts else ""

    def _uno_card_effect(self, card: str) -> str:
        """返回 UNO 功能牌效果名。"""
        _, rank = self._split_uno_card(card)
        if rank in ("skip", "reverse"):
            return rank
        if rank in ("draw-2", "draw-two", "+2"):
            return "draw_two"
        if rank in ("draw-4", "draw-four", "+4"):
            return "wild_draw_four"
        if "wild" in rank:
            return "wild"
        return "number"

    def _apply_exploding_kittens(self, context: RuleSetContext, card_action: dict) -> dict:
        """执行炸弹猫摸牌、拆除、爆炸出局和基础功能牌。"""
        actor = self._actor_from_context(context)
        verb = str(card_action.get("type") or card_action.get("action") or card_action.get("verb") or "")
        card = str(card_action.get("card") or card_action.get("value") or "")
        hand = list(context.state.get_attr(actor, "hand", []) or [])
        draw_pile = list(context.state.get_attr("GAME", "kitten_draw_pile", []) or [])
        result = {"objective": "avoid_explosion", "verb": verb, "card": card}

        if verb == "play":
            if card and card not in hand:
                return {**result, "accepted": False, "reason": "玩家手牌中没有这张功能牌。"}
            if card:
                hand.remove(card)
                context.writer.apply(SetAttr(actor, "hand", hand))
            effect = self._kitten_card_effect(card)
            if effect == "skip":
                context.writer.apply(SetAttr(actor, "skip_draw", True))
            elif effect == "attack":
                context.writer.apply(SetAttr("GAME", "kitten_attack_next", True))
            elif effect == "see_future":
                context.writer.apply(SetAttr(actor, "future_cards", draw_pile[:3]))
            return {**result, "accepted": True, "effect": effect, "future_cards": draw_pile[:3] if effect == "see_future" else []}

        if verb == "defuse":
            return self._defuse_kitten(context, actor, hand, draw_pile, result)

        if verb == "draw":
            drawn = draw_pile.pop(0) if draw_pile else None
            context.writer.apply(SetAttr("GAME", "kitten_draw_pile", draw_pile))
            if drawn == "exploding-kitten":
                defuse_count = int(context.state.get_attr(actor, "defuse_count", 0) or 0)
                if "defuse" in hand or defuse_count > 0:
                    return self._defuse_kitten(context, actor, hand, draw_pile, {**result, "drawn": drawn})
                context.writer.apply(SetAttr(actor, "alive", False))
                context.writer.apply(SetAttr("GAME", "last_exploded_player", actor))
                return {**result, "accepted": True, "drawn": drawn, "exploded": True, "defused": False}
            if drawn:
                hand.append(drawn)
                context.writer.apply(SetAttr(actor, "hand", hand))
            return {**result, "accepted": True, "drawn": drawn, "exploded": False, "defused": False}

        return {**result, "accepted": False, "reason": "炸弹猫动作需要 play、draw 或 defuse。"}

    def _defuse_kitten(self, context: RuleSetContext, actor: str, hand: list, draw_pile: list, result: dict) -> dict:
        """使用拆除牌保命，并把炸弹放回牌堆底部。"""
        defuse_count = int(context.state.get_attr(actor, "defuse_count", 0) or 0)
        if "defuse" in hand:
            hand.remove("defuse")
        elif defuse_count > 0:
            defuse_count -= 1
            context.writer.apply(SetAttr(actor, "defuse_count", defuse_count))
        else:
            context.writer.apply(SetAttr(actor, "alive", False))
            context.writer.apply(SetAttr("GAME", "last_exploded_player", actor))
            return {**result, "accepted": True, "exploded": True, "defused": False}
        draw_pile.append("exploding-kitten")
        context.writer.apply(SetAttr(actor, "hand", hand))
        context.writer.apply(SetAttr("GAME", "kitten_draw_pile", draw_pile))
        return {**result, "accepted": True, "exploded": False, "defused": True}

    def _kitten_card_effect(self, card: str) -> str:
        """解析炸弹猫基础功能牌。"""
        value = str(card).replace("_", "-").lower()
        if value in ("skip", "attack", "shuffle"):
            return value
        if value in ("see-the-future", "see-future", "future"):
            return "see_future"
        if value in ("nope", "favor"):
            return value
        return "none"

    def _apply_texas_holdem(self, context: RuleSetContext) -> dict:
        """执行德州扑克下注记录或摊牌牌型比较。"""
        showdowns = []
        for response in context.responses:
            data = response.get("data") or {}
            cards = self._card_list(data.get("cards") or data.get("hand"))
            community = self._card_list(data.get("community") or context.state.get_attr("GAME", "community_cards", []))
            if cards:
                score = self._best_poker_score(cards + community)
                showdowns.append({"actor": response.get("actor"), "score": score, "cards": cards})
        if showdowns:
            showdowns.sort(key=lambda item: item["score"]["rank_value"], reverse=True)
            winner = showdowns[0]["actor"]
            context.writer.apply(SetAttr("GAME", "showdown_results", showdowns))
            context.writer.apply(SetAttr("GAME", "winner", winner))
            return {
                "accepted": True,
                "objective": "best_hand_or_fold_equity",
                "showdown": True,
                "winner": winner,
                "best_hand": showdowns[0]["score"],
            }

        actor = self._actor_from_context(context)
        action = self._first_action(context)
        move = str(action.get("move") or action.get("action") or action.get("verb") or "")
        amount = int(action.get("amount") or 0)
        pot = int(context.state.get_attr("GAME", "pot", 0) or 0)
        chips = int(context.state.get_attr(actor, "chips", 0) or 0)
        if move in ("bet", "call", "raise") and amount > 0:
            paid = min(chips, amount)
            chips -= paid
            pot += paid
            context.writer.apply(SetAttr(actor, "chips", chips))
            context.writer.apply(SetAttr("GAME", "pot", pot))
        elif move == "fold":
            context.writer.apply(SetAttr(actor, "folded", True))
        return {
            "accepted": bool(move),
            "objective": "best_hand_or_fold_equity",
            "showdown": False,
            "move": move,
            "amount": amount,
            "pot": pot,
            "chips": chips,
        }

    def _card_list(self, value: Any) -> list[str]:
        """把列表或逗号分隔字符串转换成扑克牌列表。"""
        if isinstance(value, list | tuple):
            return [str(item).strip().upper() for item in value if str(item).strip()]
        if isinstance(value, str):
            return [item.strip().upper() for item in value.split(",") if item.strip()]
        return []

    def _best_poker_score(self, cards: list[str]) -> dict:
        """从 5-7 张牌里计算最佳五张德州牌型。"""
        best: tuple[int, list[int]] | None = None
        for combo in combinations(cards, 5):
            score = self._score_five_cards(list(combo))
            if best is None or score > best:
                best = score
        category, tiebreakers = best or (0, [])
        names = [
            "high_card", "one_pair", "two_pair", "three_kind", "straight",
            "flush", "full_house", "four_kind", "straight_flush",
        ]
        return {
            "category": names[category],
            "category_value": category,
            "tiebreakers": tiebreakers,
            "rank_value": [category] + tiebreakers,
        }

    def _score_five_cards(self, cards: list[str]) -> tuple[int, list[int]]:
        """计算五张牌分值，返回可比较 tuple。"""
        ranks = [self._poker_rank(card) for card in cards]
        suits = [self._poker_suit(card) for card in cards]
        counts = {rank: ranks.count(rank) for rank in set(ranks)}
        ordered_counts = sorted(counts.items(), key=lambda item: (item[1], item[0]), reverse=True)
        flush = len(set(suits)) == 1
        straight_high = self._straight_high(ranks)
        if flush and straight_high:
            return 8, [straight_high]
        if ordered_counts[0][1] == 4:
            kicker = max(rank for rank in ranks if rank != ordered_counts[0][0])
            return 7, [ordered_counts[0][0], kicker]
        if ordered_counts[0][1] == 3 and ordered_counts[1][1] == 2:
            return 6, [ordered_counts[0][0], ordered_counts[1][0]]
        if flush:
            return 5, sorted(ranks, reverse=True)
        if straight_high:
            return 4, [straight_high]
        if ordered_counts[0][1] == 3:
            kickers = sorted([rank for rank in ranks if rank != ordered_counts[0][0]], reverse=True)
            return 3, [ordered_counts[0][0]] + kickers
        if ordered_counts[0][1] == 2 and ordered_counts[1][1] == 2:
            pairs = sorted([rank for rank, count in counts.items() if count == 2], reverse=True)
            kicker = max(rank for rank, count in counts.items() if count == 1)
            return 2, pairs + [kicker]
        if ordered_counts[0][1] == 2:
            pair = ordered_counts[0][0]
            kickers = sorted([rank for rank in ranks if rank != pair], reverse=True)
            return 1, [pair] + kickers
        return 0, sorted(ranks, reverse=True)

    def _poker_rank(self, card: str) -> int:
        """解析扑克牌点数。"""
        value = str(card).strip().upper()[:-1]
        ranks = {"A": 14, "K": 13, "Q": 12, "J": 11, "T": 10}
        return ranks.get(value, int(value) if value.isdigit() else 0)

    def _poker_suit(self, card: str) -> str:
        """解析扑克牌花色。"""
        return str(card).strip().upper()[-1:]

    def _straight_high(self, ranks: list[int]) -> int:
        """返回顺子最高点；A2345 记为 5 高顺。"""
        unique = sorted(set(ranks), reverse=True)
        if {14, 5, 4, 3, 2}.issubset(set(unique)):
            return 5
        for index in range(0, max(0, len(unique) - 4)):
            window = unique[index:index + 5]
            if window[0] - window[4] == 4:
                return window[0]
        return 0

    def _has_any(self, value: Any, keys: tuple[str, ...]) -> bool:
        """判断 dict 中是否存在任一关键字段。"""
        return isinstance(value, dict) and any(key in value for key in keys)

    def _write_result(self, context: RuleSetContext, result: dict) -> None:
        """把最近一次 rule_set 结果写入 GAME。"""
        context.writer.apply(SetAttr("GAME", "last_rule_set_result", result))
        context.writer.apply(SetAttr("GAME", "last_rule_set_plugin", result["plugin"]))


class AvalonRulesPlugin:
    """阿瓦隆规则插件：补齐当前通用 DSL 尚未内置的任务结算原语。

    设计边界 / Design boundary:
      - 只处理《阿瓦隆》的确定性桌游规则表与计票，不接管 Director 主循环。
      - 所有状态写入仍通过 StateWriter 完成，保持唯一写入口。
      - 剧本通过 extensions.avalon.rules 提供人数、任务人数、失败阈值等配置。
    """

    def register(self, api: PluginApi) -> None:
        """注册阿瓦隆专用 effect 和 condition。"""
        api.register_effect("avalon_set_quest_rule", self._set_quest_rule)
        api.register_effect("avalon_clear_current_team", self._clear_current_team)
        api.register_effect("avalon_record_team", self._record_team)
        api.register_effect("avalon_record_team_vote", self._record_team_vote)
        api.register_effect("avalon_resolve_team_vote", self._resolve_team_vote)
        api.register_effect("avalon_record_mission_card", self._record_mission_card)
        api.register_effect("avalon_resolve_mission", self._resolve_mission)
        api.register_condition("avalon.team_vote_approved", self._team_vote_approved)
        api.register_condition("avalon.assassination_hits_merlin", self._assassination_hits_merlin)

    def _rules(self, context: EffectContext | Any) -> dict:
        """读取 Script.extensions.avalon.rules；缺失时用标准 5 人局兜底。"""
        extra = getattr(context, "extra", None)
        if extra is None and isinstance(context, dict):
            extra = context.get("extra")
        script_extensions = {}
        if isinstance(extra, dict):
            script_extensions = extra.get("script_extensions") or {}
        avalon = script_extensions.get("avalon") if isinstance(script_extensions, dict) else None
        rules = avalon.get("rules") if isinstance(avalon, dict) else None
        if isinstance(rules, dict):
            return rules
        return {
            "player_count": 5,
            "quest_team_sizes": [2, 3, 2, 3, 3],
            "quest_fail_thresholds": [1, 1, 1, 1, 1],
            "team_rejection_limit": 5,
        }

    def _quest_index(self, state: Any) -> int:
        """返回当前任务下标（0-4）。"""
        quest_number = state.get_attr("GAME", "quest_number") or 1
        index = int(quest_number) - 1
        if index < 0:
            index = 0
        if index > 4:
            index = 4
        return index

    def _set_quest_rule(self, effect: dict, context: EffectContext) -> None:
        """把当前轮任务人数与失败阈值写入 GAME，供后续 scene 使用。"""
        rules = self._rules(context)
        index = self._quest_index(context.state)
        team_sizes = list(rules.get("quest_team_sizes") or [2, 3, 2, 3, 3])
        fail_thresholds = list(rules.get("quest_fail_thresholds") or [1, 1, 1, 1, 1])
        team_size = int(team_sizes[index])
        fail_threshold = int(fail_thresholds[index])
        context.writer.apply(SetAttr("GAME", "current_team_size", team_size))
        context.writer.apply(SetAttr("GAME", "current_fail_threshold", fail_threshold))

    def _clear_current_team(self, effect: dict, context: EffectContext) -> None:
        """清空当前任务队伍标记，避免上一轮任务队伍残留。"""
        for entity in context.state.all_entities():
            if entity == "GAME":
                continue
            if context.state.get_attr(entity, "in_current_team"):
                context.writer.apply(SetAttr(entity, "in_current_team", False))
        context.writer.apply(SetAttr("GAME", "current_team", []))

    def _record_team(self, effect: dict, context: EffectContext) -> None:
        """记录队长提名的任务队伍。"""
        data = context.responses[0].get("data") if context.responses else {}
        targets = list((data or {}).get("targets") or [])
        context.writer.apply(SetAttr("GAME", "current_team", targets))

    def _record_team_vote(self, effect: dict, context: EffectContext) -> None:
        """把组队投票记录成列表，True=同意，False=反对。"""
        votes = []
        for response in context.responses:
            data = response.get("data") or {}
            votes.append({
                "actor": response.get("actor"),
                "approve": bool(data.get("action")),
                "reason": data.get("reason", ""),
            })
        approve_count = sum(1 for vote in votes if vote["approve"])
        reject_count = len(votes) - approve_count
        context.writer.apply(SetAttr("GAME", "team_votes", votes))
        context.writer.apply(SetAttr("GAME", "team_approve_count", approve_count))
        context.writer.apply(SetAttr("GAME", "team_reject_count", reject_count))

    def _team_vote_approved(self, spec: dict, context: Any) -> bool:
        """判断组队投票是否通过；超过半数同意即通过。"""
        state = context["state"] if isinstance(context, dict) else context.state
        approve_count = int(state.get_attr("GAME", "team_approve_count") or 0)
        player_count = int(state.get_attr("GAME", "player_count") or 0)
        if player_count <= 0:
            player_count = len([name for name in state.all_entities() if name != "GAME"])
        return approve_count > player_count / 2

    def _resolve_if_condition_matches(self, spec: dict, context: EffectContext) -> str:
        """按 ifs 规则解析下一流程状态；无匹配则返回 default。"""
        resolver = ValueResolver()
        rules = spec.get("ifs") or []
        for rule in rules:
            if not isinstance(rule, dict):
                continue
            condition = rule.get("when")
            target_state = rule.get("state")
            if not target_state:
                continue
            if condition is None:
                return str(target_state)
            value = resolver.resolve(
                condition.get("value"),
                state=context.state,
                responses=context.responses,
                extra=context.extra,
            )
            matched = True
            if "equal" in condition:
                expected = resolver.resolve(
                    condition.get("equal"),
                    state=context.state,
                    responses=context.responses,
                    extra=context.extra,
                )
                matched = value == expected
            elif "not_equal" in condition:
                expected = resolver.resolve(
                    condition.get("not_equal"),
                    state=context.state,
                    responses=context.responses,
                    extra=context.extra,
                )
                matched = value != expected
            elif "greater_than_equal" in condition:
                expected = resolver.resolve(
                    condition.get("greater_than_equal"),
                    state=context.state,
                    responses=context.responses,
                    extra=context.extra,
                )
                matched = value is not None and value >= expected
            elif "less_than_equal" in condition:
                expected = resolver.resolve(
                    condition.get("less_than_equal"),
                    state=context.state,
                    responses=context.responses,
                    extra=context.extra,
                )
                matched = value is not None and value <= expected
            elif "not_null" in condition:
                matched = (value is not None) == bool(condition.get("not_null"))
            if matched:
                return str(target_state)
        return str(spec.get("default", ""))

    def _resolve_team_vote(self, effect: dict, context: EffectContext) -> None:
        """根据组队投票结果更新失败次数、队长与流程分支。"""
        approved = self._team_vote_approved({}, {"state": context.state})
        rejection_count = int(context.state.get_attr("GAME", "team_rejection_count") or 0)
        leader_index = int(context.state.get_attr("GAME", "leader_index") or 1)
        player_count = int(context.state.get_attr("GAME", "player_count") or 0)
        if player_count <= 0:
            player_count = len([name for name in context.state.all_entities() if name != "GAME"])
        if approved:
            context.writer.apply(SetAttr("GAME", "team_approved", True))
            context.writer.apply(SetAttr("GAME", "team_rejection_count", 0))
            next_state = self._resolve_if_condition_matches(effect, context) or "mission"
            context.writer.apply(SetAttr("GAME", "__flow_next_state", next_state))
            return
        rejection_count += 1
        context.writer.apply(SetAttr("GAME", "team_approved", False))
        context.writer.apply(SetAttr("GAME", "team_rejection_count", rejection_count))
        if player_count > 0:
            next_leader = (leader_index % player_count) + 1
            context.writer.apply(SetAttr("GAME", "leader_index", next_leader))
        limit = int(self._rules(context).get("team_rejection_limit", 5))
        if rejection_count >= limit:
            # 阿瓦隆标准规则：连续 5 次组队失败，邪恶阵营直接获胜。
            context.writer.apply(SetAttr("GAME", "evil_auto_win", True))
            context.writer.apply(SetAttr("GAME", "__flow_next_state", "evil_win"))
        else:
            context.writer.apply(SetAttr("GAME", "__flow_next_state", "team_building"))

    def _record_mission_card(self, effect: dict, context: EffectContext) -> None:
        """记录任务队员秘密提交的成功/失败牌。"""
        cards = []
        for response in context.responses:
            data = response.get("data") or {}
            card_success = bool(data.get("action"))
            cards.append({
                "actor": response.get("actor"),
                "success": card_success,
                "reason": data.get("reason", ""),
            })
        fail_count = sum(1 for card in cards if not card["success"])
        context.writer.apply(SetAttr("GAME", "mission_cards", cards))
        context.writer.apply(SetAttr("GAME", "mission_fail_count", fail_count))

    def _resolve_mission(self, effect: dict, context: EffectContext) -> None:
        """按失败票阈值结算任务，并推进任务轮次/队长/终局流程。"""
        fail_count = int(context.state.get_attr("GAME", "mission_fail_count") or 0)
        threshold = int(context.state.get_attr("GAME", "current_fail_threshold") or 1)
        good_score = int(context.state.get_attr("GAME", "good_score") or 0)
        evil_score = int(context.state.get_attr("GAME", "evil_score") or 0)
        quest_number = int(context.state.get_attr("GAME", "quest_number") or 1)
        leader_index = int(context.state.get_attr("GAME", "leader_index") or 1)
        player_count = int(context.state.get_attr("GAME", "player_count") or 0)
        if player_count <= 0:
            player_count = len([name for name in context.state.all_entities() if name != "GAME"])

        mission_failed = fail_count >= threshold
        if mission_failed:
            evil_score += 1
            last_result = "failure"
        else:
            good_score += 1
            last_result = "success"
        context.writer.apply(SetAttr("GAME", "last_mission_result", last_result))
        context.writer.apply(SetAttr("GAME", "good_score", good_score))
        context.writer.apply(SetAttr("GAME", "evil_score", evil_score))

        if evil_score >= 3:
            context.writer.apply(SetAttr("GAME", "__flow_next_state", "evil_win"))
            return
        if good_score >= 3:
            next_state = self._resolve_if_condition_matches(effect, context) or "assassination"
            context.writer.apply(SetAttr("GAME", "__flow_next_state", next_state))
            return

        if player_count > 0:
            next_leader = (leader_index % player_count) + 1
            context.writer.apply(SetAttr("GAME", "leader_index", next_leader))
        context.writer.apply(SetAttr("GAME", "quest_number", quest_number + 1))
        context.writer.apply(SetAttr("GAME", "team_rejection_count", 0))
        context.writer.apply(SetAttr("GAME", "current_team", []))
        context.writer.apply(SetAttr("GAME", "team_votes", []))
        context.writer.apply(SetAttr("GAME", "mission_cards", []))
        context.writer.apply(SetAttr("GAME", "__flow_next_state", "team_building"))

    def _assassination_hits_merlin(self, spec: dict, context: Any) -> bool:
        """判断刺客是否命中梅林。"""
        state = context["state"] if isinstance(context, dict) else context.state
        target = state.get_attr("GAME", "assassination_target")
        if not target:
            return False
        return state.get_attr(str(target), "role") == "merlin"

def build_default_plugin_registry() -> PluginRegistry:
    """构建默认插件注册表，包含 core.views。"""
    registry = PluginRegistry()
    api = PluginApi(registry)
    CoreViewsPlugin().register(api)
    GenericRuleSetPlugin().register(api)
    BuiltinPartyRuleSetPlugin().register(api)
    AvalonRulesPlugin().register(api)
    return registry
