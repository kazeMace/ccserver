"""Player token service for Drama Engine sessions.

玩家 token 是 Web 多会话隔离的关键：token 必须定位到 session_id + seat_id，
不能只定位到 Player_1 这样的局内 seat 名称。
"""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PlayerClaim:
    """玩家 token 的解析结果。

    参数：
      session_id: 所属游戏会话 ID。
      seat_id: 所属局内 seat ID。
      user_id: 已认领用户 ID；未认领时为 None。
    """

    session_id: str
    seat_id: str
    user_id: str | None = None

    def __post_init__(self) -> None:
        assert self.session_id, "session_id 不能为空"
        assert self.seat_id, "seat_id 不能为空"


class PlayerTokenService:
    """进程级玩家 token 服务。

    本服务只保存 token 到 session/seat 的映射，不保存游戏状态和事件内容。
    """

    def __init__(self) -> None:
        self._claims_by_token: dict[str, PlayerClaim] = {}
        self._tokens_by_seat: dict[tuple[str, str], str] = {}

    def create_token(self, session_id: str, seat_id: str) -> str:
        """为 session 的 seat 创建或返回已有 token。"""
        assert session_id, "session_id 不能为空"
        assert seat_id, "seat_id 不能为空"
        key = (session_id, seat_id)
        existing = self._tokens_by_seat.get(key)
        if existing:
            return existing
        token = secrets.token_urlsafe(24)
        self._tokens_by_seat[key] = token
        self._claims_by_token[token] = PlayerClaim(session_id=session_id, seat_id=seat_id)
        logger.info("[PlayerTokenService] 创建 token：session=%s seat=%s", session_id, seat_id)
        return token

    def reset_token(self, session_id: str, seat_id: str) -> str:
        """重置指定 seat 的 token，旧 token 立即失效。"""
        assert session_id, "session_id 不能为空"
        assert seat_id, "seat_id 不能为空"
        key = (session_id, seat_id)
        old_token = self._tokens_by_seat.pop(key, None)
        if old_token is not None:
            self._claims_by_token.pop(old_token, None)
        return self.create_token(session_id=session_id, seat_id=seat_id)

    def validate(self, token: str) -> PlayerClaim | None:
        """校验 token，返回 PlayerClaim；无效时返回 None。"""
        if not token:
            return None
        return self._claims_by_token.get(token)

    def claim(self, token: str, user_id: str) -> PlayerClaim | None:
        """把 token 标记为某个用户认领。"""
        assert user_id, "user_id 不能为空"
        claim = self.validate(token)
        if claim is None:
            return None
        updated = PlayerClaim(
            session_id=claim.session_id,
            seat_id=claim.seat_id,
            user_id=user_id,
        )
        self._claims_by_token[token] = updated
        logger.info(
            "[PlayerTokenService] token 已认领：session=%s seat=%s user=%s",
            updated.session_id,
            updated.seat_id,
            updated.user_id,
        )
        return updated

    def token_for_seat(self, session_id: str, seat_id: str) -> str | None:
        """查询指定 session/seat 的 token。"""
        return self._tokens_by_seat.get((session_id, seat_id))

    def dump(self) -> dict[str, object]:
        """导出 token 映射，供持久化存储使用。"""
        return {
            "claims_by_token": {
                token: {
                    "session_id": claim.session_id,
                    "seat_id": claim.seat_id,
                    "user_id": claim.user_id,
                }
                for token, claim in self._claims_by_token.items()
            },
            "tokens_by_seat": [
                {"session_id": session_id, "seat_id": seat_id, "token": token}
                for (session_id, seat_id), token in self._tokens_by_seat.items()
            ],
        }

    def load(self, data: dict[str, object]) -> None:
        """从持久化字典恢复 token 映射。"""
        assert isinstance(data, dict), "token data 必须是 dict"
        self._claims_by_token.clear()
        self._tokens_by_seat.clear()
        raw_claims = data.get("claims_by_token") or {}
        assert isinstance(raw_claims, dict), "claims_by_token 必须是 dict"
        for token, raw_claim in raw_claims.items():
            assert isinstance(raw_claim, dict), "claim 必须是 dict"
            self._claims_by_token[str(token)] = PlayerClaim(
                session_id=str(raw_claim.get("session_id") or ""),
                seat_id=str(raw_claim.get("seat_id") or ""),
                user_id=raw_claim.get("user_id"),
            )
        raw_seats = data.get("tokens_by_seat") or []
        assert isinstance(raw_seats, list), "tokens_by_seat 必须是 list"
        for item in raw_seats:
            assert isinstance(item, dict), "tokens_by_seat item 必须是 dict"
            key = (str(item.get("session_id") or ""), str(item.get("seat_id") or ""))
            token = str(item.get("token") or "")
            assert key[0] and key[1] and token, "token seat 映射不完整"
            self._tokens_by_seat[key] = token
