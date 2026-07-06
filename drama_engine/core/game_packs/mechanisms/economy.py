"""经济领域机制（economy）。

提供现金增减、转账、破产判定等原子机制，供大富翁、资产交易等经济类游戏引用。
现金存在 <entity>.cash；破产时写 <entity>.alive=False。

机制清单：
- effect  credit          ：给某实体加钱。
- effect  debit           ：给某实体扣钱；不足时按 allow_negative 决定是否触发破产。
- effect  transfer        ：从一方转账给另一方（付款方不足可触发破产）。
- condition economy.bankrupt ：判断某实体是否破产（cash<0 或 alive=False）。
"""

from __future__ import annotations

import logging
from typing import Any

from drama_engine.core.engine import SetAttr

logger = logging.getLogger(__name__)


def _amount(effect: dict, default: int = 0) -> int:
    """读取 effect.amount。"""
    value = effect.get("amount") if isinstance(effect, dict) else None
    try:
        return int(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def _cash(state: Any, entity: str) -> int:
    """读取实体现金，默认 0。"""
    return int(state.get_attr(entity, "cash") or 0)


def _handle_credit(effect: dict, context: Any) -> None:
    """给某实体加钱。effect: target(默认当前 actor), amount。"""
    target = effect.get("target") or getattr(context, "actor", None)
    assert target, "credit 需要 target"
    amount = _amount(effect)
    context.writer.apply(SetAttr(target, "cash", _cash(context.state, target) + amount))
    logger.debug("[credit] target=%s amount=%s", target, amount)


def _apply_bankruptcy_if_needed(effect: dict, context: Any, entity: str) -> None:
    """现金为负且不允许负值时，标记破产。"""
    allow_negative = bool(effect.get("allow_negative"))
    if not allow_negative and _cash(context.state, entity) < 0:
        context.writer.apply(SetAttr(entity, "alive", False))
        context.writer.apply(SetAttr(entity, "bankrupt", True))
        logger.debug("[economy] 破产 entity=%s", entity)


def _handle_debit(effect: dict, context: Any) -> None:
    """给某实体扣钱。effect: target(默认当前 actor), amount, allow_negative。"""
    target = effect.get("target") or getattr(context, "actor", None)
    assert target, "debit 需要 target"
    amount = _amount(effect)
    context.writer.apply(SetAttr(target, "cash", _cash(context.state, target) - amount))
    _apply_bankruptcy_if_needed(effect, context, target)


def _handle_transfer(effect: dict, context: Any) -> None:
    """从 payer 转账给 payee。effect: payer(默认当前 actor), payee, amount。"""
    payer = effect.get("payer") or getattr(context, "actor", None)
    payee = effect.get("payee")
    assert payer and payee, "transfer 需要 payer 和 payee"
    amount = _amount(effect)
    context.writer.apply(SetAttr(payer, "cash", _cash(context.state, payer) - amount))
    context.writer.apply(SetAttr(payee, "cash", _cash(context.state, payee) + amount))
    _apply_bankruptcy_if_needed(effect, context, payer)
    logger.debug("[transfer] %s->%s amount=%s", payer, payee, amount)


def _cond_bankrupt(spec: dict, context: dict) -> bool:
    """判断某实体是否破产。spec.input.entity 或 spec.entity 指定实体，缺省用当前 actor。"""
    state = context.get("state")
    if state is None:
        return False
    source = spec.get("input") if isinstance(spec.get("input"), dict) else spec
    entity = source.get("entity") or context.get("actor")
    if not entity:
        return False
    if state.get_attr(entity, "bankrupt"):
        return True
    return int(state.get_attr(entity, "cash") or 0) < 0


def register(api: Any) -> None:
    """把 economy 机制注册进 PluginRegistry。"""
    api.register_effect("credit", _handle_credit)
    api.register_effect("debit", _handle_debit)
    api.register_effect("transfer", _handle_transfer)
    api.register_condition("economy.bankrupt", _cond_bankrupt)


__all__ = ["register"]
