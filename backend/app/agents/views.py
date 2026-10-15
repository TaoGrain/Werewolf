from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class PlayerSummary(BaseModel):
    id: str
    seat: int
    name: str
    alive: bool


DiscussionResponsibility = Literal[
    "INDEPENDENT_ANALYSIS",
    "LATE_CONSOLIDATION",
    "FINAL_SUMMARY",
]


class DiscussionContext(BaseModel):
    """当前公开发言轮次中的相对位置，只包含公开可推导信息。"""

    speaker_ids: list[str]
    spoken_speaker_ids: list[str]
    remaining_speaker_ids: list[str]
    speaker_index: int = Field(ge=1)
    speaker_count: int = Field(ge=1)
    remaining_after_self: int = Field(ge=0)
    is_late_speaker: bool
    is_last_speaker: bool
    responsibility: DiscussionResponsibility


class PlayerView(BaseModel):
    session_id: str
    game_id: str
    player_id: str
    day: int
    phase: str
    rules: dict[str, Any]
    players: list[PlayerSummary]
    public_events: list[dict[str, Any]] = Field(default_factory=list)
    speech_timeline: list[dict[str, Any]] = Field(default_factory=list)
    role_card: dict[str, Any]
    personality: dict[str, Any] = Field(default_factory=dict)
    private_information: dict[str, Any] = Field(default_factory=dict)
    private_memory: dict[str, Any] = Field(default_factory=dict)
    allowed_action: str | None = None
    legal_targets: list[str] = Field(default_factory=list)
    discussion_context: DiscussionContext | None = None


class PlayerViewBuilder:
    """只从明确分区的状态快照构造视图，绝不向调用方返回完整发牌表。"""

    def build(
        self,
        game_state: dict[str, Any],
        player_id: str,
        *,
        public_events: list[dict[str, Any]] | None = None,
        private_memory: dict[str, Any] | None = None,
        allowed_action: str | None = None,
        legal_targets: list[str] | None = None,
        discussion_context: DiscussionContext | None = None,
    ) -> PlayerView:
        players = game_state.get("players") or []
        current = next((p for p in players if str(p.get("id")) == str(player_id)), None)
        if current is None:
            raise KeyError(f"玩家不存在：{player_id}")
        assignments = game_state.get("role_assignments") or game_state.get("assignments") or {}
        own_assignment = self._assignment(assignments, player_id)
        role = str(own_assignment.get("role") or current.get("role") or "UNKNOWN").upper()
        private: dict[str, Any] = {}
        role_states = game_state.get("role_states") or {}
        own_state = self._assignment(role_states, player_id)
        if role in {"WEREWOLF", "WOLF"}:
            private["wolf_teammates"] = [
                str(pid)
                for pid, assignment in self._assignment_items(assignments)
                if str(pid) != str(player_id)
                and str(assignment.get("role", "")).upper() in {"WEREWOLF", "WOLF"}
            ]
            private["wolf_team_history"] = [
                dict(message) for message in (game_state.get("wolf_team_history") or [])
            ]
        elif role == "SEER":
            checks = own_assignment.get("check_results") or own_state.get("seer_results") or []
            private["check_results"] = checks
        elif role == "WITCH":
            private["antidote_available"] = bool(own_assignment.get("antidote_available", own_state.get("antidote_available", True)))
            private["poison_available"] = bool(own_assignment.get("poison_available", own_state.get("poison_available", True)))
            if game_state.get("witch_visible_victim") is not None:
                private["night_victim_id"] = str(game_state["witch_visible_victim"])
        elif role == "HUNTER":
            private["hunter_shot_available"] = bool(
                own_assignment.get("hunter_shot_available", own_state.get("hunter_shot_available", True))
            )
            pending = self._assignment_value(game_state.get("pending_hunter_action"))
            if str(pending.get("hunter_id") or "") == str(player_id):
                private["pending_hunter_action"] = {
                    "trigger_causes": [str(cause) for cause in pending.get("trigger_causes") or []],
                    "source_phase": str(pending.get("source_phase") or ""),
                    "resume_phase": str(pending.get("resume_phase") or ""),
                }


        rules = game_state.get("ruleset") or game_state.get("rules") or {}
        if hasattr(rules, "model_dump"):
            rules = rules.model_dump(mode="json")
        player_summaries = [
            PlayerSummary(
                id=str(player["id"]),
                seat=int(player.get("seat", player.get("seat_number", 0))),
                name=str(player.get("name", f"玩家{player.get('seat', '')}")),
                alive=bool(
                    player.get(
                        "alive",
                        str(player.get("status", "ALIVE")).upper() == "ALIVE",
                    )
                ),
            )
            for player in players
        ]
        visible_public_events = [
            self._public_event(event) for event in (public_events or [])
        ]
        return PlayerView(
            session_id=f"game:{game_state['id']}:player:{player_id}",
            game_id=str(game_state["id"]),
            player_id=str(player_id),
            day=int(game_state.get("day", 0)),
            phase=str(game_state.get("phase", "LOBBY")),
            rules=dict(rules),
            players=player_summaries,
            public_events=visible_public_events,
            speech_timeline=self._speech_timeline(
                visible_public_events,
                player_summaries,
            ),
            role_card={"role": role},
            personality=dict(current.get("personality") or {}),
            private_information=private,
            private_memory=private_memory or {},
            allowed_action=allowed_action,
            legal_targets=[str(item) for item in (legal_targets or [])],
            discussion_context=discussion_context,
        )


    @staticmethod
    def _speech_timeline(
        public_events: list[dict[str, Any]],
        players: list[PlayerSummary],
    ) -> list[dict[str, Any]]:
        """从公开事件确定性派生发言时间线，不依赖模型总结。"""

        players_by_id = {player.id: player for player in players}
        timeline: list[dict[str, Any]] = []
        seen: set[tuple[int, str]] = set()

        def append_item(
            *,
            event_sequence: Any,
            player_id: Any,
            day: Any,
            phase: Any,
        ) -> None:
            if not isinstance(event_sequence, int):
                return
            normalized_player_id = str(player_id or "")
            player = players_by_id.get(normalized_player_id)
            if player is None:
                return
            identity = (event_sequence, normalized_player_id)
            if identity in seen:
                return
            seen.add(identity)
            timeline.append(
                {
                    "event_sequence": event_sequence,
                    "day": int(day or 0),
                    "phase": str(phase or ""),
                    "player_id": normalized_player_id,
                    "seat": player.seat,
                    "name": player.name,
                }
            )

        for event in public_events:
            event_type = str(event.get("type") or event.get("event_type") or "").upper()
            if event_type == "SPEECH":
                append_item(
                    event_sequence=event.get("sequence"),
                    player_id=event.get("player_id"),
                    day=event.get("day"),
                    phase=event.get("phase"),
                )
                continue
            if event_type != "DISCUSSION_ROUND_SUMMARY":
                continue
            for item in event.get("summary_items") or []:
                if not isinstance(item, dict):
                    continue
                append_item(
                    event_sequence=item.get("event_sequence"),
                    player_id=item.get("player_id"),
                    day=event.get("day"),
                    phase=event.get("phase"),
                )

        timeline.sort(key=lambda item: item["event_sequence"])
        round_positions: dict[tuple[int, str], int] = {}
        for item in timeline:
            round_key = (item["day"], item["phase"])
            position = round_positions.get(round_key, 0) + 1
            round_positions[round_key] = position
            item["round_position"] = position
        return timeline

    @staticmethod
    def _assignment(assignments: Any, player_id: str) -> dict[str, Any]:
        if isinstance(assignments, dict):
            value = assignments.get(player_id) or assignments.get(str(player_id)) or {}
            return PlayerViewBuilder._assignment_value(value)
        for value in assignments:
            data = PlayerViewBuilder._assignment_value(value)
            if str(data.get("player_id")) == str(player_id):
                return data
        return {}

    @staticmethod
    def _assignment_items(assignments: Any) -> list[tuple[str, dict[str, Any]]]:
        if isinstance(assignments, dict):
            return [
                (str(key), PlayerViewBuilder._assignment_value(value))
                for key, value in assignments.items()
            ]
        result = []
        for value in assignments:
            data = PlayerViewBuilder._assignment_value(value)
            result.append((str(data.get("player_id")), data))
        return result

    @staticmethod
    def _assignment_value(value: Any) -> dict[str, Any]:
        if hasattr(value, "model_dump"):
            return value.model_dump(mode="json")
        if isinstance(value, dict):
            return dict(value)
        if isinstance(value, str):
            return {"role": value}
        return {"role": str(value)} if value else {}

    @staticmethod
    def _public_event(event: dict[str, Any]) -> dict[str, Any]:
        """白名单复制公共字段，避免 visibility/payload 内夹带私密数据。"""
        public_keys = {
            "sequence",
            "type",
            "event_type",
            "day",
            "phase",
            "player_id",
            "text",
            "content",
            "reason",
            "target_id",
            "hunter_id",
            "candidate_ids",
            "round",
            "votes",
            "counts",
            "abstain",
            "abstain_count",
            "cited_player_ids",
            "focus_player_ids",
            "key_conflicts",
            "vote_intent",
            "vote_target_id",
            "fallback_target_id",
            "decision_condition",
            "round_summary",
            "summary_items",
            "epistemic_status",
            "created_at",
        }
        return {key: value for key, value in event.items() if key in public_keys}
