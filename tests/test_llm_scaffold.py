"""Unit tests for the LLM Scaffold, prompt slots, and strategy deep-dives."""

from unittest.mock import MagicMock
from datetime import timedelta
from src.core.enums import TimeInterval
from src.core.time import interval_to_timedelta
from src.intelligence.llm_scaffold import LLMScaffold, STRATEGY_DEEP_DIVES


def test_strategy_deep_dives_integrity():
    """Verify all 6 institutional strategies contain required first-principles metadata."""
    expected_keys = ["VCEI", "SHLA", "AVIA", "TCMB", "AVWAP", "NERN"]
    for k in expected_keys:
        assert k in STRATEGY_DEEP_DIVES
        meta = STRATEGY_DEEP_DIVES[k]
        assert "purposed_name" in meta
        assert "how_it_works" in meta
        assert "why_it_works" in meta
        assert len(meta["core_assumptions"]) >= 2
        assert "limitations" in meta
        assert "tailored_adjustments" in meta


def test_llm_scaffold_prompt_slots_and_evaluation():
    """Verify slot assembly and deterministic institutional reasoning output."""
    scaffold = LLMScaffold()

    mock_opp = MagicMock()
    mock_opp.instrument_id = "bybit:BTCUSDT:perpetual"
    mock_opp.horizon = "1h"
    mock_opp.direction = "long"
    mock_opp.merit_score = 85.0
    mock_opp.confidence_score = 0.85
    mock_opp.trigger_price = 65000.0
    mock_opp.invalidation_price = 64200.0
    mock_opp.thesis = "LONG on BTC triggered by Squeeze Pro (VCEI) with volume surge."
    mock_opp.counter_evidence = None

    context_summary = {
        "macro": {
            "macro_regime": "risk_on_expansion",
            "us_10y_yield": 4.10,
            "us_10y_trend": "falling",
            "dollar_index_trend": "weakening",
        },
        "seasonality": {
            "current_session": "London Session",
            "session_bias": "trend_expansion",
            "liquidity_level": "high",
            "weekday": "Wednesday",
            "month": "September",
            "seasonality_notes": "Midweek expansion",
        },
        "sentiment": {
            "fear_greed_score": 58,
            "fear_greed_class": "Greed",
            "equity_breadth": "bullish_expansion",
            "contrarian_insight": "Balanced",
        },
        "catalysts": [],
        "nearest_catalyst": None,
        "clean_window_for_breakouts": True,
    }

    account_state = {
        "equity": 10000.0,
        "drawdown_floor": 9700.0,
        "headroom": 250.0,
        "daily_loss_limit": 300.0,
        "factored_risk_usd": 100.0,
    }

    slots = scaffold.build_prompt_slots(
        opp=mock_opp,
        context_summary=context_summary,
        account_id="bp_turbo_10k",
        account_state=account_state,
        user_notes="Taking 2R partial",
    )

    assert "MACRO REGIME" in slots["macro_regime"]
    assert "London Session" in slots["seasonality_session"]
    assert "TARGET ACCOUNT CONSTRAINTS" in slots["account_state"]
    assert "PURPOSED STRATEGY HEURISTICS" in slots["strategy_principles"]

    review = scaffold.evaluate_opportunity(
        opp=mock_opp,
        context_summary=context_summary,
        account_id="bp_turbo_10k",
        account_state=account_state,
    )

    assert review["verdict"] in ("ACCEPT", "MODIFY_RISK", "DEFER", "REJECT")
    assert 0.0 <= review["confluence_confidence"] <= 1.0
    assert len(review["executive_synthesis"]) > 10
    assert "recommended_risk_usd" in review["sizing_and_plan_refinement"]
    assert "assembled_prompt" in review


def test_weekly_and_monthly_intervals():
    """Verify W1 (1w) and M1 (1M) intervals parse and calculate correct timedeltas."""
    assert TimeInterval.W1 == "1w"
    assert TimeInterval.M1 == "1M"

    td_w = interval_to_timedelta("1w")
    assert td_w == timedelta(weeks=1)

    td_m = interval_to_timedelta("1M")
    assert td_m == timedelta(days=30)
