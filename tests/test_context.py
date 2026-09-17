"""Unit tests for the ContextEngine (Seasonality, Macro, Sentiment, Catalysts)."""

from datetime import datetime, timezone
from src.intelligence.context_engine import ContextEngine


def test_context_engine_seasonality():
    """Verify session detection and day-of-week liquidity characteristics."""
    engine = ContextEngine()

    # Test Asian session (03:00 UTC)
    dt_asia = datetime(2026, 9, 16, 3, 0, tzinfo=timezone.utc)
    res_asia = engine.evaluate_seasonality(dt_asia)
    assert res_asia["current_session"] == "Asian Session"
    assert res_asia["session_bias"] == "range_bound_accumulation"
    assert res_asia["weekday"] == "Wednesday"

    # Test London session (09:00 UTC)
    dt_london = datetime(2026, 9, 16, 9, 0, tzinfo=timezone.utc)
    res_london = engine.evaluate_seasonality(dt_london)
    assert res_london["current_session"] == "London Session"
    assert res_london["liquidity_level"] == "high"

    # Test New York session (15:00 UTC)
    dt_ny = datetime(2026, 9, 16, 15, 0, tzinfo=timezone.utc)
    res_ny = engine.evaluate_seasonality(dt_ny)
    assert res_ny["current_session"] == "New York Session"
    assert res_ny["liquidity_level"] == "peak"


def test_context_engine_catalysts_and_warnings():
    """Verify countdown calculations and warning bands for economic catalysts."""
    engine = ContextEngine()
    catalysts = engine.get_upcoming_catalysts()
    assert len(catalysts) > 0

    first = catalysts[0]
    assert "event" in first
    assert "hours_until" in first
    assert "trading_warning" in first
    assert first["hours_until"] >= 0


def test_context_engine_sentiment_and_confluence():
    """Verify sentiment evaluation and opportunity confluence cross-referencing."""
    engine = ContextEngine()
    sentiment = engine.evaluate_market_sentiment()
    assert "fear_greed_score" in sentiment
    assert 0 <= sentiment["fear_greed_score"] <= 100
    assert "equity_breadth" in sentiment

    summary = engine.compute_context_summary()
    assert "seasonality" in summary
    assert "macro" in summary
    assert "sentiment" in summary
    assert "catalysts" in summary

    # Evaluate confluence for long trade
    conf_long = engine.evaluate_opportunity_confluence(
        direction="long",
        horizon="1h",
        context_summary=summary,
    )
    assert "is_macro_aligned" in conf_long
    assert "confluence_rating" in conf_long
    assert len(conf_long["confluence_notes"]) > 0
