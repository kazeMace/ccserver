"""Pydantic schemas for Drama Engine admin service."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class UpdateScriptRequest(BaseModel):
    """Update script text request."""

    content: str = Field(..., min_length=1)


class PromoteScriptRequest(BaseModel):
    """Promote script request."""

    force: bool = False


class PlaytestCreateRequest(BaseModel):
    """Create playtest request."""

    mode: str = "dry_run"
    human_player_count: int = Field(default=0, ge=0)
    step_mode: bool = True


class PlaytestStepRequest(BaseModel):
    """Advance playtest request."""

    count: int = Field(default=1, ge=1)


class PluginRunRequest(BaseModel):
    """Run script plugin request."""

    input: dict[str, Any] = Field(default_factory=dict)


class GenerateScriptRequest(BaseModel):
    """Natural-language script generation request."""

    prompt: str = Field(..., min_length=1)
    materials: list[str] = Field(default_factory=list)
    base_script_id: str = ""
    options: dict[str, Any] = Field(default_factory=dict)
