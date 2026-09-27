from __future__ import annotations

import copy
from typing import Any

import pytest

from fabio_mechanical.contracts import Direction, SetupType
from fabio_mechanical.decision import (
    AIDisabledError,
    AIProviderStub,
    MechanicalDecisionProvider,
    get_decision_provider,
    validate_provider_output,
)
from fabio_mechanical.strategy import (
    InvalidTransition,
    MeanReversionMachine,
    MeanRevState,
    StopWideningError,
    TrendMachine,
    TrendState,
    validate_stop_update,
)
from fabio_mechanical.strategy.common import choose_lvn

from .fixtures.snapshots import (
    snapshot,
    valid_mean_reversion_long,
    valid_mean_reversion_short,
    valid_trend_long,
    valid_trend_short,
    zone,
)

TREND_PATH = [
    "BALANCED",
    "VALID_BREAKOUT",
    "DISPLACEMENT",
    "STRUCTURE_BREAK",
    "IMPULSE_LOCKED",
    "IMPULSE_PROFILE_READY",
    "WAIT_LVN_RETEST",
    "LVN_TOUCHED",
    "WAIT_AGGRESSION",
    "ENTRY_READY",
]
MR_PATH = [
    "BALANCED",
    "BREAKOUT_ATTEMPT",
    "NO_ACCEPTANCE",
    "FAILED_AUCTION",
    "VALUE_RECLAIM",
    "RECLAIM_LEG_LOCKED",
    "RECLAIM_PROFILE",
    "WAIT_LVN_RETEST",
    "SNAPBACK_AGGRESSION",
    "ENTRY_READY",
]


def test_trend_long_all_transitions_and_candidate(cfg: dict[str, Any]) -> None:
    m = TrendMachine(cfg)
    c = m.update(valid_trend_long())
    assert c is not None and c.direction == Direction.LONG and c.setup_type == SetupType.TREND
    assert m.history == TREND_PATH
    assert (c.stop_ticks, c.entry_reference_ticks, c.target_ticks) == (401, 404, 420)
    assert c.decision_snapshot.ai_enabled is False and c.decision_snapshot.decision == "ENTER"
    assert c.evidence["aggression"]["ok"] is True and c.evidence["lvn"]["low_ticks"] == 403


def test_trend_short_candidate(cfg: dict[str, Any]) -> None:
    c = TrendMachine(cfg).update(valid_trend_short())
    assert c is not None and c.direction == Direction.SHORT
    assert (c.target_ticks, c.entry_reference_ticks, c.stop_ticks) == (400, 414, 417)


def test_meanrev_long_and_short_all_transitions(cfg: dict[str, Any]) -> None:
    m = MeanReversionMachine(cfg)
    c = m.update(valid_mean_reversion_long())
    assert c is not None and c.direction == Direction.LONG and m.history == MR_PATH
    assert (c.stop_ticks, c.target_ticks) == (394, 420)
    c2 = MeanReversionMachine(cfg).update(valid_mean_reversion_short())
    assert c2 is not None and c2.direction == Direction.SHORT and (c2.stop_ticks, c2.target_ticks) == (446, 420)


def test_candidate_ids_are_deterministic(cfg: dict[str, Any]) -> None:
    a = TrendMachine(cfg).update(valid_trend_long())
    b = TrendMachine(copy.deepcopy(cfg)).update(valid_trend_long())
    assert a is not None and b is not None and a.candidate_id == b.candidate_id


def test_invalid_transition_rejected(cfg: dict[str, Any]) -> None:
    with pytest.raises(InvalidTransition):
        TrendMachine(cfg).force_transition(TrendState.DISPLACEMENT)


def test_lvn_touch_without_aggression_is_never_an_entry(cfg: dict[str, Any]) -> None:
    m = TrendMachine(cfg)
    assert m.update(snapshot(1, aggression=False)) is None
    assert m.state == TrendState.WAIT_AGGRESSION
    mr = MeanReversionMachine(cfg)
    assert mr.update(snapshot(1, Direction.LONG, "mean", aggression=False)) is None and mr.state == MeanRevState.WAIT_LVN_RETEST


def test_no_entry_on_unsafe_or_stale_data(cfg: dict[str, Any]) -> None:
    m = TrendMachine(cfg)
    assert m.update(snapshot(1, safe=False)) is None and m.state == TrendState.BALANCED
    m2 = TrendMachine(cfg)
    assert m2.update(snapshot(1, stale=10_000)) is None and m2.state == TrendState.BALANCED


def test_absorption_blocks_trade(cfg: dict[str, Any]) -> None:
    m = TrendMachine(cfg)
    assert m.update(snapshot(1, absorption=0.95)) is None and m.state == TrendState.WAIT_AGGRESSION


def test_session_windows_enforced(cfg: dict[str, Any]) -> None:
    m = TrendMachine(cfg)
    assert m.update(snapshot(1, session="LONDON")) is None and m.state == TrendState.BALANCED
    assert MeanReversionMachine(cfg).update(snapshot(1, Direction.LONG, "mean", session="GLOBEX_OVERNIGHT")) is None


def test_aggression_must_be_at_location(cfg: dict[str, Any]) -> None:
    m = TrendMachine(cfg)
    s = snapshot(1, aggression_window_low_ticks=410, aggression_window_high_ticks=414)
    assert m.update(s) is None and m.state == TrendState.WAIT_AGGRESSION


def test_aggression_persistence_requires_consecutive_observations(cfg: dict[str, Any]) -> None:
    cfg["strategy"]["aggression_persistence"] = 2
    m = TrendMachine(cfg)
    assert m.update(snapshot(1)) is None and m.aggression_streak == 1
    assert m.update(snapshot(2, aggression=False)) is None and m.aggression_streak == 0
    assert m.update(snapshot(3)) is None
    assert m.update(snapshot(4)) is not None


def test_no_target_means_no_trade_and_reset(cfg: dict[str, Any]) -> None:
    m = TrendMachine(cfg)
    assert m.update(snapshot(1, relevant_poc_candidates=[390])) is None  # no POC above a long entry
    assert m.state == TrendState.BALANCED and m.last_rejection == "no_balance_poc_target_in_trade_direction"
    assert any(t.reason.startswith("no_candidate") for t in m.transitions)


def test_structural_invalidation_before_entry_resets(cfg: dict[str, Any]) -> None:
    m = TrendMachine(cfg)
    m.update(snapshot(1, aggression=False))
    assert m.state == TrendState.WAIT_AGGRESSION
    m.update(snapshot(2, price=400, aggression=False))  # traded through LVN low (403) - 2 tick buffer
    assert m.state == TrendState.BALANCED
    assert m.transitions[-1].reason == "structural_invalidation_before_entry"


def test_setup_expiry_counts_from_setup_start(cfg: dict[str, Any]) -> None:
    m = TrendMachine(cfg)
    for i in range(1, 200):  # long quiet BALANCED period: must not pre-expire a later setup
        m.update(snapshot(i, auction={"out_of_balance": False}))
    assert m.state == TrendState.BALANCED
    m.update(snapshot(200, aggression=False))
    assert m.state == TrendState.WAIT_AGGRESSION
    started = m.setup_start_period
    later = 200 + (cfg["strategy"]["setup_expiry_events"] + 2) * 60
    s_late = snapshot(later, aggression=False)
    m.update(s_late)
    assert any(t.reason == "setup_expired" for t in m.transitions)
    assert m.setup_start_period == s_late.structure_period_index != started  # structure still valid: a fresh setup


def test_exit_returns_to_balanced_and_leg_is_not_retraded(cfg: dict[str, Any]) -> None:
    m = TrendMachine(cfg)
    assert m.update(snapshot(1)) is not None
    m.on_trade_opened(None)
    m.on_trade_closed(None, "TARGET")
    assert m.state == TrendState.EXIT
    assert m.update(snapshot(2)) is None  # same impulse leg id: no second trade on it
    assert m.state == TrendState.STRUCTURE_BREAK
    new_leg = snapshot(3, auction={"impulse_id": "B1:I:2"})
    assert m.update(new_leg) is not None


def test_entry_rejection_resets_entry_ready(cfg: dict[str, Any]) -> None:
    m = TrendMachine(cfg)
    assert m.update(snapshot(1)) is not None and m.state == TrendState.ENTRY_READY
    m.on_entry_rejected(None, "cooldown")
    assert m.state == TrendState.BALANCED


def test_reference_balance_change_resets_setup(cfg: dict[str, Any]) -> None:
    m = MeanReversionMachine(cfg)
    m.update(snapshot(1, Direction.LONG, "mean", aggression=False))
    assert m.state == MeanRevState.WAIT_LVN_RETEST
    m.update(
        snapshot(
            2,
            Direction.LONG,
            "mean",
            aggression=False,
            auction={"balance_id": "OTHER", "failed_auction": False, "breakout_side": Direction.FLAT},
        )
    )
    assert m.state == MeanRevState.BALANCED and m.transitions[-1].reason == "reference_balance_changed"


def test_meanrev_target_is_setup_balance_poc(cfg: dict[str, Any]) -> None:
    m = MeanReversionMachine(cfg)
    m.update(snapshot(1, Direction.LONG, "mean", aggression=False))
    drifted = snapshot(2, Direction.LONG, "mean", auction={"balance_poc_ticks": 412})  # developing POC drifted toward entry
    c = m.update(drifted)
    assert c is not None and c.target_ticks == 420


def test_lvn_selection_is_direction_aware(cfg: dict[str, Any]) -> None:
    s = snapshot(1)
    above = zone(406, 407)
    below = zone(398, 400)
    prof = s.impulse_profile.model_copy(update={"lvn_zones": [above, below]})  # type: ignore[union-attr]
    s2 = s.model_copy(update={"impulse_profile": prof})
    chosen = choose_lvn(s2, "impulse_profile", Direction.LONG, cfg)
    assert chosen is not None and chosen.low_ticks == 398  # the zone above price was already traded through
    weak = s.impulse_profile.model_copy(update={"lvn_zones": [zone(403, 405, ratio=0.9)]})  # type: ignore[union-attr]
    assert choose_lvn(s.model_copy(update={"impulse_profile": weak}), "impulse_profile", Direction.LONG, cfg) is None
    exhausted = s.impulse_profile.model_copy(update={"lvn_zones": [zone(403, 405, retests=9)]})  # type: ignore[union-attr]
    assert choose_lvn(s.model_copy(update={"impulse_profile": exhausted}), "impulse_profile", Direction.LONG, cfg) is None
    shallow_valley = s.impulse_profile.model_copy(update={"lvn_zones": [zone(403, 405, neighbor_ratio=1.1)]})  # type: ignore[union-attr]
    assert choose_lvn(s.model_copy(update={"impulse_profile": shallow_valley}), "impulse_profile", Direction.LONG, cfg) is None


def test_no_stop_widening() -> None:
    with pytest.raises(StopWideningError):
        validate_stop_update(Direction.LONG, 400, 399)
    with pytest.raises(StopWideningError):
        validate_stop_update(Direction.SHORT, 400, 401)
    assert validate_stop_update(Direction.LONG, 400, 401) == 401
    assert validate_stop_update(Direction.SHORT, 400, 399) == 399


def test_ai_disabled_and_provider_contract(cfg: dict[str, Any]) -> None:
    s = valid_trend_long()
    c = TrendMachine(cfg).update(s)
    assert c is not None
    assert isinstance(get_decision_provider(cfg), MechanicalDecisionProvider)
    assert validate_provider_output(c, MechanicalDecisionProvider().decide(s, c)) is True
    with pytest.raises(AIDisabledError):
        AIProviderStub().decide(s, c)
    bad = copy.deepcopy(cfg)
    bad["AI_ENABLED"] = True
    with pytest.raises(AIDisabledError):
        get_decision_provider(bad)
    veto = c.decision_snapshot.model_copy(update={"decision": "NO_TRADE"})
    assert validate_provider_output(c, veto) is False
    flipped = c.decision_snapshot.model_copy(update={"direction": Direction.SHORT})
    with pytest.raises(RuntimeError):
        validate_provider_output(c, flipped)
