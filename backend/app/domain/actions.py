"""Agent 只能提交这些行动意图，不能直接修改游戏状态。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Action(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    actor_id: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class WolfAction(Action):
    action_type: Literal["wolf_kill"] = "wolf_kill"
    target_id: str = Field(min_length=1)


class WolfDiscussionAction(Action):
    action_type: Literal["wolf_discussion"] = "wolf_discussion"
    round: Literal[1] = 1
    content: str = Field(min_length=1, max_length=1000)
    proposed_target_id: str = Field(min_length=1)


class SeerAction(Action):
    action_type: Literal["seer_inspect"] = "seer_inspect"
    target_id: str = Field(min_length=1)


class WitchAction(Action):
    action_type: Literal["witch_act"] = "witch_act"
    rescue_target_id: str | None = None
    poison_target_id: str | None = None

    @model_validator(mode="after")
    def targets_must_differ(self) -> "WitchAction":
        if (
            self.rescue_target_id is not None
            and self.rescue_target_id == self.poison_target_id
        ):
            raise ValueError("同一目标不能同时被救和被毒")
        return self


class HunterAction(Action):
    action_type: Literal["hunter_act"] = "hunter_act"
    action: Literal["shoot", "pass"]
    target_id: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def target_matches_action(self) -> "HunterAction":
        if self.action == "shoot" and self.target_id is None:
            raise ValueError("猎人开枪时必须指定目标")
        if self.action == "pass" and self.target_id is not None:
            raise ValueError("猎人放弃开枪时不能指定目标")
        return self


class SpeechAction(Action):
    action_type: Literal["speech"] = "speech"
    content: str = Field(min_length=1)
    private_summary: str | None = None
    cited_player_ids: list[str] = Field(default_factory=list)
    focus_player_ids: list[str] = Field(default_factory=list)
    key_conflicts: list[str] = Field(default_factory=list)
    vote_intent: Literal["vote", "abstain", "undecided"] = "undecided"
    vote_target_id: str | None = Field(default=None, min_length=1)
    fallback_target_id: str | None = Field(default=None, min_length=1)
    decision_condition: str | None = None
    round_summary: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def structured_fields_are_consistent(self) -> "SpeechAction":
        for field_name in ("cited_player_ids", "focus_player_ids"):
            values = getattr(self, field_name)
            if len(values) != len(set(values)):
                raise ValueError(f"{field_name} 不能包含重复玩家编号")
        if any(not item.strip() for item in self.key_conflicts):
            raise ValueError("key_conflicts 不能包含空白项")
        if any(not item.strip() for item in self.round_summary):
            raise ValueError("round_summary 不能包含空白项")
        if self.vote_intent == "vote":
            if self.vote_target_id is None:
                raise ValueError("vote_intent 为 vote 时必须填写 vote_target_id")
        elif self.vote_target_id is not None:
            raise ValueError("vote_intent 不是 vote 时 vote_target_id 必须为空")
        if self.vote_intent != "vote" and self.fallback_target_id is not None:
            raise ValueError("只有 vote_intent 为 vote 时才可填写 fallback_target_id")
        if self.fallback_target_id is not None and self.fallback_target_id == self.vote_target_id:
            raise ValueError("fallback_target_id 不能与 vote_target_id 相同")
        focus_ids = set(self.focus_player_ids)
        for field_name, target_id in (
            ("vote_target_id", self.vote_target_id),
            ("fallback_target_id", self.fallback_target_id),
        ):
            if target_id is not None and target_id not in focus_ids:
                raise ValueError(f"{field_name} 必须包含在 focus_player_ids 中")
        return self


class LastWordsAction(Action):
    action_type: Literal["last_words"] = "last_words"
    content: str = Field(min_length=1)


class VoteAction(Action):
    action_type: Literal["vote"] = "vote"
    target_id: str | None = Field(default=None, min_length=1)
    abstain: bool = False
    confidence: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def target_matches_vote_choice(self) -> "VoteAction":
        if self.abstain and self.target_id is not None:
            raise ValueError("弃票时不能指定目标")
        if not self.abstain and self.target_id is None:
            raise ValueError("非弃票时必须指定目标")
        return self


NightAction = WolfDiscussionAction | WolfAction | SeerAction | WitchAction
