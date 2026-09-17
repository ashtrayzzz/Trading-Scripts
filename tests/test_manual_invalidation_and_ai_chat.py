"""Unit tests for manual opportunity invalidation, CLI command, and AI strategy Q&A assistant."""

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from typer.testing import CliRunner

from src.cli import app
from src.core.enums import CandidateLifecycle, QualityStatus
from src.core.models import (
    Base,
    MarketObservation,
    OpportunityVersion,
    OutcomeRecord,
)
from src.core.time import utc_now
from src.intelligence.ai_review import ask_ai_strategy_chat
from src.opportunities.aggregation import build_where_why_how
from src.opportunities.lifecycle import OpportunityLifecycleManager


@pytest.fixture
def mem_session():
    """Isolated in-memory SQLite database session."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def test_manually_invalidate_opportunity_basic(mem_session):
    """Test manual invalidation transitions opportunity, records excursions and creates OutcomeRecord."""
    now = utc_now()
    inst = "bybit:SOL_USDT_USDT:perpetual"
    horizon = "1h"

    # Setup active opportunity
    opp = OpportunityVersion(
        opportunity_id="SOL_1H_LONG",
        revision=1,
        playbook_name="shla_sweep",
        instrument_id=inst,
        direction="long",
        horizon=horizon,
        lifecycle_status=CandidateLifecycle.ACTIVE.value,
        merit_score=88.0,
        confidence_score=0.82,
        trigger_price=150.0,
        invalidation_price=145.0,
        thesis="Bullish liquidity sweep",
        as_of=now - timedelta(hours=2),
        available_at=now - timedelta(hours=2),
        quality_status=QualityStatus.VALID.value,
        producer="test",
        producer_version="0.1.0",
        score_breakdown={"confluence_quality": 85.0},
    )
    mem_session.add(opp)

    # Observation showing price drifted to 152.0 (MFE > 0)
    bar = MarketObservation(
        instrument_id=inst,
        interval=horizon,
        open_time=now - timedelta(hours=1),
        close_time=now,
        open=150.0,
        high=153.0,
        low=149.0,
        close=152.0,
        volume=500.0,
        quality_status=QualityStatus.VALID.value,
        producer="test",
        producer_version="0.1.0",
        available_at=now,
    )
    mem_session.add(bar)
    mem_session.commit()

    mgr = OpportunityLifecycleManager()
    updated = mgr.manually_invalidate_opportunity(
        session=mem_session,
        opportunity_id="SOL_1H_LONG",
        reason="market_structure_broken",
        user_notes="Bearish divergence on 4h candle close",
    )

    assert updated is not None
    assert updated.lifecycle_status == CandidateLifecycle.INVALIDATED.value
    assert "market_structure_broken" in updated.counter_evidence
    assert "Bearish divergence on 4h candle close" in updated.counter_evidence

    # Check telemetry payload
    telem = updated.score_breakdown.get("invalidation_telemetry")
    assert telem is not None
    assert telem["status"] == "invalidated"
    assert telem["reason"] == "manual_market_structure_broken"
    assert telem["breach_price"] == 152.0
    assert telem["mfe_r"] > 0.0
    assert telem["user_notes"] == "Bearish divergence on 4h candle close"

    # Verify OutcomeRecord creation
    outcome = (
        mem_session.query(OutcomeRecord)
        .filter_by(opportunity_version_id=updated.id, is_actual=False)
        .first()
    )
    assert outcome is not None
    assert outcome.exit_reason == "manual_market_structure_broken"
    assert outcome.producer == "trader_manual_override"
    assert outcome.max_favorable_excursion == telem["mfe_r"]


def test_build_where_why_how_trigger_timestamp():
    """Verify that build_where_why_how formats explicit trigger timestamp and price."""
    created_at = datetime(2026, 9, 17, 14, 30, 0)
    opp = OpportunityVersion(
        opportunity_id="TEST_TRIGGER_TIME",
        revision=1,
        playbook_name="vcei_squeeze",
        instrument_id="bybit:ETH_USDT_USDT:perpetual",
        direction="long",
        horizon="1h",
        lifecycle_status=CandidateLifecycle.ACTIVE.value,
        merit_score=92.0,
        trigger_price=3500.0,
        invalidation_price=3450.0,
        thesis="Squeeze breakout test",
        created_at=created_at,
        as_of=created_at,
        available_at=created_at,
    )

    where_s, why_s, how_s, *_ = build_where_why_how(opp)

    # Both where and how summaries should contain explicit trigger time and price
    assert "Triggered at 14:30 UTC on 2026-09-17" in where_s
    assert "3500.0000" in where_s
    assert "Triggered at 14:30 UTC on 2026-09-17" in how_s
    assert "Invalidation Stop: \\$3450.0000" in where_s


def test_ask_ai_strategy_chat_prompt_construction():
    """Test ask_ai_strategy_chat formats comprehensive setup context into prompt."""
    opp_context = {
        "ticker": "SOL",
        "instrument_id": "bybit:SOL_USDT_USDT:perpetual",
        "playbook_name": "shla_sweep",
        "horizon": "1h",
        "direction": "long",
        "merit_score": "88.5",
        "confidence_pct": "85",
        "trigger_price": 150.0,
        "trigger_time": "14:00 UTC on 2026-09-17",
        "invalidation_price": 145.0,
        "stop_pct": 3.33,
        "target_2r": 160.0,
        "target_3r": 165.0,
        "latest_price": 151.8,
        "drift_pct": 1.20,
        "current_rr": 1.64,
        "technical_signals": "shla_sweep_completed, vcei_compression",
        "how_summary": "Triggered at 14:00 UTC. Enter at \\$150.0000",
        "macro_context": "Risk-on session expansion",
    }

    mock_resp = MagicMock()
    mock_resp.choices = [MagicMock(message=MagicMock(content="### Strategic Evaluation\nSetup remains valid."))]

    with patch("openai.OpenAI") as mock_openai_cls:
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = mock_resp

        result = ask_ai_strategy_chat(
            opp_context=opp_context,
            user_question="Is this setup still executable after +1.20% drift?",
            provider="openai",
            api_key="sk-test-key",
            model="gpt-4o",
        )

        assert "Strategic Evaluation" in result
        assert "Setup remains valid." in result

        # Verify prompt received by openai includes key context parameters
        call_args = mock_client.chat.completions.create.call_args[1]
        user_msg = call_args["messages"][-1]["content"]
        assert "SOL" in user_msg
        assert "150.0000" in user_msg
        assert "14:00 UTC on 2026-09-17" in user_msg
        assert "+1.20%" in user_msg
        assert "1.64R" in user_msg
        assert "Is this setup still executable after +1.20% drift?" in user_msg


def test_cli_invalidate_command():
    """Verify Typer CLI invalidate command operates successfully."""
    runner = CliRunner()

    with patch("src.opportunities.lifecycle.OpportunityLifecycleManager.manually_invalidate_opportunity") as mock_inv:
        mock_opp = MagicMock()
        mock_opp.lifecycle_status = "invalidated"
        mock_inv.return_value = mock_opp

        res = runner.invoke(app, ["invalidate", "BTC_1H_TEST", "--reason", "unfavorable_drift", "--notes", "Chased too far"])
        assert res.exit_code == 0
        assert "Successfully invalidated opportunity BTC_1H_TEST" in res.stdout
        assert "unfavorable_drift" in res.stdout
