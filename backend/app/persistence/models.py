"""狼人杀持久化模型。

所有公开标识均使用 UUID 字符串；JSON 快照隔离持久化层与领域对象。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.engine import Dialect
from sqlalchemy.dialects.mysql import DATETIME as MySQLDateTime
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator

from app.persistence.column_comments import get_column_comment


def new_uuid() -> str:
    return str(uuid4())


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class UTCDateTime(TypeDecorator[datetime]):
    """跨 SQLite/MySQL 保证写入和读出均为 UTC 感知时间。"""

    impl = DateTime
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect):  # type: ignore[no-untyped-def]
        if dialect.name == "mysql":
            return dialect.type_descriptor(MySQLDateTime(fsp=6))
        return dialect.type_descriptor(DateTime(timezone=True))

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("datetime 必须包含时区")
        normalized = value.astimezone(timezone.utc)
        if dialect.name == "sqlite":
            return normalized.replace(tzinfo=None)
        return normalized

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utc_now, onupdate=utc_now, nullable=False
    )


class Game(TimestampMixin, Base):
    __tablename__ = "games"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ACTIVE", index=True)
    phase: Mapped[str] = mapped_column(String(32), nullable=False, default="LOBBY", index=True)
    day: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rules_version: Mapped[str] = mapped_column(String(64), nullable=False, default="mvp-v1")
    random_seed: Mapped[int | None] = mapped_column(Integer)
    config: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    state_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    next_event_sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    players: Mapped[list[Player]] = relationship(back_populates="game", cascade="all, delete-orphan")

    __table_args__ = (
        CheckConstraint("day >= 0", name="ck_games_day_nonnegative"),
        CheckConstraint("version >= 1", name="ck_games_version_positive"),
        CheckConstraint("next_event_sequence >= 1", name="ck_games_event_sequence_positive"),
    )


class Player(TimestampMixin, Base):
    __tablename__ = "players"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    game_id: Mapped[str] = mapped_column(
        ForeignKey("games.id", ondelete="CASCADE"), nullable=False, index=True
    )
    seat: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    alive: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    personality: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    game: Mapped[Game] = relationship(back_populates="players")

    __table_args__ = (
        UniqueConstraint("game_id", "seat", name="uq_players_game_seat"),
        CheckConstraint("seat >= 1", name="ck_players_seat_positive"),
    )


class RoleAssignment(TimestampMixin, Base):
    __tablename__ = "role_assignments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    game_id: Mapped[str] = mapped_column(
        ForeignKey("games.id", ondelete="CASCADE"), nullable=False, index=True
    )
    player_id: Mapped[str] = mapped_column(
        ForeignKey("players.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    private_state: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    __table_args__ = (
        UniqueConstraint("game_id", "player_id", name="uq_roles_game_player"),
        Index("ix_roles_game_role", "game_id", "role"),
    )


class AgentSession(TimestampMixin, Base):
    __tablename__ = "agent_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    game_id: Mapped[str] = mapped_column(
        ForeignKey("games.id", ondelete="CASCADE"), nullable=False, index=True
    )
    player_id: Mapped[str] = mapped_column(
        ForeignKey("players.id", ondelete="CASCADE"), nullable=False
    )
    session_key: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    state: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    last_message_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    __table_args__ = (
        UniqueConstraint("game_id", "player_id", name="uq_agent_sessions_game_player"),
    )


class LLMCall(Base):
    __tablename__ = "llm_calls"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    request_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    provider_request_id: Mapped[str | None] = mapped_column(String(128), index=True)
    game_id: Mapped[str] = mapped_column(
        ForeignKey("games.id", ondelete="CASCADE"), nullable=False, index=True
    )
    player_id: Mapped[str | None] = mapped_column(
        ForeignKey("players.id", ondelete="SET NULL"), index=True
    )
    agent_session_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_sessions.id", ondelete="SET NULL"), index=True
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    adapter_version: Mapped[str] = mapped_column(String(64), nullable=False)
    day: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    phase: Mapped[str] = mapped_column(String(32), nullable=False)
    call_type: Mapped[str] = mapped_column(String(64), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    request_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    response_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    final_text: Mapped[str | None] = mapped_column(Text)
    parsed_result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    validation_errors: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    prompt_version: Mapped[str | None] = mapped_column(String(64))
    rules_version: Mapped[str | None] = mapped_column(String(64))
    schema_version: Mapped[str | None] = mapped_column(String(64))
    usage: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    reasoning_tokens: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="STARTED", index=True)
    error_type: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    safety_flagged: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    __table_args__ = (
        CheckConstraint("day >= 0", name="ck_llm_calls_day_nonnegative"),
        CheckConstraint("attempt >= 1", name="ck_llm_calls_attempt_positive"),
        Index("ix_llm_calls_lookup", "game_id", "player_id", "phase", "call_type"),
    )


class LLMMessage(Base):
    __tablename__ = "llm_messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    llm_call_id: Mapped[str] = mapped_column(
        ForeignKey("llm_calls.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(24), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str | None] = mapped_column(String(100))
    message_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)

    __table_args__ = (
        UniqueConstraint("llm_call_id", "ordinal", name="uq_llm_messages_call_ordinal"),
        CheckConstraint("ordinal >= 0", name="ck_llm_messages_ordinal_nonnegative"),
    )


class GameEvent(Base):
    __tablename__ = "game_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    game_id: Mapped[str] = mapped_column(
        ForeignKey("games.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    rules_version: Mapped[str] = mapped_column(
        String(64), nullable=False, default="mvp-v1", server_default="mvp-v1"
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    day: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    phase: Mapped[str] = mapped_column(String(32), nullable=False)
    visibility: Mapped[str] = mapped_column(String(32), nullable=False, default="PUBLIC")
    actor_player_id: Mapped[str | None] = mapped_column(
        ForeignKey("players.id", ondelete="SET NULL"), index=True
    )
    llm_call_id: Mapped[str | None] = mapped_column(
        ForeignKey("llm_calls.id", ondelete="SET NULL"), index=True
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)

    __table_args__ = (
        UniqueConstraint("game_id", "sequence", name="uq_game_events_game_sequence"),
        CheckConstraint("sequence >= 1", name="ck_game_events_sequence_positive"),
        CheckConstraint("day >= 0", name="ck_game_events_day_nonnegative"),
        Index("ix_game_events_timeline", "game_id", "sequence"),
    )


class PhaseCommand(TimestampMixin, Base):
    __tablename__ = "phase_commands"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    game_id: Mapped[str] = mapped_column(
        ForeignKey("games.id", ondelete="CASCADE"), nullable=False, index=True
    )
    command_id: Mapped[str] = mapped_column(String(128), nullable=False)
    command_type: Mapped[str] = mapped_column(String(64), nullable=False)
    phase: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="PENDING", index=True)
    request_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    result_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    __table_args__ = (
        UniqueConstraint("game_id", "command_id", name="uq_phase_commands_game_command"),
    )


class PhaseAction(Base):
    __tablename__ = "phase_actions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    game_id: Mapped[str] = mapped_column(
        ForeignKey("games.id", ondelete="CASCADE"), nullable=False, index=True
    )
    phase_command_id: Mapped[str | None] = mapped_column(
        ForeignKey("phase_commands.id", ondelete="SET NULL"), index=True
    )
    player_id: Mapped[str | None] = mapped_column(
        ForeignKey("players.id", ondelete="SET NULL"), index=True
    )
    llm_call_id: Mapped[str | None] = mapped_column(
        ForeignKey("llm_calls.id", ondelete="SET NULL"), index=True
    )
    day: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    phase: Mapped[str] = mapped_column(String(32), nullable=False)
    action_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    rules_version: Mapped[str] = mapped_column(
        String(64), nullable=False, default="mvp-v1", server_default="mvp-v1"
    )
    is_valid: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    validation_errors: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)

    __table_args__ = (
        CheckConstraint("day >= 0", name="ck_phase_actions_day_nonnegative"),
        Index("ix_phase_actions_lookup", "game_id", "day", "phase", "action_type"),
    )


class Vote(Base):
    __tablename__ = "votes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    game_id: Mapped[str] = mapped_column(
        ForeignKey("games.id", ondelete="CASCADE"), nullable=False, index=True
    )
    phase_action_id: Mapped[str | None] = mapped_column(
        ForeignKey("phase_actions.id", ondelete="SET NULL"), index=True
    )
    voter_player_id: Mapped[str] = mapped_column(
        ForeignKey("players.id", ondelete="CASCADE"), nullable=False, index=True
    )
    target_player_id: Mapped[str | None] = mapped_column(
        ForeignKey("players.id", ondelete="CASCADE"), nullable=True, index=True
    )
    abstained: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    day: Mapped[int] = mapped_column(Integer, nullable=False)
    phase: Mapped[str] = mapped_column(String(32), nullable=False, default="DAY_VOTING")
    round: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    public_reason: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float | None]
    is_valid: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "game_id", "day", "phase", "round", "voter_player_id",
            name="uq_votes_one_per_round",
        ),
        CheckConstraint("day >= 1", name="ck_votes_day_positive"),
        CheckConstraint("round >= 1", name="ck_votes_round_positive"),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_votes_confidence_range",
        ),
        CheckConstraint(
            "(abstained = true AND target_player_id IS NULL) OR "
            "(abstained = false AND target_player_id IS NOT NULL)",
            name="ck_votes_abstention_target",
        ),
        Index("ix_votes_tally", "game_id", "day", "round", "target_player_id"),
    )


class AgentMemorySnapshot(Base):
    __tablename__ = "agent_memory_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    game_id: Mapped[str] = mapped_column(
        ForeignKey("games.id", ondelete="CASCADE"), nullable=False, index=True
    )
    player_id: Mapped[str] = mapped_column(
        ForeignKey("players.id", ondelete="CASCADE"), nullable=False, index=True
    )
    agent_session_id: Mapped[str] = mapped_column(
        ForeignKey("agent_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    llm_call_id: Mapped[str | None] = mapped_column(
        ForeignKey("llm_calls.id", ondelete="SET NULL"), index=True
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    day: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    phase: Mapped[str] = mapped_column(String(32), nullable=False)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)

    __table_args__ = (
        UniqueConstraint("agent_session_id", "sequence", name="uq_memory_session_sequence"),
        CheckConstraint("sequence >= 1", name="ck_memory_sequence_positive"),
        CheckConstraint("day >= 0", name="ck_memory_day_nonnegative"),
        Index("ix_memory_lookup", "game_id", "player_id", "sequence"),
    )


for _table in Base.metadata.tables.values():
    for _column in _table.columns:
        if _column.name == "id" and _column.primary_key:
            continue
        _column.comment = get_column_comment(_table.name, _column.name)
