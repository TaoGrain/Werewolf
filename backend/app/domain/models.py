"""可 JSON 序列化的游戏状态、事件和命令结果。"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .actions import (
    SeerAction,
    SpeechAction,
    VoteAction,
    WitchAction,
    WolfAction,
    WolfDiscussionAction,
)
from .enums import (
    Alignment,
    DeathCause,
    EventType,
    GamePhase,
    PlayerStatus,
    Role,
    Visibility,
    Winner,
    WinReason,
)
from .rules import Ruleset


class Player(BaseModel):
    model_config = ConfigDict(validate_assignment=True)

    id: str
    seat: int = Field(ge=1)
    name: str = Field(min_length=1)
    personality: dict[str, Any] = Field(default_factory=dict)
    status: PlayerStatus = PlayerStatus.ALIVE
    death_cause: DeathCause | None = None
    death_causes: list[DeathCause] = Field(default_factory=list)
    death_day: int | None = None

    @model_validator(mode="after")
    def synchronize_death_causes(self) -> "Player":
        causes = list(dict.fromkeys(self.death_causes))
        if self.death_cause is not None and self.death_cause not in causes:
            causes.append(self.death_cause)
        if causes != self.death_causes:
            object.__setattr__(self, "death_causes", causes)
        if self.death_cause is None and causes:
            # 旧的单值字段保留最后应用的死因，完整集合以 death_causes 为准。
            object.__setattr__(self, "death_cause", causes[-1])
        return self


class RoleState(BaseModel):
    model_config = ConfigDict(validate_assignment=True)

    antidote_available: bool = True
    poison_available: bool = True
    seer_results: dict[str, Alignment] = Field(default_factory=dict)
    hunter_shot_available: bool = True


class GameConfig(BaseModel):
    model_config = ConfigDict(validate_assignment=True)

    random_seed: int = 0
    ruleset: Ruleset = Field(default_factory=Ruleset.mvp_v1)
    god_view: bool = False
    allow_private_inspection: bool = False
    model_key: str | None = None
    thinking_enabled: bool = False
    speech_temperature: float = Field(default=0.55, ge=0, le=2)
    vote_temperature: float = Field(default=0.35, ge=0, le=2)
    action_temperature: float = Field(default=0.5, ge=0, le=2)


class GameEvent(BaseModel):
    sequence: int = Field(ge=1)
    event_id: str
    type: EventType
    day: int = Field(ge=0)
    phase: GamePhase
    visibility: Visibility
    audience_player_ids: list[str] = Field(default_factory=list)
    payload: dict[str, Any] = Field(default_factory=dict)
    command_id: str


class PendingHunterAction(BaseModel):
    model_config = ConfigDict(frozen=True)

    hunter_id: str = Field(min_length=1)
    trigger_causes: list[DeathCause] = Field(min_length=1)
    source_phase: GamePhase
    resume_phase: GamePhase


class WinResult(BaseModel):
    winner: Winner
    reason: str
    alive_wolves: int
    alive_good: int
    reason_code: WinReason | None = None
    alive_gods: int | None = Field(default=None, ge=0)
    alive_villagers: int | None = Field(default=None, ge=0)


class CommandReceipt(BaseModel):
    operation: str
    fingerprint: str
    phase_before: GamePhase
    phase_after: GamePhase
    events: list[GameEvent] = Field(default_factory=list)


class Game(BaseModel):
    """完整领域状态；应用层必须按权限构造视图，不应直接发送给 Agent。"""

    model_config = ConfigDict(validate_assignment=True)

    id: str
    config: GameConfig
    phase: GamePhase = GamePhase.LOBBY
    day: int = 0
    players: list[Player]
    role_assignments: dict[str, Role] = Field(default_factory=dict)
    role_states: dict[str, RoleState] = Field(default_factory=dict)
    wolf_discussion: dict[str, WolfDiscussionAction] = Field(default_factory=dict)
    wolf_actions: dict[str, WolfAction] = Field(default_factory=dict)
    wolf_target_id: str | None = None
    seer_action: SeerAction | None = None
    witch_action: WitchAction | None = None
    night_death_ids: list[str] = Field(default_factory=list)
    speeches: dict[str, SpeechAction] = Field(default_factory=dict)
    runoff_speeches: dict[str, SpeechAction] = Field(default_factory=dict)
    votes: dict[str, VoteAction] = Field(default_factory=dict)
    vote_round: int = 1
    runoff_candidate_ids: list[str] = Field(default_factory=list)
    pending_exile_id: str | None = None
    pending_hunter_action: PendingHunterAction | None = None
    last_words_submitted: bool = False
    winner: WinResult | None = None
    events: list[GameEvent] = Field(default_factory=list)
    processed_commands: dict[str, CommandReceipt] = Field(default_factory=dict)

    def player(self, player_id: str) -> Player | None:
        return next((player for player in self.players if player.id == player_id), None)

    def alive_players(self) -> list[Player]:
        return sorted(
            (player for player in self.players if player.status == PlayerStatus.ALIVE),
            key=lambda player: player.seat,
        )


class ActionValidationResult(BaseModel):
    valid: bool
    code: str | None = None
    message: str | None = None


class EngineResult(BaseModel):
    game: Game
    events: list[GameEvent] = Field(default_factory=list)
    idempotent_replay: bool = False
