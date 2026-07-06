"""披露账本 / Disclosure ledger（架构文档 §14 动态可见性）。

KnowledgeFirewall 的静态部分回答「你这个身份天生能看到什么」（VisibilityPolicy）；
本模块补上动态部分：「你在游戏过程中被主动告知过什么」。

典型场景：狼人杀预言家验人后，验人结果通过 publication.disclosures 私发给预言家一次。
如果只推送一次，firewall 在下一轮为预言家构建 prompt 投影时并不知道「他已被告知过谁的身份」。
DisclosureLedger 把每次披露记录下来，firewall 投影时把这些已披露事实并入该 actor 的视图。

设计与 PatchJournal（patch/journal.py）完全对称：append-only，可 snapshot / restore，
因此天然纳入 checkpoint / rollback。回滚语义采用「截断」——回滚到验人动作之前，
「验人结果」这条披露也随之消失（披露是游戏因果链的一部分），审计留痕由 rollback_applied 事件承担。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class DisclosureRecord:
    """一条披露记录 / One disclosure record.

    字段：
      actor    — 被披露的对象（seat_id / actor 名，如 "Player_2"）。
      fact_ref — 事实引用键（如 "GAME.last_inspection_result"），用于标识「披露的是哪条事实」。
      value    — 披露的具体值（如 {"target": "Player_5", "role": "civilian"}）。
      at_beat  — 披露发生时的节拍序号（round / beat），便于排序与调试；未知时为 0。
      created_at — 记录创建时间戳（wall clock），仅用于审计。
    """

    actor: str
    fact_ref: str
    value: Any
    at_beat: int = 0
    created_at: float = 0.0


class DisclosureLedger:
    """Append-only 披露账本。

    记录「谁在何时被披露了哪条事实」，供 KnowledgeFirewall 合成 actor view。
    """

    def __init__(self) -> None:
        """初始化空账本。"""
        self._records: list[DisclosureRecord] = []

    def record(self, actor: str, fact_ref: str, value: Any, at_beat: int = 0) -> DisclosureRecord:
        """追加一条披露记录。

        参数：
          actor    — 被披露的对象（seat_id / actor 名），不能为空。
          fact_ref — 事实引用键（如 "GAME.last_inspection_result"），不能为空。
          value    — 披露的具体值（任意可序列化对象）。
          at_beat  — 披露发生的节拍序号，默认 0。

        返回：新建的 DisclosureRecord。
        """
        assert actor, "disclosure.actor 不能为空"
        assert fact_ref, "disclosure.fact_ref 不能为空"
        record = DisclosureRecord(
            actor=str(actor),
            fact_ref=str(fact_ref),
            value=value,
            at_beat=int(at_beat),
            created_at=time.time(),
        )
        self._records.append(record)
        logger.debug("[DisclosureLedger] 记录披露：actor=%s fact=%s beat=%s", actor, fact_ref, at_beat)
        return record

    def facts_for(self, actor: str) -> dict[str, Any]:
        """返回某 actor 已被披露的全部事实（fact_ref -> value）。

        同一 fact_ref 多次披露时，后者覆盖前者（返回最新值）。供 firewall 合成使用。

        参数：
          actor — 目标 actor 名。为空 / None 时返回空 dict。
        """
        if not actor:
            return {}
        facts: dict[str, Any] = {}
        for record in self._records:
            if record.actor == actor:
                facts[record.fact_ref] = record.value
        return facts

    def all(self) -> list[DisclosureRecord]:
        """返回全部披露记录（副本）。"""
        return list(self._records)

    def snapshot(self) -> list[dict[str, Any]]:
        """返回可序列化快照（供 checkpoint 使用）。"""
        return [
            {
                "actor": record.actor,
                "fact_ref": record.fact_ref,
                "value": record.value,
                "at_beat": record.at_beat,
                "created_at": record.created_at,
            }
            for record in self._records
        ]

    def restore(self, snapshot: list[dict[str, Any]]) -> None:
        """从 snapshot() 的快照整体恢复账本记录（用于回滚，截断语义）。"""
        assert isinstance(snapshot, list), "disclosure 快照必须是 list"
        records: list[DisclosureRecord] = []
        for item in snapshot:
            assert isinstance(item, dict), "disclosure 快照项必须是 dict"
            records.append(DisclosureRecord(
                actor=str(item.get("actor") or ""),
                fact_ref=str(item.get("fact_ref") or ""),
                value=item.get("value"),
                at_beat=int(item.get("at_beat") or 0),
                created_at=float(item.get("created_at") or 0.0),
            ))
        self._records = records
        logger.debug("[DisclosureLedger] 从快照恢复 %d 条披露记录", len(records))


__all__ = ["DisclosureLedger", "DisclosureRecord"]
