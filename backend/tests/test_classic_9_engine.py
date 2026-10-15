from __future__ import annotations

from collections import Counter

import pytest

from app.domain import (
    Alignment,
    DeathCause,
    EventType,
    GameConfig,
    GamePhase,
    HunterAction,
    LastWordsAction,
    PlayerStatus,
    Role,
    Ruleset,
    SeerAction,
    SpeechAction,
    VoteAction,
    Winner,
    WinReason,
    WitchAction,
    WolfAction,
)
from app.engine import GameEngine


NAMES = [f"player-{seat}" for seat in range(1, 10)]


def new_classic_game(seed: int = 901):
    engine = GameEngine()
    config = GameConfig(
        random_seed=seed,
        ruleset=Ruleset.classic_9_hunter_v1(),
    )
    game = engine.create_game(
        NAMES,
        config=config,
        game_id=f"classic-engine-{seed}",
    ).game
    game = engine.start_game(game, "start").game
    game.day = 1
    return engine, game


def role_id(game, role: Role) -> str:
    return next(
        player.id
        for player in game.players
        if game.role_assignments[player.id] == role
    )


def role_ids(game, role: Role) -> list[str]:
    return [
        player.id
        for player in game.players
        if game.role_assignments[player.id] == role
    ]


def resolve_night(
    engine: GameEngine,
    game,
    *,
    wolf_target_id: str,
    poison_target_id: str | None = None,
    rescue_target_id: str | None = None,
    command_id: str = "resolve-night",
):
    game.phase = GamePhase.NIGHT_RESOLUTION
    game.wolf_target_id = wolf_target_id
    if poison_target_id is not None or rescue_target_id is not None:
        game.witch_action = WitchAction(
            actor_id=role_id(game, Role.WITCH),
            poison_target_id=poison_target_id,
            rescue_target_id=rescue_target_id,
            reason="test resolution",
        )
    else:
        game.witch_action = None
    return engine.complete_phase(game, command_id)


def submit_vote_round(
    engine: GameEngine,
    game,
    choices: dict[str, str | None],
    prefix: str,
):
    for index, (actor_id, target_id) in enumerate(choices.items()):
        game = engine.submit_vote(
            game,
            VoteAction(
                actor_id=actor_id,
                target_id=target_id,
                abstain=target_id is None,
                reason="test vote",
            ),
            f"{prefix}-{index}",
        ).game
    return game


def resolve_wolf_votes(
    seed: int,
    target_indexes: tuple[int, int, int],
):
    engine, game = new_classic_game(seed)
    game.phase = GamePhase.NIGHT_WOLF
    wolves = role_ids(game, Role.WOLF)
    targets = [
        player.id
        for player in game.players
        if game.role_assignments[player.id] != Role.WOLF
    ][:3]
    for index, (wolf, target_index) in enumerate(zip(wolves, target_indexes)):
        game = engine.submit_wolf_action(
            game,
            WolfAction(
                actor_id=wolf,
                target_id=targets[target_index],
                reason="test wolf vote",
            ),
            f"wolf-vote-{index}",
        ).game
    result = engine.complete_phase(game, "wolf-vote-done")
    return result, targets


def test_same_seed_deals_identical_classic_lineup_with_exact_role_counts():
    _, first = new_classic_game(9001)
    _, second = new_classic_game(9001)

    first_by_seat = {
        player.seat: first.role_assignments[player.id]
        for player in first.players
    }
    second_by_seat = {
        player.seat: second.role_assignments[player.id]
        for player in second.players
    }
    assert first_by_seat == second_by_seat
    assert Counter(first_by_seat.values()) == Counter(
        {
            Role.WOLF: 3,
            Role.SEER: 1,
            Role.WITCH: 1,
            Role.HUNTER: 1,
            Role.VILLAGER: 3,
        }
    )


@pytest.mark.parametrize(
    ("target_indexes", "expected_winner_index", "expected_counts"),
    [
        ((0, 0, 0), 0, (3, 0, 0)),
        ((0, 0, 1), 0, (2, 1, 0)),
    ],
)
def test_three_wolf_vote_majority_matrix_selects_unique_top_target(
    target_indexes: tuple[int, int, int],
    expected_winner_index: int,
    expected_counts: tuple[int, int, int],
):
    result, targets = resolve_wolf_votes(9002, target_indexes)

    assert result.game.wolf_target_id == targets[expected_winner_index]
    selected = next(
        event for event in result.events if event.type == EventType.WOLF_TARGET_SELECTED
    )
    assert selected.payload["counts"] == {
        target: count
        for target, count in zip(targets, expected_counts)
        if count
    }
    assert selected.payload["tied_candidate_ids"] == [targets[expected_winner_index]]


def test_three_wolf_one_one_one_tie_is_deterministic_for_fixed_seed():
    first, targets = resolve_wolf_votes(9003, (0, 1, 2))
    second, second_targets = resolve_wolf_votes(9003, (0, 1, 2))

    assert second_targets == targets
    assert first.game.wolf_target_id == second.game.wolf_target_id
    assert first.game.wolf_target_id in targets
    selected = next(
        event for event in first.events if event.type == EventType.WOLF_TARGET_SELECTED
    )
    assert selected.payload["counts"] == {target: 1 for target in targets}
    assert selected.payload["tied_candidate_ids"] == sorted(targets)
    assert selected.payload["resolution"] == "seeded_tie_break"


def test_seer_inspecting_hunter_returns_good_alignment():
    engine, game = new_classic_game(9004)
    game.phase = GamePhase.NIGHT_SEER
    seer = role_id(game, Role.SEER)
    hunter = role_id(game, Role.HUNTER)

    result = engine.submit_seer_action(
        game,
        SeerAction(actor_id=seer, target_id=hunter, reason="check hunter"),
        "seer-check-hunter",
    )

    assert result.game.role_states[seer].seer_results[hunter] == Alignment.GOOD
    inspection = next(
        event for event in result.events if event.type == EventType.SEER_INSPECTION
    )
    assert inspection.audience_player_ids == [seer]
    assert inspection.payload["target_id"] == hunter
    assert inspection.payload["alignment"] == Alignment.GOOD.value


def test_wolf_killed_hunter_can_shoot_and_replay_is_idempotent():
    engine, game = new_classic_game(910)
    hunter = role_id(game, Role.HUNTER)
    wolf = role_ids(game, Role.WOLF)[0]

    resolved = resolve_night(engine, game, wolf_target_id=hunter)
    game = resolved.game
    assert game.phase == GamePhase.DAY_ANNOUNCEMENT
    assert game.winner is None
    assert game.pending_hunter_action is not None
    assert game.pending_hunter_action.hunter_id == hunter
    assert game.player(hunter).death_causes == [DeathCause.WOLF_ATTACK]

    announced = engine.complete_phase(game, "announce-hunter")
    game = announced.game
    assert game.phase == GamePhase.HUNTER_REACTION
    assert any(
        event.type == EventType.HUNTER_ACTION_REQUESTED
        for event in announced.events
    )
    self_target = engine.validate_action(
        game,
        HunterAction(
            actor_id=hunter,
            action="shoot",
            target_id=hunter,
            reason="invalid self target",
        ),
    )
    assert self_target.code == "SELF_TARGET_NOT_ALLOWED"

    action = HunterAction(
        actor_id=hunter,
        action="shoot",
        target_id=wolf,
        reason="take a wolf",
    )
    shot = engine.submit_hunter_action(game, action, "hunter-shot")
    assert shot.game.phase == GamePhase.DAY_DISCUSSION
    assert shot.game.pending_hunter_action is None
    assert not shot.game.role_states[hunter].hunter_shot_available
    assert shot.game.player(wolf).status == PlayerStatus.DEAD
    assert shot.game.player(wolf).death_causes == [DeathCause.HUNTER_SHOT]
    assert not any(event.type == EventType.LAST_WORDS for event in shot.events)

    event_count = len(shot.game.events)
    replay = engine.submit_hunter_action(shot.game, action, "hunter-shot")
    assert replay.idempotent_replay
    assert len(replay.game.events) == event_count
    assert [event.event_id for event in replay.events] == [
        event.event_id for event in shot.events
    ]


def test_exiled_hunter_can_pass_then_game_enters_next_night():
    engine, game = new_classic_game(911)
    hunter = role_id(game, Role.HUNTER)
    game.phase = GamePhase.DAY_RESOLUTION
    game.pending_exile_id = hunter
    game.last_words_submitted = True

    exiled = engine.complete_phase(game, "exile-hunter")
    game = exiled.game
    assert game.phase == GamePhase.HUNTER_REACTION
    assert game.player(hunter).death_causes == [DeathCause.EXILE]

    passed = engine.submit_hunter_action(
        game,
        HunterAction(actor_id=hunter, action="pass", reason="save the shot"),
        "hunter-pass",
    )
    assert passed.game.phase == GamePhase.NIGHT_SEER
    assert passed.game.day == 2
    assert passed.game.pending_hunter_action is None
    assert not passed.game.role_states[hunter].hunter_shot_available
    assert any(event.type == EventType.HUNTER_PASSED for event in passed.events)


def test_hunter_exile_orders_last_words_death_and_reaction_events():
    engine, game = new_classic_game(9005)
    hunter = role_id(game, Role.HUNTER)
    game.phase = GamePhase.DAY_RESOLUTION
    game.pending_exile_id = hunter

    last_words = engine.submit_last_words(
        game,
        LastWordsAction(
            actor_id=hunter,
            content="final hunter statement",
            reason="test last words",
        ),
        "hunter-last-words",
    )
    exiled = engine.complete_phase(last_words.game, "resolve-hunter-exile")

    event_types = [
        event.type
        for event in [*last_words.events, *exiled.events]
        if event.type
        in {
            EventType.LAST_WORDS,
            EventType.PLAYER_DIED,
            EventType.PHASE_CHANGED,
            EventType.HUNTER_ACTION_REQUESTED,
        }
    ]
    assert event_types == [
        EventType.LAST_WORDS,
        EventType.PLAYER_DIED,
        EventType.PHASE_CHANGED,
        EventType.HUNTER_ACTION_REQUESTED,
    ]
    sequences = [event.sequence for event in [*last_words.events, *exiled.events]]
    assert sequences == sorted(sequences)
    assert len(sequences) == len(set(sequences))
    assert exiled.game.phase == GamePhase.HUNTER_REACTION
    assert exiled.game.player(hunter).death_causes == [DeathCause.EXILE]
    assert exiled.game.pending_hunter_action is not None
    assert exiled.game.pending_hunter_action.hunter_id == hunter


@pytest.mark.parametrize("wolf_hits_hunter", [False, True])
def test_poison_suppresses_hunter_reaction_including_wolf_and_poison(
    wolf_hits_hunter: bool,
):
    engine, game = new_classic_game(912 + int(wolf_hits_hunter))
    hunter = role_id(game, Role.HUNTER)
    wolf_target = (
        hunter
        if wolf_hits_hunter
        else next(
            player.id
            for player in game.players
            if game.role_assignments[player.id] == Role.VILLAGER
        )
    )

    resolved = resolve_night(
        engine,
        game,
        wolf_target_id=wolf_target,
        poison_target_id=hunter,
    )
    game = resolved.game
    assert game.pending_hunter_action is None
    expected = (
        [DeathCause.WOLF_ATTACK, DeathCause.WITCH_POISON]
        if wolf_hits_hunter
        else [DeathCause.WITCH_POISON]
    )
    assert game.player(hunter).death_causes == expected

    announced = engine.complete_phase(game, "announce-poisoned-hunter")
    assert announced.game.phase == GamePhase.DAY_DISCUSSION
    assert not any(
        event.type == EventType.HUNTER_ACTION_REQUESTED
        for event in announced.game.events
    )


def test_rescued_hunter_stays_alive_and_does_not_trigger():
    engine, game = new_classic_game(914)
    hunter = role_id(game, Role.HUNTER)

    resolved = resolve_night(
        engine,
        game,
        wolf_target_id=hunter,
        rescue_target_id=hunter,
    )
    assert resolved.game.player(hunter).status == PlayerStatus.ALIVE
    assert resolved.game.player(hunter).death_causes == []
    assert resolved.game.night_death_ids == []
    assert resolved.game.pending_hunter_action is None
    announced = engine.complete_phase(resolved.game, "announce-rescue")
    assert announced.game.phase == GamePhase.DAY_DISCUSSION


def test_last_god_hunter_shoots_last_wolf_and_elimination_wins_first():
    engine, game = new_classic_game(915)
    hunter = role_id(game, Role.HUNTER)
    wolves = role_ids(game, Role.WOLF)
    for god_role in (Role.SEER, Role.WITCH):
        game.player(role_id(game, god_role)).status = PlayerStatus.DEAD
    for wolf in wolves[:-1]:
        game.player(wolf).status = PlayerStatus.DEAD

    game.phase = GamePhase.DAY_RESOLUTION
    game.pending_exile_id = hunter
    game.last_words_submitted = True
    game = engine.complete_phase(game, "last-god-exiled").game
    assert game.phase == GamePhase.HUNTER_REACTION
    assert engine.check_win(game) is None

    result = engine.submit_hunter_action(
        game,
        HunterAction(
            actor_id=hunter,
            action="shoot",
            target_id=wolves[-1],
            reason="final shot",
        ),
        "final-shot",
    )
    assert result.game.phase == GamePhase.FINISHED
    assert result.game.winner is not None
    assert result.game.winner.winner == Winner.GOOD
    assert result.game.winner.reason_code == WinReason.ALL_WOLVES_ELIMINATED
    assert result.game.winner.alive_gods == 0


def test_slaughter_side_and_parity_win_conditions():
    engine, wolves_dead = new_classic_game(916)
    for wolf in role_ids(wolves_dead, Role.WOLF):
        wolves_dead.player(wolf).status = PlayerStatus.DEAD
    win = engine.check_win(wolves_dead)
    assert win is not None
    assert win.winner == Winner.GOOD
    assert win.reason_code == WinReason.ALL_WOLVES_ELIMINATED

    engine, gods_dead = new_classic_game(917)
    for role in (Role.SEER, Role.WITCH, Role.HUNTER):
        gods_dead.player(role_id(gods_dead, role)).status = PlayerStatus.DEAD
    win = engine.check_win(gods_dead)
    assert win is not None
    assert win.winner == Winner.WOLVES
    assert win.reason_code == WinReason.ALL_GODS_ELIMINATED
    assert win.alive_gods == 0
    assert win.alive_villagers == 3

    engine, villagers_dead = new_classic_game(918)
    for villager in role_ids(villagers_dead, Role.VILLAGER):
        villagers_dead.player(villager).status = PlayerStatus.DEAD
    win = engine.check_win(villagers_dead)
    assert win is not None
    assert win.winner == Winner.WOLVES
    assert win.reason_code == WinReason.ALL_VILLAGERS_ELIMINATED
    assert win.alive_villagers == 0

    engine, parity = new_classic_game(919)
    keep_alive = set(
        role_ids(parity, Role.WOLF)[:2]
        + [role_id(parity, Role.HUNTER)]
        + role_ids(parity, Role.VILLAGER)[:1]
    )
    for player in parity.players:
        if player.id not in keep_alive:
            player.status = PlayerStatus.DEAD
    assert len(parity.alive_players()) == 4
    win = engine.check_win(parity)
    assert win is not None
    assert win.winner == Winner.WOLVES
    assert win.reason_code == WinReason.WOLVES_REACHED_PARITY
    assert win.alive_wolves == 2
    assert win.alive_good == 2
    assert win.alive_gods == 1
    assert win.alive_villagers == 1

    engine, majority = new_classic_game(9191)
    keep_alive = set(
        role_ids(majority, Role.WOLF)
        + [role_id(majority, Role.HUNTER)]
        + role_ids(majority, Role.VILLAGER)[:1]
    )
    for player in majority.players:
        if player.id not in keep_alive:
            player.status = PlayerStatus.DEAD
    assert len(majority.alive_players()) == 5
    win = engine.check_win(majority)
    assert win is not None
    assert win.winner == Winner.WOLVES
    assert win.reason_code == WinReason.WOLVES_REACHED_PARITY
    assert win.alive_wolves == 3
    assert win.alive_good == 2

    # 规则升级前创建的对局可能已经越过天亮结算点；下一次主持人推进
    # 应直接补结算，而不是要求剩余玩家继续完成白天发言。
    majority.phase = GamePhase.DAY_DISCUSSION
    result = engine.complete_phase(majority, "finish-upgraded-game")
    assert result.game.phase == GamePhase.FINISHED
    assert result.game.winner is not None
    assert result.game.winner.reason_code == WinReason.WOLVES_REACHED_PARITY
    assert result.events[-1].type == EventType.GAME_FINISHED

    engine, priority = new_classic_game(920)
    for role in (Role.WOLF, Role.SEER, Role.WITCH, Role.HUNTER):
        for player_id in role_ids(priority, role):
            priority.player(player_id).status = PlayerStatus.DEAD
    win = engine.check_win(priority)
    assert win is not None
    assert win.winner == Winner.GOOD
    assert win.reason_code == WinReason.ALL_WOLVES_ELIMINATED


def test_classic_runoff_has_candidate_speeches_and_non_candidate_voters():
    engine, game = new_classic_game(921)
    game.phase = GamePhase.DAY_VOTING
    player_ids = [player.id for player in game.alive_players()]
    first_candidate, second_candidate = player_ids[:2]
    first_round = {
        first_candidate: second_candidate,
        second_candidate: None,
        **{
            player_id: first_candidate if index < 4 else second_candidate
            for index, player_id in enumerate(player_ids[2:])
        },
    }
    game = submit_vote_round(engine, game, first_round, "first-vote")
    runoff = engine.complete_phase(game, "first-vote-done")
    game = runoff.game
    assert game.phase == GamePhase.DAY_RUNOFF_DISCUSSION
    assert game.runoff_candidate_ids == [first_candidate, second_candidate]
    assert any(event.type == EventType.RUNOFF_STARTED for event in runoff.events)

    non_candidate = player_ids[2]
    invalid_speech = engine.validate_action(
        game,
        SpeechAction(
            actor_id=non_candidate,
            content="not a candidate",
            reason="invalid runoff speech",
        ),
    )
    assert invalid_speech.code == "NOT_RUNOFF_CANDIDATE"

    for index, candidate in enumerate(game.runoff_candidate_ids):
        game = engine.submit_speech(
            game,
            SpeechAction(
                actor_id=candidate,
                content=f"runoff speech {index}",
                reason="defend self",
            ),
            f"runoff-speech-{index}",
        ).game
    assert set(game.runoff_speeches) == {first_candidate, second_candidate}
    game = engine.complete_phase(game, "runoff-speeches-done").game
    assert game.phase == GamePhase.DAY_VOTING

    candidate_vote = engine.validate_action(
        game,
        VoteAction(
            actor_id=first_candidate,
            target_id=second_candidate,
            reason="candidate cannot vote",
        ),
    )
    assert candidate_vote.code == "RUNOFF_CANDIDATE_CANNOT_VOTE"

    eligible = player_ids[2:]
    second_round = {
        player_id: (
            first_candidate
            if index < 3
            else second_candidate
            if index < 6
            else None
        )
        for index, player_id in enumerate(eligible)
    }
    game = submit_vote_round(engine, game, second_round, "second-vote")
    game = engine.complete_phase(game, "second-vote-done").game
    assert game.phase == GamePhase.DAY_RESOLUTION
    assert game.pending_exile_id is None

    resolved = engine.complete_phase(game, "runoff-no-exile")
    assert resolved.game.phase == GamePhase.NIGHT_SEER
    assert resolved.game.day == 2
    assert any(event.type == EventType.NO_EXILE for event in resolved.events)


def test_nine_way_runoff_with_no_eligible_voter_exiles_nobody():
    engine, game = new_classic_game(922)
    game.phase = GamePhase.DAY_VOTING
    player_ids = [player.id for player in game.alive_players()]
    cycle_votes = {
        player_id: player_ids[(index + 1) % len(player_ids)]
        for index, player_id in enumerate(player_ids)
    }
    game = submit_vote_round(engine, game, cycle_votes, "cycle-vote")
    game = engine.complete_phase(game, "cycle-vote-done").game
    assert game.phase == GamePhase.DAY_RUNOFF_DISCUSSION
    assert game.runoff_candidate_ids == player_ids

    for index, candidate in enumerate(player_ids):
        game = engine.submit_speech(
            game,
            SpeechAction(
                actor_id=candidate,
                content=f"candidate {index}",
                reason="nine-way tie",
            ),
            f"nine-way-speech-{index}",
        ).game
    game = engine.complete_phase(game, "nine-way-speeches-done").game
    assert game.phase == GamePhase.DAY_VOTING

    no_exile = engine.complete_phase(game, "no-eligible-voters")
    assert no_exile.game.phase == GamePhase.NIGHT_SEER
    assert no_exile.game.day == 2
    event = next(event for event in no_exile.events if event.type == EventType.NO_EXILE)
    assert event.payload["reason"] == "没有合格的重投票人"


def test_first_round_all_abstain_skips_runoff():
    engine, game = new_classic_game(923)
    game.phase = GamePhase.DAY_VOTING
    choices = {player.id: None for player in game.alive_players()}
    game = submit_vote_round(engine, game, choices, "all-abstain")
    result = engine.complete_phase(game, "all-abstain-done")
    assert result.game.phase == GamePhase.NIGHT_SEER
    assert result.game.day == 2
    assert not any(event.type == EventType.RUNOFF_STARTED for event in result.events)


def test_second_round_all_eligible_voters_abstain_exiles_nobody():
    engine, game = new_classic_game(9006)
    game.phase = GamePhase.DAY_VOTING
    player_ids = [player.id for player in game.alive_players()]
    first_candidate, second_candidate = player_ids[:2]
    first_round = {
        first_candidate: second_candidate,
        second_candidate: None,
        **{
            player_id: first_candidate if index < 4 else second_candidate
            for index, player_id in enumerate(player_ids[2:])
        },
    }
    game = submit_vote_round(engine, game, first_round, "runoff-abstain-first")
    game = engine.complete_phase(game, "runoff-abstain-first-done").game
    assert game.phase == GamePhase.DAY_RUNOFF_DISCUSSION
    assert game.vote_round == 2
    assert game.runoff_candidate_ids == [first_candidate, second_candidate]

    for index, candidate in enumerate(game.runoff_candidate_ids):
        game = engine.submit_speech(
            game,
            SpeechAction(
                actor_id=candidate,
                content=f"runoff statement {index}",
                reason="test runoff",
            ),
            f"runoff-abstain-speech-{index}",
        ).game
    game = engine.complete_phase(game, "runoff-abstain-speeches-done").game
    eligible_voters = [
        player_id
        for player_id in player_ids
        if player_id not in game.runoff_candidate_ids
    ]
    game = submit_vote_round(
        engine,
        game,
        {player_id: None for player_id in eligible_voters},
        "runoff-all-abstain",
    )
    result = engine.complete_phase(game, "runoff-all-abstain-done")

    assert result.game.pending_exile_id is None
    assert result.game.phase == GamePhase.NIGHT_SEER
    assert result.game.day == 2
    revealed = next(
        event for event in result.events if event.type == EventType.VOTES_REVEALED
    )
    assert revealed.payload["round"] == 2
    assert revealed.payload["counts"] == {}
    assert revealed.payload["abstain_count"] == len(eligible_voters)
    assert any(event.type == EventType.NO_EXILE for event in result.events)
