"""经典 9 人猎人规则的领域契约测试。"""

import pytest

from app.domain import (
    DeathCause,
    EventType,
    Game,
    GamePhase,
    HunterAction,
    PendingHunterAction,
    Player,
    Role,
    Ruleset,
    WinReason,
    WinResult,
    Winner,
)


def test_classic_9_hunter_ruleset_is_exact_and_versioned():
    rules = Ruleset.classic_9_hunter_v1()

    assert rules.version == "classic-9-hunter-v1"
    assert rules.player_count == 9
    assert rules.role_counts == {
        Role.WOLF: 3,
        Role.SEER: 1,
        Role.WITCH: 1,
        Role.HUNTER: 1,
        Role.VILLAGER: 3,
    }
    assert rules.win_condition == "slaughter_side"
    assert rules.wolves_win_at_parity is True
    assert rules.runoff_has_discussion is True


def test_ruleset_rejects_unknown_version_and_modified_registered_preset():
    with pytest.raises(ValueError, match="未知规则版本"):
        Ruleset.for_version("unknown-v1")

    with pytest.raises(ValueError, match="固定阵容"):
        Ruleset(
            version="classic-9-hunter-v1",
            role_counts={
                Role.WOLF: 3,
                Role.SEER: 1,
                Role.WITCH: 1,
                Role.VILLAGER: 4,
            },
        )

    with pytest.raises(ValueError, match="胜负或平票规则"):
        Ruleset(version="classic-9-hunter-v1", win_condition="parity")

    # 规则升级前保存的经典 9 人快照会在加载时迁移到双重胜利条件。
    legacy_classic = Ruleset(
        version="classic-9-hunter-v1",
        wolves_win_at_parity=False,
    )
    assert legacy_classic.wolves_win_at_parity is True


def test_mvp_v1_defaults_remain_compatible():
    rules = Ruleset.mvp_v1()

    assert rules == Ruleset()
    assert rules.version == "mvp-v1"
    assert rules.player_count == 6
    assert rules.role_counts == {
        Role.WOLF: 2,
        Role.SEER: 1,
        Role.WITCH: 1,
        Role.VILLAGER: 2,
    }
    assert rules.win_condition == "parity"
    assert rules.wolves_win_at_parity is True
    assert rules.runoff_has_discussion is False


def test_hunter_action_requires_target_only_when_shooting():
    shoot = HunterAction(
        actor_id="hunter",
        reason="带走最后一狼",
        action="shoot",
        target_id="wolf",
    )
    passed = HunterAction(actor_id="hunter", reason="放弃开枪", action="pass")

    assert shoot.target_id == "wolf"
    assert passed.target_id is None
    with pytest.raises(ValueError, match="开枪时必须指定目标"):
        HunterAction(actor_id="hunter", reason="开枪", action="shoot")
    with pytest.raises(ValueError, match="放弃开枪时不能指定目标"):
        HunterAction(
            actor_id="hunter",
            reason="放弃",
            action="pass",
            target_id="wolf",
        )


def test_player_preserves_complete_death_causes_and_legacy_primary_cause():
    player = Player(
        id="hunter",
        seat=1,
        name="猎人",
        death_causes=[DeathCause.WOLF_ATTACK, DeathCause.WITCH_POISON],
    )

    assert player.death_causes == [
        DeathCause.WOLF_ATTACK,
        DeathCause.WITCH_POISON,
    ]
    assert player.death_cause == DeathCause.WITCH_POISON

    legacy = Player.model_validate(
        {
            "id": "old-player",
            "seat": 2,
            "name": "旧玩家",
            "death_cause": "EXILE",
        }
    )
    assert legacy.death_cause == DeathCause.EXILE
    assert legacy.death_causes == [DeathCause.EXILE]


def test_pending_hunter_action_and_old_game_snapshot_round_trip():
    old_snapshot = {
        "id": "old-game",
        "config": {"ruleset": {"version": "mvp-v1"}},
        "players": [
            {
                "id": "p1",
                "seat": 1,
                "name": "一号",
                "death_cause": "EXILE",
            }
        ],
        "role_states": {"p1": {}},
    }
    game = Game.model_validate(old_snapshot)

    assert game.pending_hunter_action is None
    assert game.runoff_speeches == {}
    assert game.players[0].death_causes == [DeathCause.EXILE]
    assert game.role_states["p1"].hunter_shot_available is True

    game.pending_hunter_action = PendingHunterAction(
        hunter_id="p1",
        trigger_causes=[DeathCause.EXILE],
        source_phase=GamePhase.DAY_RESOLUTION,
        resume_phase=GamePhase.NIGHT_SEER,
    )
    restored = Game.model_validate(game.model_dump(mode="json"))
    assert restored.pending_hunter_action == game.pending_hunter_action


def test_hunter_enums_and_win_result_keep_legacy_fields_compatible():
    assert Role.HUNTER.value == "HUNTER"
    assert DeathCause.HUNTER_SHOT.value == "HUNTER_SHOT"
    assert GamePhase.HUNTER_REACTION.value == "HUNTER_REACTION"
    assert GamePhase.DAY_RUNOFF_DISCUSSION.value == "DAY_RUNOFF_DISCUSSION"
    assert EventType.HUNTER_ACTION_REQUESTED.value == "HUNTER_ACTION_REQUESTED"

    legacy = WinResult(
        winner=Winner.GOOD,
        reason="狼人全部出局",
        alive_wolves=0,
        alive_good=3,
    )
    assert legacy.reason_code is None
    assert legacy.alive_gods is None
    assert legacy.alive_villagers is None

    result = WinResult(
        winner=Winner.WOLVES,
        reason="神职全部出局",
        reason_code=WinReason.ALL_GODS_ELIMINATED,
        alive_wolves=2,
        alive_good=3,
        alive_gods=0,
        alive_villagers=3,
    )
    assert result.reason_code == WinReason.ALL_GODS_ELIMINATED
