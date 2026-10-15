"""完全不依赖数据库、Web 框架或 LLM 的确定性状态机。"""

from __future__ import annotations

import hashlib
import json
import random
from collections import Counter
from typing import Any, Callable
from uuid import NAMESPACE_URL, uuid4, uuid5

from app.domain.actions import (
    HunterAction,
    LastWordsAction,
    NightAction,
    SeerAction,
    SpeechAction,
    VoteAction,
    WitchAction,
    WolfAction,
    WolfDiscussionAction,
)
from app.domain.enums import (
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
from app.domain.errors import GameRuleError
from app.domain.models import (
    ActionValidationResult,
    CommandReceipt,
    EngineResult,
    Game,
    GameConfig,
    GameEvent,
    PendingHunterAction,
    Player,
    RoleState,
    WinResult,
)


class GameEngine:
    """接收当前状态和命令，返回新状态以及本次追加事件。"""

    def create_game(
        self,
        player_names: list[str],
        config: GameConfig | None = None,
        game_id: str | None = None,
        command_id: str = "create",
    ) -> EngineResult:
        config = config or GameConfig()
        if len(player_names) != config.ruleset.player_count:
            raise GameRuleError(
                "INVALID_PLAYER_COUNT",
                f"规则要求 {config.ruleset.player_count} 名玩家",
            )
        normalized = [name.strip() for name in player_names]
        if any(not name for name in normalized):
            raise GameRuleError("INVALID_PLAYER_NAME", "玩家名称不能为空")
        game_id = game_id or str(uuid4())
        game = Game(
            id=game_id,
            config=config,
            players=[
                Player(
                    id=str(uuid5(NAMESPACE_URL, f"{game_id}:player:{seat}")),
                    seat=seat,
                    name=name,
                )
                for seat, name in enumerate(normalized, start=1)
            ],
        )
        event = self._emit(
            game,
            EventType.GAME_CREATED,
            command_id,
            Visibility.PUBLIC,
            {"player_count": len(game.players), "ruleset_version": config.ruleset.version},
        )
        game.processed_commands[command_id] = CommandReceipt(
            operation="create_game",
            fingerprint=self._fingerprint("create_game", {"names": normalized}),
            phase_before=GamePhase.LOBBY,
            phase_after=GamePhase.LOBBY,
            events=[event],
        )
        return EngineResult(game=game, events=[event])

    def start_game(self, game: Game, command_id: str) -> EngineResult:
        payload = {"seed": game.config.random_seed}

        def apply(next_game: Game, events: list[GameEvent]) -> None:
            self._require_phase(next_game, GamePhase.LOBBY)
            roles: list[Role] = []
            for role, count in next_game.config.ruleset.role_counts.items():
                roles.extend([role] * count)
            random.Random(next_game.config.random_seed).shuffle(roles)
            next_game.role_assignments = {
                player.id: role
                for player, role in zip(sorted(next_game.players, key=lambda p: p.seat), roles)
            }
            next_game.role_states = {
                player.id: RoleState() for player in next_game.players
            }
            events.append(
                self._emit(
                    next_game,
                    EventType.ROLES_ASSIGNED,
                    command_id,
                    Visibility.HOST_PRIVATE,
                    {
                        "assignments": {
                            player_id: role.value
                            for player_id, role in next_game.role_assignments.items()
                        },
                        "random_seed": next_game.config.random_seed,
                    },
                )
            )
            wolves = sorted(
                player_id
                for player_id, role in next_game.role_assignments.items()
                if role == Role.WOLF
            )
            for player in next_game.players:
                role = next_game.role_assignments[player.id]
                private_payload: dict[str, Any] = {"player_id": player.id, "role": role.value}
                if role == Role.WOLF:
                    private_payload["wolf_teammate_ids"] = [
                        player_id for player_id in wolves if player_id != player.id
                    ]
                events.append(
                    self._emit(
                        next_game,
                        EventType.ROLES_ASSIGNED,
                        command_id,
                        Visibility.PLAYER_PRIVATE,
                        private_payload,
                        [player.id],
                    )
                )
            self._change_phase(next_game, GamePhase.ROLE_ASSIGNED, command_id, events)

        return self._run(game, command_id, "start_game", payload, apply)

    def submit_wolf_action(
        self, game: Game, action: WolfAction, command_id: str
    ) -> EngineResult:
        def apply(next_game: Game, events: list[GameEvent]) -> None:
            self._raise_if_invalid(self._validate_wolf(next_game, action))
            next_game.wolf_actions[action.actor_id] = action
            events.append(
                self._emit(
                    next_game,
                    EventType.WOLF_ACTION_SUBMITTED,
                    command_id,
                    Visibility.WOLF_TEAM_PRIVATE,
                    action.model_dump(mode="json"),
                    self._living_role_ids(next_game, Role.WOLF),
                )
            )

        return self._run_action(game, action, command_id, "submit_wolf_action", apply)

    def submit_wolf_discussion(
        self, game: Game, action: WolfDiscussionAction, command_id: str
    ) -> EngineResult:
        def apply(next_game: Game, events: list[GameEvent]) -> None:
            self._raise_if_invalid(self._validate_wolf_discussion(next_game, action))
            next_game.wolf_discussion[action.actor_id] = action
            events.append(
                self._emit(
                    next_game,
                    EventType.WOLF_DISCUSSION_MESSAGE,
                    command_id,
                    Visibility.WOLF_TEAM_PRIVATE,
                    action.model_dump(mode="json"),
                    self._living_role_ids(next_game, Role.WOLF),
                )
            )

        return self._run_action(game, action, command_id, "submit_wolf_discussion", apply)

    def submit_seer_action(
        self, game: Game, action: SeerAction, command_id: str
    ) -> EngineResult:
        def apply(next_game: Game, events: list[GameEvent]) -> None:
            self._raise_if_invalid(self._validate_seer(next_game, action))
            next_game.seer_action = action
            target_role = next_game.role_assignments[action.target_id]
            alignment = Alignment.WOLF if target_role == Role.WOLF else Alignment.GOOD
            next_game.role_states[action.actor_id].seer_results[action.target_id] = alignment
            events.append(
                self._emit(
                    next_game,
                    EventType.SEER_INSPECTION,
                    command_id,
                    Visibility.PLAYER_PRIVATE,
                    {
                        "actor_id": action.actor_id,
                        "target_id": action.target_id,
                        "alignment": alignment.value,
                        "reason": action.reason,
                    },
                    [action.actor_id],
                )
            )

        return self._run_action(game, action, command_id, "submit_seer_action", apply)

    def submit_witch_action(
        self, game: Game, action: WitchAction, command_id: str
    ) -> EngineResult:
        def apply(next_game: Game, events: list[GameEvent]) -> None:
            self._raise_if_invalid(self._validate_witch(next_game, action))
            state = next_game.role_states[action.actor_id]
            if action.rescue_target_id is not None:
                state.antidote_available = False
            if action.poison_target_id is not None:
                state.poison_available = False
            next_game.witch_action = action
            events.append(
                self._emit(
                    next_game,
                    EventType.WITCH_ACTION,
                    command_id,
                    Visibility.PLAYER_PRIVATE,
                    action.model_dump(mode="json"),
                    [action.actor_id],
                )
            )

        return self._run_action(game, action, command_id, "submit_witch_action", apply)

    def submit_hunter_action(
        self, game: Game, action: HunterAction, command_id: str
    ) -> EngineResult:
        def apply(next_game: Game, events: list[GameEvent]) -> None:
            self._raise_if_invalid(self._validate_hunter(next_game, action))
            pending = next_game.pending_hunter_action
            assert pending is not None

            next_game.role_states[action.actor_id].hunter_shot_available = False
            events.append(
                self._emit(
                    next_game,
                    EventType.HUNTER_ACTION_SUBMITTED,
                    command_id,
                    Visibility.HOST_PRIVATE,
                    action.model_dump(mode="json"),
                )
            )

            if action.action == "shoot":
                target_id = action.target_id
                assert target_id is not None
                events.append(
                    self._emit(
                        next_game,
                        EventType.HUNTER_SHOT,
                        command_id,
                        Visibility.PUBLIC,
                        {
                            "hunter_id": action.actor_id,
                            "target_id": target_id,
                            "reason": action.reason,
                        },
                    )
                )
                self._kill(next_game, target_id, DeathCause.HUNTER_SHOT)
                events.append(
                    self._emit(
                        next_game,
                        EventType.PLAYER_DIED,
                        command_id,
                        Visibility.PUBLIC,
                        self._death_payload(next_game, target_id),
                    )
                )
            else:
                events.append(
                    self._emit(
                        next_game,
                        EventType.HUNTER_PASSED,
                        command_id,
                        Visibility.PUBLIC,
                        {"hunter_id": action.actor_id, "reason": action.reason},
                    )
                )

            next_game.pending_hunter_action = None
            if (
                len(next_game.role_assignments) == len(next_game.players)
                and self._finish_if_won(next_game, command_id, events)
            ):
                return
            self._resume_after_hunter(next_game, pending, command_id, events)

        return self._run_action(game, action, command_id, "submit_hunter_action", apply)

    def submit_speech(
        self, game: Game, action: SpeechAction, command_id: str
    ) -> EngineResult:
        def apply(next_game: Game, events: list[GameEvent]) -> None:
            self._raise_if_invalid(self._validate_speech(next_game, action))
            if next_game.phase == GamePhase.DAY_RUNOFF_DISCUSSION:
                next_game.runoff_speeches[action.actor_id] = action
            else:
                next_game.speeches[action.actor_id] = action
            events.append(
                self._emit(
                    next_game,
                    EventType.SPEECH,
                    command_id,
                    Visibility.PUBLIC,
                    {
                        "actor_id": action.actor_id,
                        "content": action.content,
                        "reason": action.reason,
                        "cited_player_ids": action.cited_player_ids,
                        "focus_player_ids": action.focus_player_ids,
                        "key_conflicts": action.key_conflicts,
                        "vote_intent": action.vote_intent,
                        "vote_target_id": action.vote_target_id,
                        "fallback_target_id": action.fallback_target_id,
                        "decision_condition": action.decision_condition,
                        "round_summary": action.round_summary,
                    },
                )
            )

        return self._run_action(game, action, command_id, "submit_speech", apply)

    def submit_vote(
        self, game: Game, action: VoteAction, command_id: str
    ) -> EngineResult:
        def apply(next_game: Game, events: list[GameEvent]) -> None:
            self._raise_if_invalid(self._validate_vote(next_game, action))
            next_game.votes[action.actor_id] = action
            # 投票在全部收齐前保持私密，结算时统一公开。
            events.append(
                self._emit(
                    next_game,
                    EventType.VOTE_SUBMITTED,
                    command_id,
                    Visibility.HOST_PRIVATE,
                    {
                        "actor_id": action.actor_id,
                        "round": next_game.vote_round,
                    },
                )
            )

        return self._run_action(game, action, command_id, "submit_vote", apply)

    def submit_last_words(
        self, game: Game, action: LastWordsAction, command_id: str
    ) -> EngineResult:
        def apply(next_game: Game, events: list[GameEvent]) -> None:
            self._raise_if_invalid(self._validate_last_words(next_game, action))
            next_game.last_words_submitted = True
            events.append(
                self._emit(
                    next_game,
                    EventType.LAST_WORDS,
                    command_id,
                    Visibility.PUBLIC,
                    {
                        "actor_id": action.actor_id,
                        "content": action.content,
                        "reason": action.reason,
                    },
                )
            )

        return self._run_action(game, action, command_id, "submit_last_words", apply)

    def complete_phase(self, game: Game, command_id: str) -> EngineResult:
        # 操作名不编码当前阶段：首次成功后状态已经变化，HTTP 重试仍须命中原回执。
        operation = "complete_phase"

        def apply(next_game: Game, events: list[GameEvent]) -> None:
            phase = next_game.phase
            # 规则升级后，旧对局可能已经越过原本应触发终局的结算点。
            # 每次主持人推进时先补做胜负检查，使这类对局无需再走完整阶段。
            if self._finish_if_won(next_game, command_id, events):
                return
            handlers: dict[GamePhase, Callable[[Game, str, list[GameEvent]], None]] = {
                GamePhase.ROLE_ASSIGNED: self._complete_role_assigned,
                GamePhase.NIGHT_WOLF_DISCUSSION: self._complete_wolf_discussion,
                GamePhase.NIGHT_WOLF: self._complete_wolf,
                GamePhase.NIGHT_SEER: self._complete_seer,
                GamePhase.NIGHT_WITCH: self._complete_witch,
                GamePhase.NIGHT_RESOLUTION: self._complete_night_resolution,
                GamePhase.DAY_ANNOUNCEMENT: self._complete_announcement,
                GamePhase.DAY_DISCUSSION: self._complete_discussion,
                GamePhase.DAY_RUNOFF_DISCUSSION: self._complete_runoff_discussion,
                GamePhase.DAY_VOTING: self._complete_voting,
                GamePhase.DAY_RESOLUTION: self._complete_day_resolution,
            }
            handler = handlers.get(phase)
            if handler is None:
                raise GameRuleError("PHASE_NOT_ADVANCEABLE", f"阶段 {phase.value} 不能推进")
            handler(next_game, command_id, events)

        return self._run(game, command_id, operation, {}, apply)

    advance = complete_phase

    def validate_action(
        self,
        game: Game,
        action: NightAction | HunterAction | SpeechAction | VoteAction | LastWordsAction,
    ) -> ActionValidationResult:
        if isinstance(action, WolfDiscussionAction):
            return self._validate_wolf_discussion(game, action)
        if isinstance(action, WolfAction):
            return self._validate_wolf(game, action)
        if isinstance(action, SeerAction):
            return self._validate_seer(game, action)
        if isinstance(action, WitchAction):
            return self._validate_witch(game, action)
        if isinstance(action, HunterAction):
            return self._validate_hunter(game, action)
        if isinstance(action, SpeechAction):
            return self._validate_speech(game, action)
        if isinstance(action, VoteAction):
            return self._validate_vote(game, action)
        return self._validate_last_words(game, action)

    def _check_win_parity_legacy(self, game: Game) -> WinResult | None:
        alive_wolves = sum(
            1
            for player in game.alive_players()
            if game.role_assignments.get(player.id) == Role.WOLF
        )
        alive_good = len(game.alive_players()) - alive_wolves
        if alive_wolves == 0:
            return WinResult(
                winner=Winner.GOOD,
                reason="所有狼人均已死亡",
                alive_wolves=0,
                alive_good=alive_good,
            )
        if game.config.ruleset.wolves_win_at_parity and alive_wolves >= alive_good:
            return WinResult(
                winner=Winner.WOLVES,
                reason="存活狼人人数大于或等于存活好人人数",
                alive_wolves=alive_wolves,
                alive_good=alive_good,
            )
        return None

    def check_win(self, game: Game) -> WinResult | None:
        # 猎人的合法反应属于当前死亡结算链，未处理前不得提前结束游戏。
        if game.pending_hunter_action is not None:
            return None

        alive = game.alive_players()
        alive_wolves = sum(
            1 for player in alive if game.role_assignments.get(player.id) == Role.WOLF
        )
        alive_good = len(alive) - alive_wolves

        if game.config.ruleset.win_condition == "slaughter_side":
            god_roles = {Role.SEER, Role.WITCH, Role.HUNTER}
            alive_gods = sum(
                1 for player in alive if game.role_assignments.get(player.id) in god_roles
            )
            alive_villagers = sum(
                1
                for player in alive
                if game.role_assignments.get(player.id) == Role.VILLAGER
            )
            if alive_wolves == 0:
                return WinResult(
                    winner=Winner.GOOD,
                    reason="所有狼人均已死亡",
                    reason_code=WinReason.ALL_WOLVES_ELIMINATED,
                    alive_wolves=0,
                    alive_good=alive_good,
                    alive_gods=alive_gods,
                    alive_villagers=alive_villagers,
                )
            if alive_gods == 0:
                return WinResult(
                    winner=Winner.WOLVES,
                    reason="所有神职均已死亡",
                    reason_code=WinReason.ALL_GODS_ELIMINATED,
                    alive_wolves=alive_wolves,
                    alive_good=alive_good,
                    alive_gods=0,
                    alive_villagers=alive_villagers,
                )
            if alive_villagers == 0:
                return WinResult(
                    winner=Winner.WOLVES,
                    reason="所有平民均已死亡",
                    reason_code=WinReason.ALL_VILLAGERS_ELIMINATED,
                    alive_wolves=alive_wolves,
                    alive_good=alive_good,
                    alive_gods=alive_gods,
                    alive_villagers=0,
                )
            # 经典 9 人局同时采用屠边和人数对等判定；规则模型会把旧快照
            # 中的开关迁移为当前值，使升级前创建的对局也能及时结束。
            if game.config.ruleset.wolves_win_at_parity and alive_wolves >= alive_good:
                return WinResult(
                    winner=Winner.WOLVES,
                    reason="存活狼人人数大于或等于存活好人人数",
                    reason_code=WinReason.WOLVES_REACHED_PARITY,
                    alive_wolves=alive_wolves,
                    alive_good=alive_good,
                    alive_gods=alive_gods,
                    alive_villagers=alive_villagers,
                )
            return None

        return self._check_win_parity_legacy(game)

    # ---- 阶段结算 -----------------------------------------------------

    def _complete_role_assigned(
        self, game: Game, command_id: str, events: list[GameEvent]
    ) -> None:
        game.day = 1
        self._reset_night(game)
        self._enter_night(game, command_id, events)

    def _complete_wolf_discussion(
        self, game: Game, command_id: str, events: list[GameEvent]
    ) -> None:
        wolves = self._living_role_ids(game, Role.WOLF)
        if len(wolves) > 1:
            missing = sorted(set(wolves) - set(game.wolf_discussion))
            if missing:
                raise GameRuleError("MISSING_WOLF_DISCUSSION", f"尚缺少狼人交流：{missing}")
        self._change_phase(game, GamePhase.NIGHT_WOLF, command_id, events)

    def _complete_wolf(
        self, game: Game, command_id: str, events: list[GameEvent]
    ) -> None:
        wolves = self._living_role_ids(game, Role.WOLF)
        missing = sorted(set(wolves) - set(game.wolf_actions))
        if missing:
            raise GameRuleError("MISSING_WOLF_ACTIONS", f"尚缺少狼人行动：{missing}")
        counts = Counter(action.target_id for action in game.wolf_actions.values())
        top = max(counts.values())
        candidates = sorted(target for target, count in counts.items() if count == top)
        if len(candidates) == 1:
            target_id = candidates[0]
            resolution = "single_wolf" if len(wolves) == 1 else "unanimous"
        else:
            target_id = random.Random(
                f"{game.config.random_seed}:wolf:{game.day}"
            ).choice(candidates)
            resolution = "seeded_tie_break"
        game.wolf_target_id = target_id
        events.append(
            self._emit(
                game,
                EventType.WOLF_TARGET_SELECTED,
                command_id,
                Visibility.WOLF_TEAM_PRIVATE,
                {
                    "target_id": target_id,
                    "counts": dict(counts),
                    "tied_candidate_ids": candidates,
                    "resolution": resolution,
                },
                wolves,
            )
        )
        witches = self._living_role_ids(game, Role.WITCH)
        if witches:
            events.append(
                self._emit(
                    game,
                    EventType.WOLF_TARGET_SELECTED,
                    command_id,
                    Visibility.PLAYER_PRIVATE,
                    {"target_id": target_id},
                    witches,
                )
            )
        # 兼容按旧顺序保存于狼人阶段的进行中对局；新顺序下预言家此时已经行动。
        if self._living_role_ids(game, Role.SEER) and game.seer_action is None:
            self._change_phase(game, GamePhase.NIGHT_SEER, command_id, events)
        else:
            self._change_phase(game, GamePhase.NIGHT_WITCH, command_id, events)

    def _complete_seer(
        self, game: Game, command_id: str, events: list[GameEvent]
    ) -> None:
        if self._living_role_ids(game, Role.SEER) and game.seer_action is None:
            raise GameRuleError("MISSING_SEER_ACTION", "存活预言家尚未提交查验")
        # 旧存档可能已完成狼人行动后才进入预言家阶段。
        if game.wolf_target_id is not None:
            self._change_phase(game, GamePhase.NIGHT_WITCH, command_id, events)
        else:
            self._enter_wolf_night(game, command_id, events)

    def _complete_witch(
        self, game: Game, command_id: str, events: list[GameEvent]
    ) -> None:
        if self._living_role_ids(game, Role.WITCH) and game.witch_action is None:
            raise GameRuleError("MISSING_WITCH_ACTION", "存活女巫尚未提交行动（可提交空过）")
        self._change_phase(game, GamePhase.NIGHT_RESOLUTION, command_id, events)

    def _complete_night_resolution(
        self, game: Game, command_id: str, events: list[GameEvent]
    ) -> None:
        deaths: dict[str, list[DeathCause]] = {}
        rescued = game.witch_action.rescue_target_id if game.witch_action else None
        poisoned = game.witch_action.poison_target_id if game.witch_action else None

        if game.wolf_target_id is not None and game.wolf_target_id != rescued:
            deaths.setdefault(game.wolf_target_id, []).append(DeathCause.WOLF_ATTACK)
        if poisoned is not None:
            deaths.setdefault(poisoned, []).append(DeathCause.WITCH_POISON)

        ordered_deaths = sorted(
            deaths,
            key=lambda player_id: game.player(player_id).seat,  # type: ignore[union-attr]
        )
        for player_id in ordered_deaths:
            causes = deaths[player_id]
            self._kill(game, player_id, causes)
            events.append(
                self._emit(
                    game,
                    EventType.PLAYER_DIED,
                    command_id,
                    Visibility.HOST_PRIVATE,
                    self._death_payload(game, player_id),
                )
            )
            self._queue_hunter_reaction(
                game,
                player_id,
                causes,
                source_phase=GamePhase.NIGHT_RESOLUTION,
                resume_phase=GamePhase.DAY_DISCUSSION,
            )

        game.night_death_ids = ordered_deaths
        events.append(
            self._emit(
                game,
                EventType.NIGHT_RESOLVED,
                command_id,
                Visibility.HOST_PRIVATE,
                {
                    "death_ids": game.night_death_ids,
                    "death_causes": {
                        player_id: [cause.value for cause in deaths[player_id]]
                        for player_id in ordered_deaths
                    },
                    "rescued_id": rescued,
                },
            )
        )

        # 旧规则维持原有的夜间即时判胜；屠边版等待天亮公告及猎人反应。
        if game.config.ruleset.win_condition == "parity":
            if self._finish_if_won(game, command_id, events):
                return
        self._change_phase(game, GamePhase.DAY_ANNOUNCEMENT, command_id, events)

    def _complete_announcement(
        self, game: Game, command_id: str, events: list[GameEvent]
    ) -> None:
        events.append(
            self._emit(
                game,
                EventType.NIGHT_ANNOUNCED,
                command_id,
                Visibility.PUBLIC,
                {"death_ids": game.night_death_ids},
            )
        )
        if game.pending_hunter_action is not None:
            self._enter_hunter_reaction(game, command_id, events)
            return
        if self._finish_if_won(game, command_id, events):
            return
        game.speeches = {}
        self._change_phase(game, GamePhase.DAY_DISCUSSION, command_id, events)

    def _complete_discussion(
        self, game: Game, command_id: str, events: list[GameEvent]
    ) -> None:
        missing = [player.id for player in game.alive_players() if player.id not in game.speeches]
        if missing:
            raise GameRuleError("MISSING_SPEECHES", f"尚缺少发言：{missing}")
        game.votes = {}
        game.runoff_speeches = {}
        game.vote_round = 1
        game.runoff_candidate_ids = []
        game.pending_exile_id = None
        game.last_words_submitted = False
        self._change_phase(game, GamePhase.DAY_VOTING, command_id, events)

    def _complete_runoff_discussion(
        self, game: Game, command_id: str, events: list[GameEvent]
    ) -> None:
        candidates = [
            player.id
            for player in game.alive_players()
            if player.id in game.runoff_candidate_ids
        ]
        missing = [
            player_id
            for player_id in candidates
            if player_id not in game.runoff_speeches
        ]
        if missing:
            raise GameRuleError("MISSING_RUNOFF_SPEECHES", f"尚缺少 PK 发言：{missing}")
        game.votes = {}
        self._change_phase(game, GamePhase.DAY_VOTING, command_id, events)

    def _complete_voting(
        self, game: Game, command_id: str, events: list[GameEvent]
    ) -> None:
        eligible_voters = self._eligible_voter_ids(game)
        missing = [player_id for player_id in eligible_voters if player_id not in game.votes]
        if missing:
            raise GameRuleError("MISSING_VOTES", f"尚缺少投票：{missing}")
        revealed = [
            {
                "actor_id": action.actor_id,
                "target_id": action.target_id,
                "abstain": action.abstain,
                "reason": action.reason,
                "confidence": action.confidence,
            }
            for action in sorted(
                game.votes.values(), key=lambda value: game.player(value.actor_id).seat  # type: ignore[union-attr]
            )
        ]
        counted_targets = [
            action.target_id
            for action in game.votes.values()
            if not action.abstain and action.target_id is not None
        ]
        counts = Counter(counted_targets)
        abstain_count = sum(action.abstain for action in game.votes.values())
        events.append(
            self._emit(
                game,
                EventType.VOTES_REVEALED,
                command_id,
                Visibility.PUBLIC,
                {
                    "round": game.vote_round,
                    "votes": revealed,
                    "counts": dict(counts),
                    "abstain_count": abstain_count,
                },
            )
        )
        if not counted_targets:
            game.pending_exile_id = None
            if not eligible_voters:
                no_exile_reason = "没有合格的重投票人"
            elif game.vote_round > 1:
                no_exile_reason = "所有合格重投票人均弃票"
            else:
                no_exile_reason = "所有存活玩家均弃票"
            events.append(
                self._emit(
                    game,
                    EventType.NO_EXILE,
                    command_id,
                    Visibility.PUBLIC,
                    {"reason": no_exile_reason},
                )
            )
            if self._finish_if_won(game, command_id, events):
                return
            game.day += 1
            self._reset_night(game)
            self._enter_night(game, command_id, events)
            return
        top = max(counts.values())
        tied = sorted(
            (target for target, count in counts.items() if count == top),
            key=lambda player_id: game.player(player_id).seat,  # type: ignore[union-attr]
        )
        if len(tied) == 1:
            game.pending_exile_id = tied[0]
            self._change_phase(game, GamePhase.DAY_RESOLUTION, command_id, events)
            return
        max_round = 1 + game.config.ruleset.runoff_rounds
        if game.vote_round < max_round:
            game.vote_round += 1
            game.runoff_candidate_ids = tied
            game.votes = {}
            game.runoff_speeches = {}
            events.append(
                self._emit(
                    game,
                    EventType.RUNOFF_STARTED,
                    command_id,
                    Visibility.PUBLIC,
                    {"round": game.vote_round, "candidate_ids": tied},
                )
            )
            if game.config.ruleset.runoff_has_discussion:
                self._change_phase(
                    game, GamePhase.DAY_RUNOFF_DISCUSSION, command_id, events
                )
            return
        game.pending_exile_id = None
        self._change_phase(game, GamePhase.DAY_RESOLUTION, command_id, events)

    def _complete_day_resolution(
        self, game: Game, command_id: str, events: list[GameEvent]
    ) -> None:
        exiled = game.pending_exile_id
        if exiled is not None:
            if game.config.ruleset.exile_has_last_words and not game.last_words_submitted:
                raise GameRuleError("MISSING_LAST_WORDS", "被放逐玩家尚未提交遗言")
            self._kill(game, exiled, DeathCause.EXILE)
            events.append(
                self._emit(
                    game,
                    EventType.PLAYER_DIED,
                    command_id,
                    Visibility.PUBLIC,
                    self._death_payload(game, exiled),
                )
            )
            if self._queue_hunter_reaction(
                game,
                exiled,
                [DeathCause.EXILE],
                source_phase=GamePhase.DAY_RESOLUTION,
                resume_phase=GamePhase.NIGHT_SEER,
            ):
                self._enter_hunter_reaction(game, command_id, events)
                return
        else:
            events.append(
                self._emit(
                    game,
                    EventType.NO_EXILE,
                    command_id,
                    Visibility.PUBLIC,
                    {"reason": "第二轮投票仍然平票"},
                )
            )
        if self._finish_if_won(game, command_id, events):
            return
        game.day += 1
        self._reset_night(game)
        self._enter_night(game, command_id, events)

    # ---- 行动校验 -----------------------------------------------------

    def _validate_wolf_discussion(
        self, game: Game, action: WolfDiscussionAction
    ) -> ActionValidationResult:
        error = self._base_action_error(
            game,
            action.actor_id,
            action.proposed_target_id,
            GamePhase.NIGHT_WOLF_DISCUSSION,
        )
        if error:
            return error
        if game.role_assignments.get(action.actor_id) != Role.WOLF:
            return self._invalid("ROLE_NOT_ALLOWED", "只有狼人可以参与狼队交流")
        if action.actor_id in game.wolf_discussion:
            return self._invalid("ACTION_ALREADY_SUBMITTED", "该狼人本夜已经完成交流")
        wolves = self._living_role_ids(game, Role.WOLF)
        remaining = [player.id for player in game.alive_players() if player.id in wolves and player.id not in game.wolf_discussion]
        if not remaining or remaining[0] != action.actor_id:
            return self._invalid("OUT_OF_WOLF_DISCUSSION_ORDER", "狼人必须按照存活玩家座位号顺序交流")
        if (
            not game.config.ruleset.wolves_can_target_wolves
            and game.role_assignments.get(action.proposed_target_id) == Role.WOLF
        ):
            return self._invalid("WOLF_TARGET_NOT_ALLOWED", "当前规则不允许狼人建议选择狼人")
        return self._valid()

    def _validate_wolf(self, game: Game, action: WolfAction) -> ActionValidationResult:
        error = self._base_action_error(game, action.actor_id, action.target_id, GamePhase.NIGHT_WOLF)
        if error:
            return error
        if game.role_assignments.get(action.actor_id) != Role.WOLF:
            return self._invalid("ROLE_NOT_ALLOWED", "只有狼人可以提交击杀目标")
        if action.actor_id in game.wolf_actions:
            return self._invalid("ACTION_ALREADY_SUBMITTED", "该狼人本夜已经提交行动")
        if (
            not game.config.ruleset.wolves_can_target_wolves
            and game.role_assignments.get(action.target_id) == Role.WOLF
        ):
            return self._invalid("WOLF_TARGET_NOT_ALLOWED", "当前规则不允许狼人选择狼人")
        return self._valid()

    def _validate_seer(self, game: Game, action: SeerAction) -> ActionValidationResult:
        error = self._base_action_error(game, action.actor_id, action.target_id, GamePhase.NIGHT_SEER)
        if error:
            return error
        if game.role_assignments.get(action.actor_id) != Role.SEER:
            return self._invalid("ROLE_NOT_ALLOWED", "只有预言家可以查验")
        if action.actor_id == action.target_id:
            return self._invalid("SELF_TARGET_NOT_ALLOWED", "预言家不能查验自己")
        if game.seer_action is not None:
            return self._invalid("ACTION_ALREADY_SUBMITTED", "预言家本夜已经查验")
        return self._valid()

    def _validate_witch(self, game: Game, action: WitchAction) -> ActionValidationResult:
        phase_error = self._actor_error(game, action.actor_id, GamePhase.NIGHT_WITCH)
        if phase_error:
            return phase_error
        if game.role_assignments.get(action.actor_id) != Role.WITCH:
            return self._invalid("ROLE_NOT_ALLOWED", "只有女巫可以用药")
        if game.witch_action is not None:
            return self._invalid("ACTION_ALREADY_SUBMITTED", "女巫本夜已经提交行动")
        rules = game.config.ruleset
        state = game.role_states[action.actor_id]
        if action.rescue_target_id is not None and action.poison_target_id is not None:
            if not rules.witch_can_use_both_potions_same_night:
                return self._invalid("TWO_POTIONS_NOT_ALLOWED", "同一夜不能同时使用两瓶药")
        if action.rescue_target_id is not None:
            target = game.player(action.rescue_target_id)
            if target is None or target.status != PlayerStatus.ALIVE:
                return self._invalid("INVALID_TARGET", "解药目标不存在或已死亡")
            if not state.antidote_available:
                return self._invalid("ANTIDOTE_UNAVAILABLE", "解药已经使用")
            if action.rescue_target_id != game.wolf_target_id:
                return self._invalid("RESCUE_TARGET_NOT_ATTACKED", "只能救当晚狼人刀口")
            if action.rescue_target_id == action.actor_id:
                if game.day == 1 and not rules.witch_can_self_save_first_night:
                    return self._invalid("SELF_SAVE_NOT_ALLOWED", "首夜不允许女巫自救")
                if game.day > 1 and rules.witch_self_save_only_first_night:
                    return self._invalid("SELF_SAVE_NOT_ALLOWED", "女巫只能在首夜自救")
        if action.poison_target_id is not None:
            target = game.player(action.poison_target_id)
            if target is None or target.status != PlayerStatus.ALIVE:
                return self._invalid("INVALID_TARGET", "毒药目标不存在或已死亡")
            if not state.poison_available:
                return self._invalid("POISON_UNAVAILABLE", "毒药已经使用")
        return self._valid()

    def _validate_hunter(self, game: Game, action: HunterAction) -> ActionValidationResult:
        if game.phase != GamePhase.HUNTER_REACTION:
            return self._invalid("INVALID_PHASE", "当前不是猎人反应阶段")
        actor = game.player(action.actor_id)
        if actor is None:
            return self._invalid("ACTOR_NOT_FOUND", "行动玩家不存在")
        pending = game.pending_hunter_action
        if pending is None or pending.hunter_id != action.actor_id:
            return self._invalid("ROLE_NOT_ALLOWED", "只有待处理的猎人可以行动")
        if game.role_assignments.get(action.actor_id) != Role.HUNTER:
            return self._invalid("ROLE_NOT_ALLOWED", "只有猎人可以开枪")
        if actor.status != PlayerStatus.DEAD:
            return self._invalid("HUNTER_NOT_DEAD", "猎人仅能在死亡结算后反应")
        state = game.role_states.get(action.actor_id)
        if state is None or not state.hunter_shot_available:
            return self._invalid("HUNTER_SHOT_UNAVAILABLE", "猎人开枪机会已经消耗")
        if action.action == "pass":
            return self._valid()

        target_id = action.target_id
        assert target_id is not None
        if target_id == action.actor_id:
            return self._invalid("SELF_TARGET_NOT_ALLOWED", "猎人不能射击自己")
        target = game.player(target_id)
        if target is None:
            return self._invalid("TARGET_NOT_FOUND", "目标玩家不存在")
        if target.status != PlayerStatus.ALIVE:
            return self._invalid("TARGET_NOT_ALIVE", "猎人只能射击存活玩家")
        return self._valid()

    def _validate_speech(self, game: Game, action: SpeechAction) -> ActionValidationResult:
        if game.phase == GamePhase.DAY_RUNOFF_DISCUSSION:
            actor_error = self._actor_error(
                game, action.actor_id, GamePhase.DAY_RUNOFF_DISCUSSION
            )
            if actor_error:
                return actor_error
            if action.actor_id not in game.runoff_candidate_ids:
                return self._invalid("NOT_RUNOFF_CANDIDATE", "只有平票候选人可以进行 PK 发言")
            if action.actor_id in game.runoff_speeches:
                return self._invalid("ACTION_ALREADY_SUBMITTED", "该候选人已经完成 PK 发言")
            remaining = [
                player.id
                for player in game.alive_players()
                if player.id in game.runoff_candidate_ids
                and player.id not in game.runoff_speeches
            ]
            if not remaining or remaining[0] != action.actor_id:
                return self._invalid("OUT_OF_SPEECH_ORDER", "PK 候选人必须按照座位号顺序发言")
            return self._valid()

        actor_error = self._actor_error(game, action.actor_id, GamePhase.DAY_DISCUSSION)
        if actor_error:
            return actor_error
        if action.actor_id in game.speeches:
            return self._invalid("ACTION_ALREADY_SUBMITTED", "该玩家本日已经发言")
        if game.config.ruleset.discussion_in_seat_order:
            remaining = [p.id for p in game.alive_players() if p.id not in game.speeches]
            if not remaining or remaining[0] != action.actor_id:
                return self._invalid("OUT_OF_SPEECH_ORDER", "必须按照存活玩家座位号顺序发言")
        return self._valid()

    def _validate_vote(self, game: Game, action: VoteAction) -> ActionValidationResult:
        error = self._actor_error(game, action.actor_id, GamePhase.DAY_VOTING)
        if error:
            return error
        if (
            game.vote_round > 1
            and game.config.ruleset.runoff_has_discussion
            and action.actor_id in game.runoff_candidate_ids
        ):
            return self._invalid("RUNOFF_CANDIDATE_CANNOT_VOTE", "PK 候选人不能参与重投")
        if action.actor_id in game.votes:
            return self._invalid("ACTION_ALREADY_SUBMITTED", "该玩家本轮已经投票")
        if action.abstain:
            return self._valid()
        assert action.target_id is not None
        error = self._base_action_error(game, action.actor_id, action.target_id, GamePhase.DAY_VOTING)
        if error:
            return error
        if action.actor_id == action.target_id:
            return self._invalid("SELF_TARGET_NOT_ALLOWED", "不能投票给自己")
        if game.vote_round > 1 and action.target_id not in game.runoff_candidate_ids:
            return self._invalid("NOT_RUNOFF_CANDIDATE", "重新投票只能选择平票候选人")
        return self._valid()

    def _validate_last_words(
        self, game: Game, action: LastWordsAction
    ) -> ActionValidationResult:
        if game.phase != GamePhase.DAY_RESOLUTION:
            return self._invalid("INVALID_PHASE", "当前不是白天结算阶段")
        if not game.config.ruleset.exile_has_last_words:
            return self._invalid("LAST_WORDS_NOT_ALLOWED", "当前规则没有遗言")
        if game.pending_exile_id != action.actor_id:
            return self._invalid("ROLE_NOT_ALLOWED", "只有待放逐玩家可以发表遗言")
        if game.last_words_submitted:
            return self._invalid("ACTION_ALREADY_SUBMITTED", "遗言已经提交")
        return self._valid()

    # ---- 通用辅助 -----------------------------------------------------

    def _run_action(
        self,
        game: Game,
        action: NightAction | HunterAction | SpeechAction | VoteAction | LastWordsAction,
        command_id: str,
        operation: str,
        apply: Callable[[Game, list[GameEvent]], None],
    ) -> EngineResult:
        return self._run(
            game,
            command_id,
            operation,
            action.model_dump(mode="json"),
            apply,
        )

    def _run(
        self,
        game: Game,
        command_id: str,
        operation: str,
        payload: dict[str, Any],
        apply: Callable[[Game, list[GameEvent]], None],
    ) -> EngineResult:
        if not command_id.strip():
            raise GameRuleError("INVALID_COMMAND_ID", "command_id 不能为空")
        fingerprint = self._fingerprint(operation, payload)
        receipt = game.processed_commands.get(command_id)
        if receipt is not None:
            if receipt.operation != operation or receipt.fingerprint != fingerprint:
                raise GameRuleError("COMMAND_ID_CONFLICT", "command_id 已用于其他请求")
            return EngineResult(
                game=game.model_copy(deep=True),
                events=[event.model_copy(deep=True) for event in receipt.events],
                idempotent_replay=True,
            )
        next_game = game.model_copy(deep=True)
        phase_before = next_game.phase
        new_events: list[GameEvent] = []
        apply(next_game, new_events)
        next_game.processed_commands[command_id] = CommandReceipt(
            operation=operation,
            fingerprint=fingerprint,
            phase_before=phase_before,
            phase_after=next_game.phase,
            events=[event.model_copy(deep=True) for event in new_events],
        )
        return EngineResult(game=next_game, events=new_events)

    @staticmethod
    def _fingerprint(operation: str, payload: dict[str, Any]) -> str:
        raw = json.dumps(
            {"operation": operation, "payload": payload},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _emit(
        self,
        game: Game,
        event_type: EventType,
        command_id: str,
        visibility: Visibility,
        payload: dict[str, Any],
        audience_player_ids: list[str] | None = None,
    ) -> GameEvent:
        sequence = len(game.events) + 1
        event = GameEvent(
            sequence=sequence,
            event_id=f"{game.id}:{sequence}",
            type=event_type,
            day=game.day,
            phase=game.phase,
            visibility=visibility,
            audience_player_ids=audience_player_ids or [],
            payload=payload,
            command_id=command_id,
        )
        game.events.append(event)
        return event

    def _change_phase(
        self,
        game: Game,
        phase: GamePhase,
        command_id: str,
        events: list[GameEvent],
    ) -> None:
        previous = game.phase
        game.phase = phase
        events.append(
            self._emit(
                game,
                EventType.PHASE_CHANGED,
                command_id,
                Visibility.PUBLIC,
                {"from": previous.value, "to": phase.value},
            )
        )

    def _finish_if_won(
        self, game: Game, command_id: str, events: list[GameEvent]
    ) -> bool:
        result = self.check_win(game)
        if result is None:
            return False
        game.winner = result
        self._change_phase(game, GamePhase.FINISHED, command_id, events)
        events.append(
            self._emit(
                game,
                EventType.GAME_FINISHED,
                command_id,
                Visibility.POST_GAME_PUBLIC,
                result.model_dump(mode="json"),
            )
        )
        return True

    def _reset_night(self, game: Game) -> None:
        game.wolf_discussion = {}
        game.wolf_actions = {}
        game.wolf_target_id = None
        game.seer_action = None
        game.witch_action = None
        game.runoff_speeches = {}
        game.night_death_ids = []
        game.speeches = {}
        game.votes = {}
        game.vote_round = 1
        game.pending_hunter_action = None
        game.runoff_candidate_ids = []
        game.pending_exile_id = None
        game.last_words_submitted = False

    def _enter_wolf_night(
        self, game: Game, command_id: str, events: list[GameEvent]
    ) -> None:
        phase = (
            GamePhase.NIGHT_WOLF_DISCUSSION
            if len(self._living_role_ids(game, Role.WOLF)) > 1
            else GamePhase.NIGHT_WOLF
        )
        self._change_phase(game, phase, command_id, events)

    def _enter_night(
        self, game: Game, command_id: str, events: list[GameEvent]
    ) -> None:
        self._change_phase(game, GamePhase.NIGHT_SEER, command_id, events)

    def _eligible_voter_ids(self, game: Game) -> list[str]:
        voter_ids = [player.id for player in game.alive_players()]
        if game.vote_round > 1 and game.config.ruleset.runoff_has_discussion:
            candidates = set(game.runoff_candidate_ids)
            return [player_id for player_id in voter_ids if player_id not in candidates]
        return voter_ids

    @staticmethod
    def _death_payload(game: Game, player_id: str) -> dict[str, Any]:
        player = game.player(player_id)
        assert player is not None
        causes = list(player.death_causes)
        if not causes and player.death_cause is not None:
            causes = [player.death_cause]
        return {
            "player_id": player_id,
            "cause": player.death_cause.value if player.death_cause is not None else None,
            "causes": [cause.value for cause in causes],
        }

    def _queue_hunter_reaction(
        self,
        game: Game,
        player_id: str,
        causes: list[DeathCause],
        *,
        source_phase: GamePhase,
        resume_phase: GamePhase,
    ) -> bool:
        if game.pending_hunter_action is not None:
            return False
        if game.role_assignments.get(player_id) != Role.HUNTER:
            return False
        if DeathCause.WITCH_POISON in causes:
            return False
        if not any(
            cause in {DeathCause.WOLF_ATTACK, DeathCause.EXILE} for cause in causes
        ):
            return False
        state = game.role_states.get(player_id)
        if state is None or not state.hunter_shot_available:
            return False
        game.pending_hunter_action = PendingHunterAction(
            hunter_id=player_id,
            trigger_causes=list(dict.fromkeys(causes)),
            source_phase=source_phase,
            resume_phase=resume_phase,
        )
        return True

    def _enter_hunter_reaction(
        self, game: Game, command_id: str, events: list[GameEvent]
    ) -> None:
        pending = game.pending_hunter_action
        if pending is None:
            raise GameRuleError("MISSING_HUNTER_REACTION", "当前没有待处理的猎人行动")
        self._change_phase(game, GamePhase.HUNTER_REACTION, command_id, events)
        events.append(
            self._emit(
                game,
                EventType.HUNTER_ACTION_REQUESTED,
                command_id,
                Visibility.PUBLIC,
                {
                    "hunter_id": pending.hunter_id,
                    "trigger_causes": [cause.value for cause in pending.trigger_causes],
                    "legal_target_ids": [
                        player.id
                        for player in game.alive_players()
                        if player.id != pending.hunter_id
                    ],
                },
            )
        )

    def _resume_after_hunter(
        self,
        game: Game,
        pending: PendingHunterAction,
        command_id: str,
        events: list[GameEvent],
    ) -> None:
        if pending.resume_phase == GamePhase.DAY_DISCUSSION:
            game.speeches = {}
            game.runoff_speeches = {}
            self._change_phase(game, GamePhase.DAY_DISCUSSION, command_id, events)
            return
        if pending.resume_phase == GamePhase.NIGHT_SEER:
            game.day += 1
            self._reset_night(game)
            self._enter_night(game, command_id, events)
            return
        self._change_phase(game, pending.resume_phase, command_id, events)

    def _kill(
        self, game: Game, player_id: str, cause: DeathCause | list[DeathCause]
    ) -> None:
        player = game.player(player_id)
        if player is None or player.status != PlayerStatus.ALIVE:
            raise GameRuleError("INVALID_DEATH", "不能结算不存在或已死亡的玩家")
        causes = [cause] if isinstance(cause, DeathCause) else list(cause)
        causes = list(dict.fromkeys(causes))
        if not causes:
            raise GameRuleError("INVALID_DEATH", "死亡结算必须包含至少一个死因")
        player.status = PlayerStatus.DEAD
        player.death_causes = causes
        player.death_cause = causes[-1]
        player.death_day = game.day

    def _base_action_error(
        self, game: Game, actor_id: str, target_id: str, phase: GamePhase
    ) -> ActionValidationResult | None:
        actor_error = self._actor_error(game, actor_id, phase)
        if actor_error:
            return actor_error
        target = game.player(target_id)
        if target is None:
            return self._invalid("TARGET_NOT_FOUND", "目标玩家不存在")
        if target.status != PlayerStatus.ALIVE:
            return self._invalid("TARGET_NOT_ALIVE", "目标玩家已死亡")
        return None

    def _actor_error(
        self, game: Game, actor_id: str, phase: GamePhase
    ) -> ActionValidationResult | None:
        if game.phase != phase:
            return self._invalid("INVALID_PHASE", f"行动只允许在 {phase.value} 阶段提交")
        actor = game.player(actor_id)
        if actor is None:
            return self._invalid("ACTOR_NOT_FOUND", "行动玩家不存在")
        if actor.status != PlayerStatus.ALIVE:
            return self._invalid("ACTOR_NOT_ALIVE", "死亡玩家不能行动")
        return None

    @staticmethod
    def _valid() -> ActionValidationResult:
        return ActionValidationResult(valid=True)

    @staticmethod
    def _invalid(code: str, message: str) -> ActionValidationResult:
        return ActionValidationResult(valid=False, code=code, message=message)

    @staticmethod
    def _raise_if_invalid(result: ActionValidationResult) -> None:
        if not result.valid:
            raise GameRuleError(result.code or "INVALID_ACTION", result.message or "非法行动")

    @staticmethod
    def _require_phase(game: Game, phase: GamePhase) -> None:
        if game.phase != phase:
            raise GameRuleError("INVALID_PHASE", f"当前阶段不是 {phase.value}")

    @staticmethod
    def _living_role_ids(game: Game, role: Role) -> list[str]:
        return [
            player.id
            for player in game.alive_players()
            if game.role_assignments.get(player.id) == role
        ]
