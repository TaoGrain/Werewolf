from __future__ import annotations

import json
from typing import Any, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


class StructuredOutputError(ValueError):
    pass


class SpeechOutput(BaseModel):
    """白天讨论专用输出；结构化判断字段必须由模型显式填写。"""

    model_config = ConfigDict(extra="forbid")

    speech: str = Field(description="要在游戏中公开发表的中文发言", min_length=1, max_length=2000)
    public_reason: str = Field(description="可以向所有玩家公开的中文理由", min_length=1, max_length=1000)
    private_strategy: str = Field(
        description=(
            "仅自己可见的中文主观策略摘要；不是系统事实记录。引用其他玩家的身份、"
            "查验、用药或阵营说法时必须写明来源，并使用‘自称/声称/认为’等未验证措辞"
        ),
        default="",
        max_length=1000,
    )
    cited_player_ids: list[str] = Field(
        description="正文中明确引用的玩家编号；可以引用当前或此前轮次的任意玩家",
        max_length=9,
    )
    focus_player_ids: list[str] = Field(
        description="当前重点关注、怀疑或建议归票涉及的玩家编号",
        max_length=3,
    )
    key_conflicts: list[str] = Field(
        description="从公开信息中提炼的关键分歧或矛盾；玩家说法必须注明是谁声称的；没有时填写空列表",
        default_factory=list,
        max_length=5,
    )
    vote_intent: Literal["vote", "abstain", "undecided"] = Field(
        description="当前公开归票建议：投向目标、建议弃票或尚未决定",
    )
    vote_target_id: str | None = Field(
        description="当前公开首选归票目标；vote 时必须填写合法玩家编号",
    )
    fallback_target_id: str | None = Field(
        description="公开备选归票目标；没有备选时必须填写 null",
    )
    decision_condition: str | None = Field(
        description="维持或改变当前判断、票向的具体公开条件；没有时填写 null",
        min_length=1,
        max_length=500,
    )
    @model_validator(mode="after")
    def speech_plan_is_consistent(self) -> "SpeechOutput":
        for field_name in ("cited_player_ids", "focus_player_ids"):
            values = getattr(self, field_name)
            if len(values) != len(set(values)):
                raise ValueError(f"{field_name} 不能包含重复玩家编号")
        if any(not item.strip() for item in self.key_conflicts):
            raise ValueError("key_conflicts 不能包含空白项")
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


class LastWordsOutput(BaseModel):
    """遗言无需伪造后续票向或讨论位置。"""

    model_config = ConfigDict(extra="forbid")

    speech: str = Field(description="要公开发表的中文遗言", min_length=1, max_length=2000)
    public_reason: str = Field(description="遗言的公开摘要", min_length=1, max_length=1000)
    private_strategy: str = Field(
        description=(
            "仅自己可见的遗言策略摘要；不得在此字段提前决定、承诺或暗示尚未进入的角色反应，"
            "例如猎人稍后的开枪或放弃"
        ),
        default="",
        max_length=1000,
    )


class VoteOutput(BaseModel):
    action: Literal["vote", "abstain"] = Field(
        description="投票选择：vote 为投给一名玩家，abstain 为弃票",
        default="vote",
    )
    target_id: str | None = Field(description="投票目标编号；弃票时为空", default=None)
    public_reason: str = Field(description="可以向所有玩家公开的中文投票理由", min_length=1, max_length=1000)
    private_reason: str = Field(
        description=(
            "仅自己可见的阵营策略评估：说明是否根据后置证据更新判断，并比较本票的当下收益、"
            "长期收益与身份暴露风险；不得把私有身份或私密行动写入 public_reason"
        ),
        default="",
        max_length=1000,
    )
    confidence: float | None = Field(description="信心值，范围为 0 到 1", default=None, ge=0, le=1)

    @model_validator(mode="after")
    def target_matches_action(self) -> "VoteOutput":
        if self.action == "abstain" and self.target_id is not None:
            raise ValueError("弃票时 target_id 必须为空")
        if self.action == "vote" and self.target_id is None:
            raise ValueError("投票时必须指定 target_id")
        return self


class TargetOutput(BaseModel):
    target_id: str = Field(description="必须原样选自合法目标编号")
    private_reason: str = Field(description="仅自己可见的中文行动理由", default="", max_length=1000)


class WolfDiscussionOutput(BaseModel):
    message: str = Field(description="发送给狼队友的中文讨论内容", min_length=1, max_length=1000)
    proposed_target_id: str = Field(description="本轮讨论建议的击杀目标，必须原样选自合法目标编号")


class WitchOutput(BaseModel):
    action: Literal["save", "poison", "pass"] = Field(description="行动：save 为救人，poison 为毒人，pass 为跳过")
    target_id: str | None = Field(description="用药目标编号；跳过时为空", default=None)
    private_reason: str = Field(description="仅自己可见的中文行动理由", default="", max_length=1000)


class HunterOutput(BaseModel):
    action: Literal["shoot", "pass"] = Field(description="行动：shoot 为开枪，pass 为放弃")
    target_id: str | None = Field(description="开枪目标编号；放弃时为空", default=None)
    public_reason: str = Field(description="可以向所有玩家公开的中文决定理由", min_length=1, max_length=1000)

    @model_validator(mode="after")
    def target_matches_action(self) -> "HunterOutput":
        if self.action == "shoot" and self.target_id is None:
            raise ValueError("猎人开枪时必须指定 target_id")
        if self.action == "pass" and self.target_id is not None:
            raise ValueError("猎人放弃开枪时 target_id 必须为空")
        return self


class AIReviewDimensionScores(BaseModel):
    """赛后评价的分项分数，统一使用 10 分制。"""

    model_config = ConfigDict(extra="forbid")

    role_performance: int = Field(description="身份职责与阵营目标完成度", ge=0, le=10)
    reasoning: int = Field(description="逻辑、信息利用与判断质量", ge=0, le=10)
    communication: int = Field(description="发言清晰度、说服力与协作质量", ge=0, le=10)
    adaptability: int = Field(description="根据新信息修正策略的能力", ge=0, le=10)


class AgentEvaluation(BaseModel):
    """单个 Agent 的结构化赛后评价。"""

    model_config = ConfigDict(extra="forbid")

    player_id: str = Field(description="必须原样使用事实包中的玩家编号")
    overall_score: int = Field(description="综合评分，满分 100", ge=0, le=100)
    dimensions: AIReviewDimensionScores
    summary: str = Field(description="简洁、具体且基于本局事实的中文总评", min_length=1, max_length=600)
    strengths: list[str] = Field(description="本局做得好的具体事项", min_length=1, max_length=3)
    improvements: list[str] = Field(description="下一局可执行的改进建议", min_length=1, max_length=3)
    decisive_moment: str | None = Field(
        description="最能体现该 Agent 表现的关键时刻；没有明确时填写 null",
        default=None,
        max_length=500,
    )


class AIReviewOutput(BaseModel):
    """整局 AI 复盘输出；必须覆盖所有参局 Agent。"""

    model_config = ConfigDict(extra="forbid")

    overall_summary: str = Field(description="整局表现与胜负关键的中文总结", min_length=1, max_length=1200)
    evaluations: list[AgentEvaluation] = Field(min_length=1, max_length=9)


T = TypeVar("T", bound=BaseModel)


def extract_json_object(text: str) -> dict[str, Any]:
    """提取最外层 JSON 对象，兼容模型额外文本与 Markdown 围栏。"""
    stripped = text.strip()
    try:
        value = json.loads(stripped)
        if isinstance(value, dict):
            return value
    except json.JSONDecodeError:
        pass

    start = stripped.find("{")
    if start < 0:
        raise StructuredOutputError("模型输出中没有 JSON 对象")
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(stripped)):
        char = stripped[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                try:
                    value = json.loads(stripped[start : index + 1])
                except json.JSONDecodeError as exc:
                    raise StructuredOutputError(f"JSON 解析失败：{exc.msg}") from exc
                if not isinstance(value, dict):
                    raise StructuredOutputError("结构化输出必须是对象")
                return value
    raise StructuredOutputError("JSON 对象不完整")


def parse_structured(text: str, model: type[T]) -> T:
    try:
        return model.model_validate(extract_json_object(text))
    except ValidationError as exc:
        raise StructuredOutputError(str(exc)) from exc


_INTERNAL_REASONING_KEYS = {"reasoning_content", "thinking", "chain_of_thought"}


def split_model_text(text: str) -> tuple[str | None, str]:
    """将模型思考与最终结构化回答分离，避免思考内容干扰 JSON 校验。"""
    import re

    reasoning_parts = [
        match.group(1).strip()
        for match in re.finditer(
            r"<(?:think|analysis)>([\s\S]*?)</(?:think|analysis)>",
            text,
            flags=re.IGNORECASE,
        )
        if match.group(1).strip()
    ]
    final_text = re.sub(
        r"<(?:think|analysis)>[\s\S]*?</(?:think|analysis)>",
        "",
        text,
        flags=re.IGNORECASE,
    ).strip()

    if not any(key in final_text.lower() for key in _INTERNAL_REASONING_KEYS):
        return ("\n\n".join(reasoning_parts) or None, final_text)
    try:
        payload = extract_json_object(final_text)
    except StructuredOutputError:
        return ("\n\n".join(reasoning_parts) or None, final_text)

    def clean(value: Any) -> Any:
        if isinstance(value, dict):
            cleaned: dict[str, Any] = {}
            for key, item in value.items():
                if str(key).lower() in _INTERNAL_REASONING_KEYS:
                    if isinstance(item, str) and item.strip():
                        reasoning_parts.append(item.strip())
                    continue
                cleaned[key] = clean(item)
            return cleaned
        if isinstance(value, list):
            return [clean(item) for item in value]
        return value

    cleaned_payload = clean(payload)
    return ("\n\n".join(reasoning_parts) or None, json.dumps(cleaned_payload, ensure_ascii=False))


def sanitize_model_text(text: str) -> str:
    """兼容旧调用方：只返回已与思考过程分离的最终回答。"""
    return split_model_text(text)[1]
