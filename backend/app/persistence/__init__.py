"""SQLAlchemy 持久化公共接口。"""

from .database import Database
from .migrations import DatabaseMigrationError, upgrade_database
from .models import (
    AgentMemorySnapshot,
    AgentSession,
    Base,
    Game,
    GameEvent,
    LLMCall,
    LLMMessage,
    PhaseAction,
    PhaseCommand,
    Player,
    RoleAssignment,
    Vote,
    new_uuid,
    utc_now,
)
from .repository import (
    CommandExecution,
    EntityNotFoundError,
    PersistenceService,
    Repository,
    StaleGameVersionError,
    sanitize_for_storage,
)

__all__ = [
    "AgentMemorySnapshot",
    "AgentSession",
    "Base",
    "CommandExecution",
    "Database",
    "DatabaseMigrationError",
    "EntityNotFoundError",
    "Game",
    "GameEvent",
    "LLMCall",
    "LLMMessage",
    "PersistenceService",
    "PhaseAction",
    "PhaseCommand",
    "Player",
    "Repository",
    "RoleAssignment",
    "StaleGameVersionError",
    "Vote",
    "new_uuid",
    "sanitize_for_storage",
    "utc_now",
    "upgrade_database",
]
