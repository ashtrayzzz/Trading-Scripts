"""Unit tests for opportunity lifecycle invalidation, real-time drift, and telemetry logging."""

from datetime import datetime, timedelta
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.core.enums import CandidateLifecycle, QualityStatus, SignalDirection
from src.core.models import (
    Base,
    FeatureSnapshot,
    MarketObservation,
    ModuleSignal,
    OpportunityVersion,
    OutcomeRecord,
)
from src.core.time import utc_now
from src.opportunities.assembly import OpportunityAssembler
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


def test_long_stop_breach_invalidation(mem_session):
    """Test that a LONG opportunity is invalidated when subsequent price breaches the stop loss."""
    now = utc_now()
    t0 = now - timedelta(hours=2)
    t1 = now - timedelta(hours=1)
    inst = "bybit:BTC_USDT_USDT:perpetual"
    horizon = "1h"

    # Setup opportunity: Entry 60000, Stop 59000 (1000 risk, 2R target 62000)
    opp = OpportunityVersion(
        opportunity_id="BTC_1H_LONG",
        revision=1,
        playbook_name="vcei_squeeze",
        instrument_id=inst,
        direction="long",
        horizon=horizon,
        lifecycle_status=CandidateLifecycle.SCORED.value,
        merit_score=85.0,
        confidence_score=0.85,
        trigger_price=60000.0,
        invalidation_price=59000.0,
        thesis="Breakout test",
        as_of=t0,
        available_at=t0,
        quality_status=QualityStatus.VALID.value,
        producer="test",
        producer_version="0.1.0",
        score_breakdown={
            "confluence_quality": 80.0,
            "volume_confirmation": 90.0,
            "trend_alignment": 85.0,
            "compression_duration": 80.0,
            "confluence_count": 3,
        },
    )
    mem_session.add(opp)

    # Observation at t0: within range
    bar0 = MarketObservation(
        instrument_id=inst,
        interval=horizon,
        open_time=t0 - timedelta(hours=1),
        close_time=t0,
        open=59800.0,
        high=60500.0,
        low=59700.0,
        close=60100.0,
        volume=100.0,
        quality_status=QualityStatus.VALID.value,
        producer="test",
        producer_version="0.1.0",
        available_at=t0,
    )
    # Observation at t1: stop breached (low dips to 58800 < 59000)
    bar1 = MarketObservation(
        instrument_id=inst,
        interval=horizon,
        open_time=t1 - timedelta(hours=1),
        close_time=t1,
        open=60000.0,
        high=60200.0,
        low=58800.0,
        close=58900.0,
        volume=150.0,
        quality_status=QualityStatus.VALID.value,
        producer="test",
        producer_version="0.1.0",
        available_at=t1,
    )
    mem_session.add_all([bar0, bar1])
    mem_session.commit()

    # Run lifecycle evaluation
    mgr = OpportunityLifecycleManager()
    counts = mgr.evaluate_active_opportunities(mem_session, horizon=horizon)

    assert counts["invalidated"] == 1
    assert opp.lifecycle_status == CandidateLifecycle.INVALIDATED.value
    assert "Price breached stop level" in opp.counter_evidence

    # Check telemetry
    telemetry = opp.score_breakdown.get("invalidation_telemetry")
    assert telemetry is not None
    assert telemetry["status"] == "invalidated"
    assert telemetry["reason"] == "stop_breached"
    assert telemetry["breach_price"] == 58800.0
    assert telemetry["exit_r"] == -1.0
    assert telemetry["mfe_r"] == 0.5  # (60500 - 60000) / 1000 = +0.5R
    assert telemetry["mae_r"] == 1.2  # (60000 - 58800) / 1000 = 1.2R

    # Check OutcomeRecord persisted
    outcomes = mem_session.query(OutcomeRecord).filter_by(opportunity_version_id=opp.id).all()
    assert len(outcomes) == 1
    assert outcomes[0].is_actual is False
    assert outcomes[0].exit_reason == "stop_breached"
    assert outcomes[0].r_multiple == -1.0


def test_short_stop_breach_invalidation(mem_session):
    """Test that a SHORT opportunity is invalidated when price rallies through the stop loss."""
    now = utc_now()
    t0 = now - timedelta(hours=2)
    t1 = now - timedelta(hours=1)
    inst = "bybit:ETH_USDT_USDT:perpetual"
    horizon = "1h"

    # Setup opportunity: Entry 3000, Stop 3100 (100 risk, 2R target 2800)
    opp = OpportunityVersion(
        opportunity_id="ETH_1H_SHORT",
        revision=1,
        playbook_name="range_sfp_reclaim",
        instrument_id=inst,
        direction="short",
        horizon=horizon,
        lifecycle_status=CandidateLifecycle.ACTIVE.value,
        merit_score=82.0,
        confidence_score=0.80,
        trigger_price=3000.0,
        invalidation_price=3100.0,
        thesis="SFP Breakdown",
        as_of=t0,
        available_at=t0,
        quality_status=QualityStatus.VALID.value,
        producer="test",
        producer_version="0.1.0",
        score_breakdown={},
    )
    mem_session.add(opp)

    # Observation at t1: high breaches stop at 3120 > 3100
    bar1 = MarketObservation(
        instrument_id=inst,
        interval=horizon,
        open_time=t1 - timedelta(hours=1),
        close_time=t1,
        open=3010.0,
        high=3120.0,
        low=2980.0,
        close=3110.0,
        volume=200.0,
        quality_status=QualityStatus.VALID.value,
        producer="test",
        producer_version="0.1.0",
        available_at=t1,
    )
    mem_session.add(bar1)
    mem_session.commit()

    mgr = OpportunityLifecycleManager()
    counts = mgr.evaluate_active_opportunities(mem_session, horizon=horizon)

    assert counts["invalidated"] == 1
    assert opp.lifecycle_status == CandidateLifecycle.INVALIDATED.value
    telem = opp.score_breakdown["invalidation_telemetry"]
    assert telem["reason"] == "stop_breached"
    assert telem["breach_price"] == 3120.0


def test_target_2r_hit_expiration(mem_session):
    """Test that an opportunity is transitioned to EXPIRED when price achieves 2R target."""
    now = utc_now()
    t0 = now - timedelta(hours=2)
    t1 = now - timedelta(hours=1)
    inst = "bybit:SOL_USDT_USDT:perpetual"
    horizon = "1h"

    # Entry 150, Stop 140 (10 risk, 2R target = 170)
    opp = OpportunityVersion(
        opportunity_id="SOL_1H_LONG",
        revision=1,
        playbook_name="ict_fair_value_gap",
        instrument_id=inst,
        direction="long",
        horizon=horizon,
        lifecycle_status=CandidateLifecycle.SCORED.value,
        merit_score=90.0,
        confidence_score=0.90,
        trigger_price=150.0,
        invalidation_price=140.0,
        thesis="Bullish FVG retest",
        as_of=t0,
        available_at=t0,
        quality_status=QualityStatus.VALID.value,
        producer="test",
        producer_version="0.1.0",
    )
    mem_session.add(opp)

    # Observation reaching 172 > 170 target
    bar1 = MarketObservation(
        instrument_id=inst,
        interval=horizon,
        open_time=t1 - timedelta(hours=1),
        close_time=t1,
        open=155.0,
        high=172.0,
        low=152.0,
        close=171.0,
        volume=500.0,
        quality_status=QualityStatus.VALID.value,
        producer="test",
        producer_version="0.1.0",
        available_at=t1,
    )
    mem_session.add(bar1)
    mem_session.commit()

    mgr = OpportunityLifecycleManager()
    counts = mgr.evaluate_active_opportunities(mem_session, horizon=horizon)

    assert counts["targets_hit"] == 1
    assert opp.lifecycle_status == CandidateLifecycle.EXPIRED.value
    assert "Target 2R reached" in opp.counter_evidence
    telem = opp.score_breakdown["invalidation_telemetry"]
    assert telem["reason"] == "target_2r_hit"
    assert telem["exit_r"] == 2.0


def test_live_drift_and_executable_rr(mem_session):
    """Test that live price drift and executable R:R dynamically update against current candle close."""
    now = utc_now()
    t0 = now - timedelta(hours=2)
    t1 = now - timedelta(hours=1)
    inst = "bybit:AVAX_USDT_USDT:perpetual"
    horizon = "1h"

    # Entry 20.0, Stop 18.0 (2.0 risk, 2R target = 24.0)
    opp = OpportunityVersion(
        opportunity_id="AVAX_1H_LONG",
        revision=1,
        playbook_name="vcei_squeeze",
        instrument_id=inst,
        direction="long",
        horizon=horizon,
        lifecycle_status=CandidateLifecycle.SCORED.value,
        merit_score=78.0,
        confidence_score=0.75,
        trigger_price=20.0,
        invalidation_price=18.0,
        thesis="Squeeze expansion",
        as_of=t0,
        available_at=t0,
        quality_status=QualityStatus.VALID.value,
        producer="test",
        producer_version="0.1.0",
    )
    mem_session.add(opp)

    # Latest close moved up to 21.0 (+5% drift).
    # Risk distance is now 21.0 - 18.0 = 3.0.
    # Target distance remaining is 24.0 - 21.0 = 3.0.
    # Executable R:R is now 3.0 / 3.0 = 1.0R (vs 2.0R initial).
    bar1 = MarketObservation(
        instrument_id=inst,
        interval=horizon,
        open_time=t1 - timedelta(hours=1),
        close_time=t1,
        open=20.2,
        high=21.5,
        low=20.0,
        close=21.0,
        volume=300.0,
        quality_status=QualityStatus.VALID.value,
        producer="test",
        producer_version="0.1.0",
        available_at=t1,
    )
    mem_session.add(bar1)
    mem_session.commit()

    mgr = OpportunityLifecycleManager()
    mgr.evaluate_active_opportunities(mem_session, horizon=horizon)

    live = opp.score_breakdown.get("live_tracking")
    assert live is not None
    assert live["latest_price"] == 21.0
    assert live["drift_pct"] == 5.0
    assert live["current_rr"] == 1.0


def test_criteria_dissolved_during_assembly(mem_session):
    """Test that an opportunity is invalidated when a newer candle arrives without active signals."""
    now = utc_now()
    t0 = now - timedelta(hours=2)
    t1 = now - timedelta(hours=1)
    inst = "bybit:NEAR_USDT_USDT:perpetual"
    horizon = "1h"

    # Previous active opportunity from t0
    opp = OpportunityVersion(
        opportunity_id="NEAR_1H_LONG",
        revision=1,
        playbook_name="vcei_squeeze",
        instrument_id=inst,
        direction="long",
        horizon=horizon,
        lifecycle_status=CandidateLifecycle.ACTIVE.value,
        merit_score=75.0,
        confidence_score=0.70,
        trigger_price=5.0,
        invalidation_price=4.5,
        thesis="Squeeze breakout",
        as_of=t0,
        available_at=t0,
        quality_status=QualityStatus.VALID.value,
        producer="test",
        producer_version="0.1.0",
    )
    mem_session.add(opp)

    # Newer candle arrived at t1, but no signals emitted for NEAR
    bar1 = MarketObservation(
        instrument_id=inst,
        interval=horizon,
        open_time=t1 - timedelta(hours=1),
        close_time=t1,
        open=4.9,
        high=5.1,
        low=4.8,
        close=4.95,
        volume=100.0,
        quality_status=QualityStatus.VALID.value,
        producer="test",
        producer_version="0.1.0",
        available_at=t1,
    )
    mem_session.add(bar1)
    mem_session.commit()

    assembler = OpportunityAssembler()
    assembler.assemble_opportunities(mem_session, playbook_name="vcei_squeeze", horizon=horizon)

    assert opp.lifecycle_status == CandidateLifecycle.INVALIDATED.value
    assert "Technical confluence criteria dissolved" in opp.counter_evidence
    telem = opp.score_breakdown["invalidation_telemetry"]
    assert telem["reason"] == "criteria_dissolved"

    # Check OutcomeRecord created
    outcome = mem_session.query(OutcomeRecord).filter_by(opportunity_version_id=opp.id).first()
    assert outcome is not None
    assert outcome.exit_reason == "criteria_dissolved"


def test_invalidation_telemetry_logs_query(mem_session):
    """Test get_invalidation_telemetry_logs returns rich structured telemetry data."""
    now = utc_now()
    opp = OpportunityVersion(
        opportunity_id="TEST_TELEMETRY",
        revision=1,
        playbook_name="range_sfp_reclaim",
        instrument_id="bybit:BTC_USDT_USDT:perpetual",
        direction="long",
        horizon="1h",
        lifecycle_status=CandidateLifecycle.INVALIDATED.value,
        merit_score=88.5,
        confidence_score=0.88,
        trigger_price=65000.0,
        invalidation_price=64000.0,
        thesis="SFP Range Low Reclaim",
        counter_evidence="Stop breached",
        as_of=now,
        available_at=now,
        quality_status=QualityStatus.VALID.value,
        producer="test",
        producer_version="0.1.0",
        score_breakdown={
            "confluence_quality": 85.0,
            "volume_confirmation": 90.0,
            "trend_alignment": 95.0,
            "compression_duration": 80.0,
            "confluence_count": 4,
            "invalidation_telemetry": {
                "status": "invalidated",
                "reason": "stop_breached",
                "breach_price": 63950.0,
                "breach_time": now.isoformat(),
                "bars_held": 3,
                "mfe_r": 0.8,
                "mae_r": 1.05,
                "exit_r": -1.0,
            },
        },
    )
    mem_session.add(opp)
    mem_session.commit()

    logs = OpportunityLifecycleManager.get_invalidation_telemetry_logs(mem_session)
    assert len(logs) == 1
    log = logs[0]
    assert log["ticker"] == "BTC/USDT"
    assert log["reason"] == "stop_breached"
    assert log["breach_price"] == 63950.0
    assert log["confluence_quality"] == 85.0
    assert log["trend_alignment"] == 95.0
    assert log["mfe_r"] == 0.8
    assert log["bars_held"] == 3
