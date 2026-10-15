from __future__ import annotations

import re
import unicodedata
from collections.abc import Collection, Mapping, Sequence
from difflib import SequenceMatcher
from typing import Any

from .schemas import SpeechOutput, StructuredOutputError


_SENTENCE_SPLIT = re.compile(r"[。！？!?；;\n]+")
_VOTE_EXPRESSION = re.compile(
    r"归票|投(?:给|向|票)|票(?:给|向)|放逐|建议出|今天出|挂票|抬走|下掉|(?:我|这轮|本轮)(?:会)?出"
)
_ABSTAIN_EXPRESSION = re.compile(r"弃票|不投票|选择不投|放弃投票")
_CONDITION_EXPRESSION = re.compile(r"如果|若|除非|一旦|只要|否则|能否|是否|当.+时")
def _context_value(context: object | None, name: str, default: Any = None) -> Any:
    if context is None:
        return default
    if isinstance(context, Mapping):
        return context.get(name, default)
    return getattr(context, name, default)


def _normalize(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    return re.sub(r"[\W_]+", "", normalized, flags=re.UNICODE)


def _last_sentence(text: str) -> str:
    parts = [part.strip() for part in _SENTENCE_SPLIT.split(text) if part.strip()]
    return _normalize(parts[-1]) if parts else ""


def _aliases(
    player_id: str,
    player_aliases: Mapping[str, Sequence[str]],
) -> tuple[str, ...]:
    raw = player_aliases.get(player_id, ())
    if isinstance(raw, str):
        raw = (raw,)
    return tuple(
        dict.fromkeys((player_id, *(str(item) for item in raw if str(item).strip())))
    )


def _alias_is_mentioned(text: str, alias: str) -> bool:
    folded_text = unicodedata.normalize("NFKC", text).casefold()
    folded_alias = unicodedata.normalize("NFKC", alias).casefold().strip()
    if not folded_alias:
        return False
    escaped = re.escape(folded_alias)
    if re.fullmatch(r"[a-z0-9][a-z0-9_-]*", folded_alias):
        return re.search(rf"(?<![a-z0-9]){escaped}(?![a-z0-9])", folded_text) is not None
    if re.fullmatch(r"\d+号(?:玩家)?", folded_alias):
        return re.search(rf"(?<!\d){escaped}(?!\d)", folded_text) is not None
    if re.fullmatch(r"玩家\d+", folded_alias):
        return re.search(rf"{escaped}(?!\d)", folded_text) is not None
    normalized_alias = _normalize(folded_alias)
    return bool(normalized_alias) and normalized_alias in _normalize(folded_text)


def _mentions_player(
    text: str,
    player_id: str,
    player_aliases: Mapping[str, Sequence[str]],
) -> bool:
    return any(
        _alias_is_mentioned(text, alias)
        for alias in _aliases(player_id, player_aliases)
    )


def _similarity_issues(current: str, previous_speeches: Sequence[str]) -> list[str]:
    current_normalized = _normalize(current)
    if not current_normalized:
        return []

    best_full: tuple[float, int] | None = None
    best_tail: tuple[float, int] | None = None
    current_tail = _last_sentence(current)
    recent_speeches = tuple(previous_speeches)[-8:]
    for index, previous in enumerate(recent_speeches, start=1):
        previous_normalized = _normalize(str(previous))
        shorter = min(len(current_normalized), len(previous_normalized))
        if shorter >= 20 and current_normalized == previous_normalized:
            best_full = (1.0, index)
        elif shorter >= 24:
            threshold = 0.88 if shorter >= 60 else 0.92
            ratio = SequenceMatcher(
                None,
                current_normalized,
                previous_normalized,
                autojunk=False,
            ).ratio()
            if ratio >= threshold and (best_full is None or ratio > best_full[0]):
                best_full = (ratio, index)

        previous_tail = _last_sentence(str(previous))
        if min(len(current_tail), len(previous_tail)) >= 14:
            tail_ratio = SequenceMatcher(
                None,
                current_tail,
                previous_tail,
                autojunk=False,
            ).ratio()
            if tail_ratio >= 0.92 and (best_tail is None or tail_ratio > best_tail[0]):
                best_tail = (tail_ratio, index)

    issues: list[str] = []
    if best_full is not None:
        issues.append(
            "SPEECH_TOO_SIMILAR: "
            f"与本轮已有发言整体相似度 {best_full[0]:.2f}；"
            "必须重写 speech，保留公开事实但加入自己的具体比较和判断"
        )
    if best_tail is not None:
        issues.append(
            "SPEECH_REPEATED_ENDING: "
            f"结尾与本轮已有发言相似度 {best_tail[0]:.2f}；"
            "不得继续使用相同的观察、等待或找矛盾套话"
        )
    return issues


def validate_speech_quality(
    output: SpeechOutput,
    *,
    discussion_context: object | None,
    previous_speeches: Sequence[str],
    legal_target_ids: Collection[str],
    player_aliases: Mapping[str, Sequence[str]],
) -> None:
    """校验发言的当前位置职责；失败时交给 Gateway 定向修复。"""

    issues: list[str] = []
    legal_targets = {str(item) for item in legal_target_ids}
    known_players = {str(item) for item in player_aliases}
    cited_ids = [str(item) for item in output.cited_player_ids]
    focus_ids = [str(item) for item in output.focus_player_ids]
    spoken_ids = {
        str(item)
        for item in (
            _context_value(discussion_context, "spoken_speaker_ids", ()) or ()
        )
    }

    unknown_citations = [item for item in cited_ids if item not in known_players]
    if unknown_citations:
        issues.append(
            f"SPEECH_UNKNOWN_CITATION: cited_player_ids 含未知玩家 {unknown_citations}"
        )
    citations_without_prior_speech = [item for item in cited_ids if item not in spoken_ids]
    if discussion_context is not None and citations_without_prior_speech:
        issues.append(
            "SPEECH_INVALID_CITATION: 只能引用本轮已经发言的玩家，非法编号 "
            f"{citations_without_prior_speech}"
        )
    illegal_focus = [item for item in focus_ids if item not in legal_targets]
    if illegal_focus:
        issues.append(
            f"SPEECH_ILLEGAL_FOCUS: focus_player_ids 含非法目标 {illegal_focus}"
        )

    for player_id in cited_ids:
        if player_id in known_players and not _mentions_player(
            output.speech,
            player_id,
            player_aliases,
        ):
            issues.append(
                "SPEECH_CITATION_NOT_PUBLIC: speech 正文未明确提到 "
                f"cited_player_ids 中的 {player_id}"
            )
    for player_id in focus_ids:
        if player_id in known_players and not _mentions_player(
            output.speech,
            player_id,
            player_aliases,
        ):
            issues.append(
                "SPEECH_FOCUS_NOT_PUBLIC: speech 正文未明确提到 "
                f"focus_player_ids 中的 {player_id}"
            )

    for field_name, target_id in (
        ("vote_target_id", output.vote_target_id),
        ("fallback_target_id", output.fallback_target_id),
    ):
        if target_id is not None and target_id not in legal_targets:
            issues.append(
                f"SPEECH_ILLEGAL_TARGET: {field_name}={target_id} 不在合法目标中"
            )

    reference_ids = list(dict.fromkeys([*cited_ids, *focus_ids]))
    for index, conflict in enumerate(output.key_conflicts, start=1):
        if len(_normalize(conflict)) < 6:
            issues.append(
                f"SPEECH_LOW_INFORMATION_CONFLICT: key_conflicts 第 {index} 项缺少有效信息"
            )
        elif reference_ids and not any(
            _mentions_player(conflict, player_id, player_aliases)
            for player_id in reference_ids
            if player_id in known_players
        ):
            issues.append(
                "SPEECH_CONFLICT_WITHOUT_PLAYER: key_conflicts 必须关联 cited_player_ids "
                "或 focus_player_ids 中的具体玩家"
            )

    if output.decision_condition is not None:
        if len(_normalize(output.decision_condition)) < 8:
            issues.append(
                "SPEECH_LOW_INFORMATION_CONDITION: decision_condition 缺少有效信息"
            )
        if _CONDITION_EXPRESSION.search(output.decision_condition) is None:
            issues.append(
                "SPEECH_DECISION_NOT_CONDITIONAL: decision_condition 必须使用如果、若、"
                "除非、否则等可验证条件表达"
            )
        condition_reference_ids = list(
            dict.fromkeys(
                [
                    *focus_ids,
                    *(
                        [output.vote_target_id]
                        if output.vote_target_id is not None
                        else []
                    ),
                    *(
                        [output.fallback_target_id]
                        if output.fallback_target_id is not None
                        else []
                    ),
                ]
            )
        )
        if condition_reference_ids and not any(
            _mentions_player(output.decision_condition, player_id, player_aliases)
            for player_id in condition_reference_ids
            if player_id in known_players
        ):
            issues.append(
                "SPEECH_DECISION_TARGET_REQUIRED: decision_condition 必须明确关联重点或票向玩家"
            )

    if discussion_context is not None:
        required_citations = min(1, len(spoken_ids))
        if len(cited_ids) < required_citations:
            issues.append(
                "SPEECH_CITATION_REQUIRED: 已有前位发言时必须引用至少一名玩家"
            )
        if legal_targets and not focus_ids:
            issues.append(
                "SPEECH_FOCUS_REQUIRED: 必须给出当前关注或待验证的具体玩家"
            )
        if output.vote_intent == "undecided" and (
            output.decision_condition is None
            or len(_normalize(output.decision_condition)) < 6
        ):
            issues.append(
                "SPEECH_DECISION_CONDITION_REQUIRED: 尚未定票时必须说明形成判断的具体条件"
            )

    if output.vote_intent == "vote" and output.vote_target_id is not None:
        if not _mentions_player(
            output.speech,
            output.vote_target_id,
            player_aliases,
        ):
            issues.append(
                "SPEECH_VOTE_TARGET_NOT_PUBLIC: speech 正文必须明确说出首选归票目标"
            )
        if _VOTE_EXPRESSION.search(output.speech) is None:
            issues.append(
                "SPEECH_VOTE_INTENT_NOT_PUBLIC: speech 正文必须使用投票、归票或放逐措辞公开票向"
            )
    elif output.vote_intent == "abstain":
        if _ABSTAIN_EXPRESSION.search(output.speech) is None:
            issues.append(
                "SPEECH_ABSTAIN_NOT_PUBLIC: 选择 abstain 时必须在 speech 正文明确说出弃票"
            )

    if output.fallback_target_id is not None and not _mentions_player(
        output.speech,
        output.fallback_target_id,
        player_aliases,
    ):
        issues.append(
            "SPEECH_FALLBACK_NOT_PUBLIC: 填写 fallback_target_id 时必须在 speech 正文说明备选目标"
        )

    issues.extend(_similarity_issues(output.speech, previous_speeches))
    if issues:
        raise StructuredOutputError(
            "发言质量校验失败：" + "；".join(dict.fromkeys(issues))
        )
