from __future__ import annotations

from collections import Counter

import pytest

from app.domain import (
    Alignment,
    EventType,
    GamePhase,
    GameRuleError,
    LastWordsAction,
    PlayerStatus,
    Role,
    Ruleset,
    SeerAction,
    SpeechAction,
    Visibility,
    VoteAction,
    Winner,
    WitchAction,
    WolfAction,
    WolfDiscussionAction,
)
from app.engine import GameEngine


NAMES = ["一号", "二号", "三号", "四号", "五号", "六号"]


def new_game(seed: int = 17):
    engine = GameEngine()
    result = engine.create_game(NAMES, game_id=f"game-{seed}")
    game = result.game
    game.config.random_seed = seed
    game = engine.start_game(game, "start").game
    return engine, game


def enter_first_night(seed: int = 17):
    engine, game = new_game(seed)
    game = engine.complete_phase(game, "enter-night").game
    assert game.phase == GamePhase.NIGHT_SEER
    game = complete_seer_phase(engine, game, "opening")
    assert game.phase == GamePhase.NIGHT_WOLF_DISCUSSION
    wolves = [
        player.id
        for player in game.alive_players()
        if game.role_assignments[player.id] == Role.WOLF
    ]
    target = next(player.id for player in game.alive_players() if player.id not in wolves)
    for index, wolf in enumerate(wolves):
        game = engine.submit_wolf_discussion(
            game,
            WolfDiscussionAction(
                actor_id=wolf,
                reason="讨论刀口",
                content="建议统一目标",
                proposed_target_id=target,
            ),
            f"wolf-discussion-{index}",
        ).game
    game = engine.complete_phase(game, "wolf-discussion-done").game
    assert game.phase == GamePhase.NIGHT_WOLF
    return engine, game


def role_id(game, role: Role) -> str:
    return next(player_id for player_id, assigned in game.role_assignments.items() if assigned == role)


def role_ids(game, role: Role) -> list[str]:
    return sorted(player_id for player_id, assigned in game.role_assignments.items() if assigned == role)


def complete_seer_phase(engine: GameEngine, game, prefix: str = "seer"):
    seer = role_id(game, Role.SEER)
    target = next(player.id for player in game.alive_players() if player.id != seer)
    game = engine.submit_seer_action(
        game,
        SeerAction(actor_id=seer, target_id=target, reason="查验"),
        f"{prefix}-action",
    ).game
    return engine.complete_phase(game, f"{prefix}-done").game


def pass_a_safe_night(engine: GameEngine, game, prefix: str = "n"):
    wolves = role_ids(game, Role.WOLF)
    witch = role_id(game, Role.WITCH)
    victim = next(player.id for player in game.alive_players() if player.id not in wolves)
    for index, wolf in enumerate(wolves):
        game = engine.submit_wolf_action(
            game,
            WolfAction(actor_id=wolf, target_id=victim, reason="统一刀口"),
            f"{prefix}-wolf-{index}",
        ).game
    game = engine.complete_phase(game, f"{prefix}-wolf-done").game
    assert game.phase == GamePhase.NIGHT_WITCH
    game = engine.submit_witch_action(
        game,
        WitchAction(actor_id=witch, rescue_target_id=victim, reason="救人"),
        f"{prefix}-witch",
    ).game
    game = engine.complete_phase(game, f"{prefix}-witch-done").game
    game = engine.complete_phase(game, f"{prefix}-resolve").game
    assert game.phase == GamePhase.DAY_ANNOUNCEMENT
    assert all(player.status == PlayerStatus.ALIVE for player in game.players)
    return game


def enter_voting(engine: GameEngine, game, prefix: str = "d"):
    game = engine.complete_phase(game, f"{prefix}-announce").game
    for player in game.alive_players():
        game = engine.submit_speech(
            game,
            SpeechAction(
                actor_id=player.id,
                content=f"{player.name} 发言",
                reason="公开分析",
                private_summary="不会进入公开事件",
            ),
            f"{prefix}-speech-{player.id}",
        ).game
    game = engine.complete_phase(game, f"{prefix}-discussion-done").game
    assert game.phase == GamePhase.DAY_VOTING
    return game


def submit_votes(engine: GameEngine, game, targets: dict[str, str], prefix: str):
    for voter_id, target_id in targets.items():
        game = engine.submit_vote(
            game,
            VoteAction(actor_id=voter_id, target_id=target_id, reason="公开票型理由"),
            f"{prefix}-{voter_id}",
        ).game
    return game


def exile_player(engine: GameEngine, game, exiled: str, prefix: str):
    alive_ids = [player.id for player in game.alive_players()]
    fallback = next(player_id for player_id in alive_ids if player_id != exiled)
    targets = {
        voter_id: (fallback if voter_id == exiled else exiled)
        for voter_id in alive_ids
    }
    game = submit_votes(engine, game, targets, f"{prefix}-vote")
    game = engine.complete_phase(game, f"{prefix}-vote-done").game
    game = engine.submit_last_words(
        game,
        LastWordsAction(actor_id=exiled, content="遗言", reason="被放逐后的最后陈述"),
        f"{prefix}-last-words",
    ).game
    return engine.complete_phase(game, f"{prefix}-resolve").game


def test_ruleset_mvp_v1_is_frozen_and_complete():
    rules = Ruleset.mvp_v1()
    assert rules.version == "mvp-v1"
    assert rules.player_count == 6
    assert rules.role_counts == {
        Role.WOLF: 2,
        Role.SEER: 1,
        Role.WITCH: 1,
        Role.VILLAGER: 2,
    }
    assert rules.witch_can_self_save_first_night
    assert not rules.witch_can_use_both_potions_same_night
    assert rules.runoff_rounds == 1


def test_same_seed_deals_same_roles_and_different_seed_can_differ():
    _, first = new_game(12345)
    _, second = new_game(12345)
    _, third = new_game(54321)
    assert first.role_assignments == second.role_assignments
    assert first.role_assignments != third.role_assignments
    assert Counter(first.role_assignments.values()) == Counter(Ruleset.mvp_v1().role_counts)


def test_start_and_commands_are_pure_and_idempotent():
    engine = GameEngine()
    lobby = engine.create_game(NAMES, game_id="pure-game").game
    started = engine.start_game(lobby, "same-command")
    assert lobby.phase == GamePhase.LOBBY
    assert lobby.role_assignments == {}
    replay = engine.start_game(started.game, "same-command")
    assert replay.idempotent_replay is True
    assert [event.event_id for event in replay.events] == [
        event.event_id for event in started.events
    ]
    assert len(replay.game.events) == len(started.game.events)
    with pytest.raises(GameRuleError, match="COMMAND_ID_CONFLICT"):
        engine.complete_phase(started.game, "same-command")

    advanced = engine.complete_phase(started.game, "advance-once")
    advance_replay = engine.complete_phase(advanced.game, "advance-once")
    assert advance_replay.idempotent_replay is True
    assert advance_replay.game.phase == GamePhase.NIGHT_SEER
    assert len(advance_replay.game.events) == len(advanced.game.events)


def test_wolf_discussion_is_ordered_private_and_stops_before_kill():
    engine, game = new_game(8)
    game = engine.complete_phase(game, "enter-wolf-discussion").game
    game = complete_seer_phase(engine, game, "seer-before-wolves")
    wolves = [
        player.id
        for player in game.alive_players()
        if game.role_assignments[player.id] == Role.WOLF
    ]
    target = next(player.id for player in game.alive_players() if player.id not in wolves)

    with pytest.raises(GameRuleError, match="MISSING_WOLF_DISCUSSION"):
        engine.complete_phase(game, "discussion-too-soon")
    with pytest.raises(GameRuleError, match="OUT_OF_WOLF_DISCUSSION_ORDER"):
        engine.submit_wolf_discussion(
            game,
            WolfDiscussionAction(
                actor_id=wolves[1],
                reason="抢先发言",
                content="我先说",
                proposed_target_id=target,
            ),
            "out-of-order",
        )

    for index, wolf in enumerate(wolves):
        result = engine.submit_wolf_discussion(
            game,
            WolfDiscussionAction(
                actor_id=wolf,
                reason="团队讨论",
                content=f"狼人 {index + 1} 建议该目标",
                proposed_target_id=target,
            ),
            f"discussion-{index}",
        )
        game = result.game
        assert result.events[0].type == EventType.WOLF_DISCUSSION_MESSAGE
        assert result.events[0].visibility == Visibility.WOLF_TEAM_PRIVATE
        assert set(result.events[0].audience_player_ids) == set(wolves)

    game = engine.complete_phase(game, "discussion-complete").game
    assert game.phase == GamePhase.NIGHT_WOLF
    assert game.wolf_actions == {}


def test_wolf_tie_uses_fixed_seed_and_wolf_self_target_is_allowed():
    engine, game = enter_first_night(9)
    wolves = role_ids(game, Role.WOLF)
    targets = [wolves[0], next(player.id for player in game.alive_players() if player.id not in wolves)]
    for index, (wolf, target) in enumerate(zip(wolves, targets)):
        game = engine.submit_wolf_action(
            game,
            WolfAction(actor_id=wolf, target_id=target, reason="分歧刀口"),
            f"wolf-{index}",
        ).game
    resolved = engine.complete_phase(game, "wolf-resolve")
    assert resolved.game.wolf_target_id in targets
    assert resolved.game.phase == GamePhase.NIGHT_WITCH
    witch = role_id(resolved.game, Role.WITCH)
    witch_event = next(
        event
        for event in resolved.events
        if event.type == EventType.WOLF_TARGET_SELECTED
        and event.audience_player_ids == [witch]
    )
    assert witch_event.payload == {"target_id": resolved.game.wolf_target_id}

    engine2, game2 = enter_first_night(9)
    for index, (wolf, target) in enumerate(zip(wolves, targets)):
        game2 = engine2.submit_wolf_action(
            game2,
            WolfAction(actor_id=wolf, target_id=target, reason="分歧刀口"),
            f"wolf-{index}",
        ).game
    assert engine2.complete_phase(game2, "wolf-resolve").game.wolf_target_id == resolved.game.wolf_target_id


def test_seer_result_is_alignment_and_private_event():
    engine, game = new_game()
    game = engine.complete_phase(game, "enter-seer").game
    assert game.phase == GamePhase.NIGHT_SEER
    wolves = role_ids(game, Role.WOLF)
    seer = role_id(game, Role.SEER)
    result = engine.submit_seer_action(
        game,
        SeerAction(actor_id=seer, target_id=wolves[0], reason="查狼"),
        "seer-check",
    )
    assert result.game.role_states[seer].seer_results[wolves[0]] == Alignment.WOLF
    event = next(event for event in result.events if event.type == EventType.SEER_INSPECTION)
    assert event.audience_player_ids == [seer]
    assert event.payload["alignment"] == "WOLF"


def test_witch_can_self_save_first_night_but_cannot_use_both_potions():
    engine, game = enter_first_night(31)
    witch = role_id(game, Role.WITCH)
    wolves = role_ids(game, Role.WOLF)
    for index, wolf in enumerate(wolves):
        game = engine.submit_wolf_action(
            game,
            WolfAction(actor_id=wolf, target_id=witch, reason="刀女巫"),
            f"w{index}",
        ).game
    game = engine.complete_phase(game, "wolf-done").game
    assert game.phase == GamePhase.NIGHT_WITCH
    both = WitchAction(
        actor_id=witch,
        rescue_target_id=witch,
        poison_target_id=wolves[0],
        reason="同时用药",
    )
    validation = engine.validate_action(game, both)
    assert not validation.valid and validation.code == "TWO_POTIONS_NOT_ALLOWED"

    game = engine.submit_witch_action(
        game,
        WitchAction(actor_id=witch, rescue_target_id=witch, reason="首夜自救"),
        "witch-save",
    ).game
    assert not game.role_states[witch].antidote_available
    game = engine.complete_phase(game, "witch-done").game
    game = engine.complete_phase(game, "night-resolution").game
    assert game.player(witch).status == PlayerStatus.ALIVE


def test_night_resolution_applies_wolf_kill_and_poison_simultaneously():
    engine, game = enter_first_night(44)
    wolves = role_ids(game, Role.WOLF)
    good = [player.id for player in game.alive_players() if player.id not in wolves]
    victim, poison_target = good[0], wolves[0]
    for index, wolf in enumerate(wolves):
        game = engine.submit_wolf_action(
            game, WolfAction(actor_id=wolf, target_id=victim, reason="刀人"), f"w{index}"
        ).game
    game = engine.complete_phase(game, "wd").game
    assert game.phase == GamePhase.NIGHT_WITCH
    witch = role_id(game, Role.WITCH)
    game = engine.submit_witch_action(
        game,
        WitchAction(actor_id=witch, poison_target_id=poison_target, reason="毒狼"),
        "poison",
    ).game
    game = engine.complete_phase(game, "pd").game
    result = engine.complete_phase(game, "resolve")
    assert result.game.player(victim).status == PlayerStatus.DEAD
    assert result.game.player(poison_target).status == PlayerStatus.DEAD
    assert set(result.game.night_death_ids) == {victim, poison_target}


def test_discussion_requires_seat_order_and_public_event_hides_private_summary():
    engine, game = enter_first_night()
    game = pass_a_safe_night(engine, game)
    game = engine.complete_phase(game, "announce").game
    alive = game.alive_players()
    invalid = engine.validate_action(
        game,
        SpeechAction(actor_id=alive[1].id, content="抢先发言", reason="测试"),
    )
    assert invalid.code == "OUT_OF_SPEECH_ORDER"
    result = engine.submit_speech(
        game,
        SpeechAction(
            actor_id=alive[0].id,
            content="正常发言",
            reason="公开理由",
            private_summary="秘密策略",
        ),
        "speech",
    )
    assert result.events[0].payload == {
        "actor_id": alive[0].id,
        "content": "正常发言",
        "reason": "公开理由",
        "cited_player_ids": [],
        "focus_player_ids": [],
        "key_conflicts": [],
        "vote_intent": "undecided",
        "vote_target_id": None,
        "fallback_target_id": None,
        "decision_condition": None,
        "round_summary": [],
    }
    assert "private_summary" not in result.events[0].payload


def test_first_vote_tie_runs_off_and_second_tie_exiles_nobody():
    engine, game = enter_first_night(52)
    game = enter_voting(engine, pass_a_safe_night(engine, game))
    ids = [player.id for player in game.alive_players()]
    first = {
        ids[0]: ids[1],
        ids[1]: ids[0],
        ids[2]: ids[0],
        ids[3]: ids[0],
        ids[4]: ids[1],
        ids[5]: ids[1],
    }
    game = submit_votes(engine, game, first, "v1")
    result = engine.complete_phase(game, "votes-1")
    game = result.game
    assert game.phase == GamePhase.DAY_VOTING
    assert game.vote_round == 2
    assert set(game.runoff_candidate_ids) == {ids[0], ids[1]}
    assert any(event.type == EventType.RUNOFF_STARTED for event in result.events)

    second = first
    game = submit_votes(engine, game, second, "v2")
    game = engine.complete_phase(game, "votes-2").game
    assert game.phase == GamePhase.DAY_RESOLUTION
    assert game.pending_exile_id is None
    result = engine.complete_phase(game, "day-resolve")
    assert result.game.phase == GamePhase.NIGHT_SEER
    assert result.game.day == 2
    assert any(event.type == EventType.NO_EXILE for event in result.events)


def test_all_players_abstain_and_voting_advances_directly_to_next_night():
    engine, game = enter_first_night(53)
    game = enter_voting(engine, pass_a_safe_night(engine, game))
    ids = [player.id for player in game.alive_players()]

    for voter_id in ids:
        game = engine.submit_vote(
            game,
            VoteAction(
                actor_id=voter_id,
                target_id=None,
                abstain=True,
                reason="当前没有足够信息，选择弃票",
            ),
            f"abstain-{voter_id}",
        ).game

    result = engine.complete_phase(game, "all-abstained")

    assert result.game.phase == GamePhase.NIGHT_SEER
    assert result.game.day == 2
    assert result.game.pending_exile_id is None
    revealed = next(event for event in result.events if event.type == EventType.VOTES_REVEALED)
    assert revealed.payload["counts"] == {}
    assert revealed.payload["abstain_count"] == len(ids)
    assert all(vote["abstain"] for vote in revealed.payload["votes"])
    no_exile = next(event for event in result.events if event.type == EventType.NO_EXILE)
    assert no_exile.payload == {"reason": "所有存活玩家均弃票"}
    assert all(event.payload.get("to") != GamePhase.DAY_RESOLUTION.value for event in result.events)


def test_abstentions_are_excluded_from_the_vote_tally():
    engine, game = enter_first_night(54)
    game = enter_voting(engine, pass_a_safe_night(engine, game))
    ids = [player.id for player in game.alive_players()]
    target_id = ids[0]

    for voter_id in ids:
        abstain = voter_id != ids[1]
        game = engine.submit_vote(
            game,
            VoteAction(
                actor_id=voter_id,
                target_id=None if abstain else target_id,
                abstain=abstain,
                reason="弃票" if abstain else "投给一号",
            ),
            f"mixed-vote-{voter_id}",
        ).game

    result = engine.complete_phase(game, "mixed-votes")

    assert result.game.phase == GamePhase.DAY_RESOLUTION
    assert result.game.pending_exile_id == target_id
    revealed = next(event for event in result.events if event.type == EventType.VOTES_REVEALED)
    assert revealed.payload["counts"] == {target_id: 1}
    assert revealed.payload["abstain_count"] == len(ids) - 1


def test_vote_action_requires_a_target_unless_abstaining():
    with pytest.raises(ValueError, match="非弃票时必须指定目标"):
        VoteAction(actor_id="p1", target_id=None, reason="缺少目标")
    with pytest.raises(ValueError, match="弃票时不能指定目标"):
        VoteAction(actor_id="p1", target_id="p2", abstain=True, reason="冲突票型")


def test_unique_exile_requires_one_round_of_last_words():
    engine, game = enter_first_night(63)
    game = enter_voting(engine, pass_a_safe_night(engine, game))
    ids = [player.id for player in game.alive_players()]
    exiled = ids[0]
    targets = {voter: exiled for voter in ids if voter != exiled}
    targets[exiled] = ids[1]
    game = submit_votes(engine, game, targets, "vote")
    game = engine.complete_phase(game, "vote-done").game
    assert game.pending_exile_id == exiled
    with pytest.raises(GameRuleError, match="MISSING_LAST_WORDS"):
        engine.complete_phase(game, "resolve-too-soon")
    assert game.player(exiled).status == PlayerStatus.ALIVE
    game = engine.submit_last_words(
        game,
        LastWordsAction(actor_id=exiled, content="这是遗言", reason="离场陈述"),
        "last-words",
    ).game
    result = engine.complete_phase(game, "resolve-exile")
    assert result.game.player(exiled).status == PlayerStatus.DEAD


def test_win_conditions_prioritize_all_wolves_dead_then_parity():
    engine, game = new_game(77)
    wolves = role_ids(game, Role.WOLF)
    for wolf in wolves:
        game.player(wolf).status = PlayerStatus.DEAD
    win = engine.check_win(game)
    assert win is not None and win.winner == Winner.GOOD

    _, parity = new_game(77)
    good_ids = [
        player.id
        for player in parity.players
        if parity.role_assignments[player.id] != Role.WOLF
    ]
    for player_id in good_ids[:2]:
        parity.player(player_id).status = PlayerStatus.DEAD
    win = engine.check_win(parity)
    assert win is not None and win.winner == Winner.WOLVES


def test_events_have_stable_contiguous_sequence_and_json_round_trip():
    engine, game = enter_first_night(88)
    dumped = game.model_dump_json()
    restored = type(game).model_validate_json(dumped)
    assert [event.sequence for event in restored.events] == list(
        range(1, len(restored.events) + 1)
    )
    assert restored.role_assignments == game.role_assignments


def test_fixed_actions_can_run_a_complete_game_to_good_victory():
    engine, game = enter_first_night(101)
    wolves = role_ids(game, Role.WOLF)

    game = pass_a_safe_night(engine, game, "night-1")
    game = enter_voting(engine, game, "day-1")
    game = exile_player(engine, game, wolves[0], "day-1")
    assert game.phase == GamePhase.NIGHT_SEER

    game = complete_seer_phase(engine, game, "night-2-seer")
    assert game.phase == GamePhase.NIGHT_WOLF

    living_wolf = next(
        player_id for player_id in wolves if game.player(player_id).status == PlayerStatus.ALIVE
    )
    victim = next(
        player.id
        for player in game.alive_players()
        if game.role_assignments[player.id] == Role.VILLAGER
    )
    game = engine.submit_wolf_action(
        game,
        WolfAction(actor_id=living_wolf, target_id=victim, reason="夜间击杀"),
        "night-2-wolf",
    ).game
    game = engine.complete_phase(game, "night-2-wolf-done").game
    assert game.phase == GamePhase.NIGHT_WITCH
    witch = role_id(game, Role.WITCH)
    game = engine.submit_witch_action(
        game,
        WitchAction(actor_id=witch, reason="解药已用，选择空过"),
        "night-2-witch",
    ).game
    game = engine.complete_phase(game, "night-2-witch-done").game
    game = engine.complete_phase(game, "night-2-resolution").game
    assert game.player(victim).status == PlayerStatus.DEAD
    assert game.phase == GamePhase.DAY_ANNOUNCEMENT

    game = enter_voting(engine, game, "day-2")
    game = exile_player(engine, game, living_wolf, "day-2")
    assert game.phase == GamePhase.FINISHED
    assert game.winner is not None and game.winner.winner == Winner.GOOD
    assert any(event.type == EventType.GAME_FINISHED for event in game.events)
