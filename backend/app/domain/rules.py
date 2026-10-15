"""版本化规则配置。所有会影响结算的 MVP 默认值都集中在此。"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .enums import Role


class Ruleset(BaseModel):
    model_config = ConfigDict(frozen=True, use_enum_values=False)

    version: str = "mvp-v1"
    player_count: int = 6
    role_counts: dict[Role, int] = Field(
        default_factory=lambda: {
            Role.WOLF: 2,
            Role.SEER: 1,
            Role.WITCH: 1,
            Role.VILLAGER: 2,
        }
    )
    witch_can_self_save_first_night: bool = True
    witch_self_save_only_first_night: bool = True
    witch_can_use_both_potions_same_night: bool = False
    wolves_can_target_wolves: bool = True
    wolf_tie_break_with_seed: bool = True
    discussion_in_seat_order: bool = True
    runoff_rounds: int = 1
    runoff_has_discussion: bool = False
    exile_has_last_words: bool = True
    night_death_has_last_words: bool = False
    win_condition: Literal["parity", "slaughter_side"] = "parity"
    wolves_win_at_parity: bool = True
    default_god_view: bool = False

    @model_validator(mode="before")
    @classmethod
    def apply_version_defaults(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data

        values = dict(data)
        version = values.get("version", "mvp-v1")
        if version not in {"mvp-v1", "classic-9-hunter-v1"}:
            raise ValueError(f"未知规则版本：{version}")
        if version == "classic-9-hunter-v1":
            values.setdefault("player_count", 9)
            values.setdefault(
                "role_counts",
                {
                    Role.WOLF: 3,
                    Role.SEER: 1,
                    Role.WITCH: 1,
                    Role.HUNTER: 1,
                    Role.VILLAGER: 3,
                },
            )
            values.setdefault("runoff_has_discussion", True)
            values.setdefault("win_condition", "slaughter_side")
            # 经典 9 人规则已升级为“屠边 + 人数对等”。旧快照中该字段
            # 可能显式保存为 false，加载时统一迁移为当前固定规则。
            values["wolves_win_at_parity"] = True
        return values

    @model_validator(mode="after")
    def validate_registered_preset(self) -> "Ruleset":
        expected_role_counts = {
            "mvp-v1": {
                Role.WOLF: 2,
                Role.SEER: 1,
                Role.WITCH: 1,
                Role.VILLAGER: 2,
            },
            "classic-9-hunter-v1": {
                Role.WOLF: 3,
                Role.SEER: 1,
                Role.WITCH: 1,
                Role.HUNTER: 1,
                Role.VILLAGER: 3,
            },
        }
        expected_player_count = {"mvp-v1": 6, "classic-9-hunter-v1": 9}
        expected_win_condition = {
            "mvp-v1": "parity",
            "classic-9-hunter-v1": "slaughter_side",
        }
        expected_runoff_discussion = {
            "mvp-v1": False,
            "classic-9-hunter-v1": True,
        }
        expected_parity = {"mvp-v1": True, "classic-9-hunter-v1": True}
        if (
            self.player_count != expected_player_count[self.version]
            or self.role_counts != expected_role_counts[self.version]
        ):
            raise ValueError(f"{self.version} 的玩家人数或固定阵容不正确")
        if (
            self.win_condition != expected_win_condition[self.version]
            or self.runoff_has_discussion != expected_runoff_discussion[self.version]
            or self.wolves_win_at_parity != expected_parity[self.version]
        ):
            raise ValueError(f"{self.version} 的胜负或平票规则不正确")
        if sum(self.role_counts.values()) != self.player_count:
            raise ValueError("角色数量总和必须等于玩家数量")
        if any(count < 0 for count in self.role_counts.values()):
            raise ValueError("角色数量不能为负数")
        if self.runoff_rounds < 0:
            raise ValueError("重新投票轮数不能为负数")
        return self

    @classmethod
    def mvp_v1(cls) -> "Ruleset":
        return cls()

    @classmethod
    def classic_9_hunter_v1(cls) -> "Ruleset":
        return cls(version="classic-9-hunter-v1")

    @classmethod
    def for_version(cls, version: str) -> "Ruleset":
        return cls(version=version)
