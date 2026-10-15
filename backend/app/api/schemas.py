from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class CreateGameRequest(BaseModel):
    name: str | None = None
    ruleset_version: Literal["mvp-v1", "classic-9-hunter-v1"] = "mvp-v1"
    player_count: int | None = Field(default=None, ge=6, le=9)
    player_names: list[str] | None = None
    random_seed: int | None = None
    seed: int | None = None
    god_mode: bool = False
    allow_private_inspection: bool = False
    model: Literal["minimaxM3", "deepseekv4pro", "deepseekv4flash"] | None = None
    thinking_enabled: bool = False
    speech_temperature: float = Field(default=0.55, ge=0, le=2)
    vote_temperature: float = Field(default=0.35, ge=0, le=2)
    action_temperature: float = Field(default=0.5, ge=0, le=2)


class CommandRequest(BaseModel):
    command_id: str | None = Field(default=None, min_length=1, max_length=128)


class AIReviewRequest(CommandRequest):
    model: Literal["minimaxM3", "deepseekv4pro", "deepseekv4flash"] | None = None


class CommandResponse(BaseModel):
    command_id: str
    status: str
    replayed: bool = False
    result: dict | None = None
