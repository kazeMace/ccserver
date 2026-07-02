"""Pydantic schemas for Drama Engine Web service."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class CreateSessionRequest(BaseModel):
    """创建 session 请求。"""

    game_id: str = Field(..., min_length=1)
    script_path: str | None = None
    seat_ids: list[str] = Field(default_factory=list)
    human_seat_ids: list[str] = Field(default_factory=list)
    params: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class PlayerInputRequest(BaseModel):
    """玩家提交动作请求。"""

    token: str = Field(..., min_length=1)
    data: dict[str, Any] | None = None
    text: str = ""


class ClaimSeatRequest(BaseModel):
    """玩家认领 seat 请求。"""

    token: str = Field(..., min_length=1)
    user_id: str = Field(..., min_length=1)
