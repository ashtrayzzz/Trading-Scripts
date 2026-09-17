"""Priority validation tests for account rules, drawdown limits, and eligibility."""

from datetime import datetime, time, timezone
from src.accounts.base import AccountState
from src.accounts.breakoutprop import BreakoutpropAccount
from src.accounts.eligibility import AccountEligibilityEngine
from src.core.enums import AssessmentStage, EligibilityResult, RiskState
from src.core.models import OpportunityVersion
from src.core.time import get_breakoutprop_daily_window, utc_now


def test_high_score_cannot_bypass_risk_limit(breakoutprop_account):
    """
    PRIORITY VALIDATION SCENARIO:
    A high opportunity score cannot override a risk block or increase an approved risk budget.
    """
    engine = AccountEligibilityEngine()

    # Opportunity with high merit score (98.5)
    opp = OpportunityVersion(
        opportunity_id="opp_high_merit",
        revision=1,
        playbook_name="breakout",
        instrument_id="bybit:BTCUSDT:perpetual",
        direction="long",
        horizon="1h",
        merit_score=98.5,
        confidence_score=0.95,
        thesis="Exceptional setup",
        trigger_price=50000.0,
        invalidation_price=49000.0,
        score_breakdown={"setup_quality": 100},
        signal_ids=[],
        producer="test",
        producer_version="0.1.0",
        as_of=utc_now(),
        available_at=utc_now(),
    )

    # Account equity is at $9,720 (close to $9,700 floor, remaining buffer = 0)
    # Available headroom before buffer: $20. But buffer is $50, so headroom <= 0!
    exhausted_state = AccountState(equity=9720.0, balance=9720.0)

    snap = engine.evaluate(
        account=breakoutprop_account,
        opportunity=opp,
        state=exhausted_state,
        stage=AssessmentStage.PRELIMINARY,
    )

    # Must be strictly BLOCKED despite 98.5 merit score!
    assert snap.result == EligibilityResult.BLOCKED.value
    assert any("drawdown" in r or "headroom" in r or "paused" in r for r in snap.reason_codes)


def test_headroom_near_floor(breakoutprop_account):
    """
    PRIORITY VALIDATION SCENARIO:
    Equity near floor; checks use remaining headroom without double counting marked losses.
    """
    # Floor: 9700, Buffer: 50.
    # If equity is 9850, static dd headroom = (9850 - 9700) - 50 = 100.
    state = AccountState(equity=9850.0, balance=9850.0)
    effective_h, dd_h, daily_h = breakoutprop_account.calculate_headroom(state)

    assert dd_h == 100.0
    assert effective_h == 100.0


def test_breakoutprop_daily_reset_window():
    """
    PRIORITY VALIDATION SCENARIO:
    Daily reset boundary applies the correct 00:30 UTC time convention.
    """
    # Time before 00:30 UTC today (e.g. 00:15 UTC)
    t_early = datetime(2026, 9, 16, 0, 15, tzinfo=timezone.utc)
    w_start, w_end = get_breakoutprop_daily_window(t_early)
    assert w_start == datetime(2026, 9, 15, 0, 30, tzinfo=timezone.utc)
    assert w_end == datetime(2026, 9, 16, 0, 30, tzinfo=timezone.utc)

    # Time after 00:30 UTC today (e.g. 14:00 UTC)
    t_late = datetime(2026, 9, 16, 14, 0, tzinfo=timezone.utc)
    w_start2, w_end2 = get_breakoutprop_daily_window(t_late)
    assert w_start2 == datetime(2026, 9, 16, 0, 30, tzinfo=timezone.utc)
    assert w_end2 == datetime(2026, 9, 17, 0, 30, tzinfo=timezone.utc)


def test_sizing_adapts_to_wide_swing_stops(breakoutprop_account, personal_account):
    """
    Verify swing trades with wide stops are sized conservatively on Breakoutprop
    to fit within drawdown headroom, while sizing normally on personal accounts.
    """
    # 5% stop swing candidate on BTC
    swing_opp = OpportunityVersion(
        opportunity_id="opp_swing",
        revision=1,
        playbook_name="swing",
        instrument_id="bybit:BTCUSDT:perpetual",
        direction="long",
        horizon="1d",
        merit_score=80.0,
        confidence_score=0.8,
        thesis="Swing pullback",
        trigger_price=50000.0,
        invalidation_price=47500.0,  # 5% stop distance ($2,500)
        score_breakdown={},
        signal_ids=[],
        producer="test",
        producer_version="0.1.0",
        as_of=utc_now(),
        available_at=utc_now(),
    )

    bp_state = AccountState(equity=10000.0, balance=10000.0)
    bp_sizing = breakoutprop_account.evaluate_sizing_and_risk(swing_opp, bp_state)

    assert bp_sizing.allowed is True
    # Risk must be capped by per-trade limit ($100) or headroom
    assert bp_sizing.estimated_risk_usd <= 100.0
    # Stop is 5%, so notional must be <= $100 / 0.05 = $2000 (0.04 BTC)
    assert bp_sizing.notional_value <= 2001.0
    assert bp_sizing.suggested_quantity <= 0.041

    # Personal account with $25,000 equity and 1% risk ($250 risk budget)
    p_state = AccountState(equity=25000.0, balance=25000.0)
    p_sizing = personal_account.evaluate_sizing_and_risk(swing_opp, p_state)
    assert p_sizing.allowed is True
    assert abs(p_sizing.estimated_risk_usd - 250.0) < 1.0
