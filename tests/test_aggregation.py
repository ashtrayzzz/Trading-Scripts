"""Tests for opportunity deduplication and ticker-level aggregation."""

from datetime import datetime, timedelta
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.core.enums import CandidateLifecycle, QualityStatus, SignalDirection
from src.core.models import Base, FeatureSnapshot, ModuleSignal, OpportunityVersion
from src.core.time import utc_now
from src.opportunities.aggregation import (
    aggregate_opportunities_by_ticker,
    get_latest_active_opportunities,
)
from src.opportunities.assembly import OpportunityAssembler
from src.opportunities.scoring import OpportunityScorer


@pytest.fixture
def mem_session():
    """In-memory SQLite session for isolated aggregation tests."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def test_get_latest_active_opportunities_deduplication(mem_session):
    """
    Test that queries for opportunities return only the latest revision
    and exclude superseded revisions.
    """
    now = utc_now()
    inst = "bybit:DOGE_USDT_USDT:perpetual"

    # Rev 1
    opp_v1 = OpportunityVersion(
        opportunity_id="DOGE_15M_LONG",
        revision=1,
        playbook_name="vcei_squeeze",
        instrument_id=inst,
        direction="long",
        horizon="15m",
        lifecycle_status=getattr(CandidateLifecycle, "SUPERSEDED", "superseded"),
        confidence_score=0.8,
        thesis="Rev 1 thesis",
        trigger_price=0.1800,
        invalidation_price=0.1750,
        as_of=now - timedelta(minutes=30),
        available_at=now - timedelta(minutes=30),
        quality_status=QualityStatus.VALID.value,
        producer="test",
        producer_version="0.1.0",
    )
    # Rev 2
    opp_v2 = OpportunityVersion(
        opportunity_id="DOGE_15M_LONG",
        revision=2,
        playbook_name="vcei_squeeze",
        instrument_id=inst,
        direction="long",
        horizon="15m",
        lifecycle_status=CandidateLifecycle.SCORED.value,
        merit_score=86.7,
        confidence_score=0.9,
        thesis="Rev 2 thesis with tighter stop",
        trigger_price=0.1850,
        invalidation_price=0.1795,
        as_of=now,
        available_at=now,
        quality_status=QualityStatus.VALID.value,
        producer="test",
        producer_version="0.1.0",
    )

    mem_session.add_all([opp_v1, opp_v2])
    mem_session.commit()

    latest = get_latest_active_opportunities(mem_session)
    assert len(latest) == 1
    assert latest[0].revision == 2
    assert latest[0].merit_score == 86.7
    assert latest[0].trigger_price == 0.1850


def test_aggregate_opportunities_by_ticker(mem_session):
    """
    Test that multiple horizon setups for a single ticker are grouped into
    a unified aggregate with multi-timeframe confluence and Where/Why/How details.
    """
    now = utc_now()
    inst = "bybit:DOGE_USDT_USDT:perpetual"

    # 15m Squeeze
    opp_15m = OpportunityVersion(
        opportunity_id="DOGE_15M",
        revision=1,
        playbook_name="vcei_squeeze_pro",
        instrument_id=inst,
        direction="long",
        horizon="15m",
        lifecycle_status=CandidateLifecycle.SCORED.value,
        merit_score=86.7,
        confidence_score=0.9,
        thesis="15M Volatility squeeze breakout.",
        trigger_price=0.1850,
        invalidation_price=0.1795,
        as_of=now,
        available_at=now,
        quality_status=QualityStatus.VALID.value,
        producer="test",
        producer_version="0.1.0",
    )
    # 1h Trend Cloud
    opp_1h = OpportunityVersion(
        opportunity_id="DOGE_1H",
        revision=1,
        playbook_name="tcmb_trend_cloud",
        instrument_id=inst,
        direction="long",
        horizon="1h",
        lifecycle_status=CandidateLifecycle.SCORED.value,
        merit_score=82.0,
        confidence_score=0.85,
        thesis="1H Trend cloud bullish continuation.",
        trigger_price=0.1860,
        invalidation_price=0.1750,
        as_of=now,
        available_at=now,
        quality_status=QualityStatus.VALID.value,
        producer="test",
        producer_version="0.1.0",
    )

    mem_session.add_all([opp_15m, opp_1h])
    mem_session.commit()

    all_opps = [opp_15m, opp_1h]
    aggregates = aggregate_opportunities_by_ticker(all_opps)

    assert len(aggregates) == 1
    agg = aggregates[0]
    assert agg.ticker == "DOGE/USDT"
    assert agg.peak_merit_score == 86.7
    assert agg.horizon_count == 2
    assert "15m" in agg.active_horizons
    assert "1h" in agg.active_horizons
    assert "2 Timeframes Aligned" in agg.directional_confluence
    assert agg.primary_direction == "long"

    # Verify triggered signals structure
    assert len(agg.triggered_signals) >= 1
    # Check Where/Why/How
    vcei_sig = next((s for s in agg.triggered_signals if s.system_acronym == "VCEI"), None)
    assert vcei_sig is not None
    assert "15m" in vcei_sig.timeframes
    detail_15m = vcei_sig.timeframe_details["15m"]
    assert "Trigger Entry: \\$0.1850" in detail_15m.where_summary
    assert detail_15m.target_2r == pytest.approx(0.1850 + 2 * (0.1850 - 0.1795), 0.0001)
    assert "Enter long on candle close" in detail_15m.how_summary


def test_assembly_skips_identical_candles(mem_session):
    """
    Test that re-running OpportunityAssembler on the exact same closed bar
    does not spawn duplicate revisions.
    """
    now = utc_now()
    inst = "bybit:DOGE_USDT_USDT:perpetual"

    # Create dummy signal and feature snapshot
    snap = FeatureSnapshot(
        instrument_id=inst,
        horizon="15m",
        feature_values={"squeeze_on": 1, "trend": 1},
        producer="test",
        producer_version="0.1.0",
        as_of=now,
        available_at=now,
    )
    mem_session.add(snap)
    mem_session.flush()

    sig = ModuleSignal(
        module_name="vcei_squeeze",
        setup_name="squeeze_pro",
        instrument_id=inst,
        direction=SignalDirection.LONG.value,
        horizon="15m",
        trigger_price=0.1850,
        invalidation_price=0.1795,
        confidence=0.9,
        feature_snapshot_ids=[str(snap.id)],
        producer="test",
        producer_version="0.1.0",
        as_of=now,
        available_at=now,
    )
    mem_session.add(sig)
    mem_session.commit()

    assembler = OpportunityAssembler()

    # First run: creates rev 1
    run1 = assembler.assemble_opportunities(mem_session, playbook_name="playbook_15m", horizon="15m", as_of=now)
    assert len(run1) == 1
    assert run1[0].revision == 1

    count_after_run1 = mem_session.query(OpportunityVersion).count()
    assert count_after_run1 == 1

    # Second run on same candle: should NOT create rev 2
    run2 = assembler.assemble_opportunities(mem_session, playbook_name="playbook_15m", horizon="15m", as_of=now)
    assert len(run2) == 1
    assert run2[0].revision == 1

    count_after_run2 = mem_session.query(OpportunityVersion).count()
    assert count_after_run2 == 1, "Expected identical scan to not create a duplicate row in the database"


def test_multitimeframe_conflict_reconciliation_htf_priority(mem_session):
    """
    Test that conflicting multi-timeframe signals (e.g. Daily Long vs 15m Short)
    are reconciled via Higher-Timeframe Seniority and weighted conviction.
    """
    now = utc_now()
    inst = "bybit:DOGE_USDT_USDT:perpetual"

    # Daily Macro Long setup (85.0 Merit)
    opp_1d = OpportunityVersion(
        opportunity_id="DOGE_1D_LONG",
        revision=1,
        playbook_name="nern_lorentzian",
        instrument_id=inst,
        direction="long",
        horizon="1d",
        lifecycle_status=CandidateLifecycle.SCORED.value,
        merit_score=85.0,
        confidence_score=0.9,
        thesis="Macro Daily trend bullish above 200 EMA",
        trigger_price=0.1850,
        invalidation_price=0.1750,
        as_of=now,
        available_at=now,
        quality_status=QualityStatus.VALID.value,
        producer="test",
        producer_version="0.1.0",
    )

    # 15m Counter-Trend Short setup (58.0 Merit)
    opp_15m = OpportunityVersion(
        opportunity_id="DOGE_15M_SHORT",
        revision=1,
        playbook_name="playbook_15m",
        instrument_id=inst,
        direction="short",
        horizon="15m",
        lifecycle_status=CandidateLifecycle.SCORED.value,
        merit_score=58.0,
        confidence_score=0.7,
        thesis="15m momentum divergence pullback",
        trigger_price=0.1840,
        invalidation_price=0.1865,
        as_of=now,
        available_at=now,
        quality_status=QualityStatus.VALID.value,
        producer="test",
        producer_version="0.1.0",
    )

    mem_session.add_all([opp_1d, opp_15m])
    mem_session.commit()

    from src.opportunities.aggregation import aggregate_opportunities_by_ticker

    aggs = aggregate_opportunities_by_ticker([opp_1d, opp_15m])
    assert len(aggs) == 1
    agg = aggs[0]

    # Verification of conflict reconciliation
    assert agg.is_conflicted is True
    assert agg.reconciled_direction == "long"
    assert agg.htf_dominant_direction == "long"
    assert agg.is_counter_trend_warning is True
    assert "HTF Seniority" in agg.directional_confluence
    assert "Pullback Caution" in agg.directional_confluence
    assert "BULLISH" in agg.reconciliation_summary
    assert agg.weighted_long_conviction > agg.weighted_short_conviction

    # Test noise filtering (exclude 15m)
    aggs_filtered = aggregate_opportunities_by_ticker([opp_1d, opp_15m], exclude_sub_hourly=True)
    assert len(aggs_filtered) == 1
    assert aggs_filtered[0].is_conflicted is False
    assert aggs_filtered[0].active_horizons == ["1d"]


def test_where_why_how_katex_escaping_and_clean_ticker():
    r"""
    Test that prices in Where/Why/How are escaped with \$ so Streamlit
    KaTeX math parser doesn't strip spaces or italicize text.
    """
    now = utc_now()
    from src.opportunities.aggregation import build_where_why_how

    opp = OpportunityVersion(
        opportunity_id="TEST_15M",
        revision=1,
        playbook_name="volume_profile_breakout",
        instrument_id="bybit:DOGE_USDT_USDT:perpetual",
        direction="long",
        horizon="15m",
        lifecycle_status=CandidateLifecycle.SCORED.value,
        merit_score=86.7,
        confidence_score=0.88,
        thesis="LONG candidate on bybit:DOGE_USDT_USDT:perpetual (15m) triggered by volume_profile_breakout. Entry trigger: 0.0811, Invalidation: 0.0795.",
        trigger_price=0.0811,
        invalidation_price=0.0795,
        as_of=now,
        available_at=now,
        quality_status=QualityStatus.VALID.value,
        producer="test",
        producer_version="0.1.0",
    )

    where_s, why_s, how_s, t2, t3, r_dist, r_pct = build_where_why_how(opp)

    # Verify KaTeX dollar signs are escaped
    assert r"\$" in where_s
    assert r"\$" in how_s
    # Verify no raw unescaped single $ remains in how_s
    assert "0.0811ifprice" not in how_s
    assert r"\$0.0811 if price structure holds" in how_s
    assert r"\$0.0795" in how_s

    # Verify why_summary does not have raw connector string
    assert "bybit:DOGE_USDT_USDT:perpetual" not in why_s
    assert "DOGE/USDT" in why_s
    assert "Volume Profile Breakout" in why_s
    assert "0795." not in why_s


def test_risk_geometry_invariants_and_neutral_filtering(mem_session):
    """Verify that zero-risk or neutral opportunities are rejected from the pipeline."""
    now = utc_now()
    from src.opportunities.aggregation import (
        build_where_why_how,
        get_latest_active_opportunities,
        aggregate_opportunities_by_ticker,
    )

    # 1. Zero-risk opportunity (entry == stop)
    opp_zero_risk = OpportunityVersion(
        opportunity_id="ZERO_RISK_SHOP",
        revision=1,
        playbook_name="broad_ev_discovery",
        instrument_id="yahoo:SHOP.TO:equity",
        direction="long",
        horizon="1h",
        lifecycle_status=CandidateLifecycle.SCORED.value,
        merit_score=78.0,
        confidence_score=0.75,
        thesis="Zero risk anomaly test.",
        trigger_price=181.84,
        invalidation_price=181.84,  # Identical!
        as_of=now,
        available_at=now,
        quality_status=QualityStatus.VALID.value,
        producer="test",
        producer_version="0.1.0",
    )

    # 2. Neutral context opportunity
    opp_neutral = OpportunityVersion(
        opportunity_id="NEUTRAL_OPP",
        revision=1,
        playbook_name="broad_ev_discovery",
        instrument_id="yahoo:SHOP.TO:equity",
        direction="neutral_context",
        horizon="1h",
        lifecycle_status=CandidateLifecycle.SCORED.value,
        merit_score=60.0,
        confidence_score=0.7,
        thesis="Neutral context anomaly test.",
        trigger_price=181.84,
        invalidation_price=180.00,
        as_of=now,
        available_at=now,
        quality_status=QualityStatus.VALID.value,
        producer="test",
        producer_version="0.1.0",
    )

    # 3. Valid directional opportunity
    opp_valid = OpportunityVersion(
        opportunity_id="VALID_LONG_SHOP",
        revision=1,
        playbook_name="compression_breakout",
        instrument_id="yahoo:SHOP.TO:equity",
        direction="long",
        horizon="1h",
        lifecycle_status=CandidateLifecycle.SCORED.value,
        merit_score=85.0,
        confidence_score=0.9,
        thesis="Valid breakout test.",
        trigger_price=181.84,
        invalidation_price=178.50,
        as_of=now,
        available_at=now,
        quality_status=QualityStatus.VALID.value,
        producer="test",
        producer_version="0.1.0",
    )

    # Test build_where_why_how guard against zero risk
    where_z, _, _, t2_z, t3_z, r_dist_z, r_pct_z = build_where_why_how(opp_zero_risk)
    assert "Invalid Risk Geometry" in where_z
    assert t2_z is None
    assert t3_z is None
    assert r_dist_z is None
    assert r_pct_z is None

    # Test get_latest_active_opportunities filtering
    mem_session.add_all([opp_zero_risk, opp_neutral, opp_valid])
    mem_session.commit()

    active = get_latest_active_opportunities(mem_session)
    active_ids = [o.opportunity_id for o in active]
    assert "VALID_LONG_SHOP" in active_ids
    assert "ZERO_RISK_SHOP" not in active_ids
    assert "NEUTRAL_OPP" not in active_ids

    # Test aggregate_opportunities_by_ticker filtering
    aggs = aggregate_opportunities_by_ticker([opp_zero_risk, opp_neutral, opp_valid])
    assert len(aggs) == 1
    assert aggs[0].primary_opportunity.opportunity_id == "VALID_LONG_SHOP"
    assert aggs[0].trigger_price == 181.84
    assert aggs[0].invalidation_price == 178.50
    assert aggs[0].target_2r is not None
    assert aggs[0].target_2r > aggs[0].trigger_price


def test_htf_priority_and_noise_filtering(mem_session):
    """
    Verify that higher timeframe (Daily, 4h, 1w) setups are prioritized
    as the primary opportunity anchor over noisy lower timeframes (15m, 1h),
    and that htf_only filters out sub-4h noise.
    """
    now = utc_now()
    inst = "yahoo:SPY:etf"

    # 15m noisy scalp with high nominal merit
    opp_15m = OpportunityVersion(
        opportunity_id="SPY_15M_SCALP",
        revision=1,
        playbook_name="vcei_squeeze",
        instrument_id=inst,
        direction="long",
        horizon="15m",
        lifecycle_status=CandidateLifecycle.SCORED.value,
        merit_score=92.0,
        confidence_score=0.9,
        thesis="15m micro momentum scalp",
        trigger_price=590.0,
        invalidation_price=588.5,
        as_of=now,
        available_at=now,
        quality_status=QualityStatus.VALID.value,
        producer="test",
        producer_version="0.1.0",
    )

    # 1d high-conviction macro/swing setup with slightly lower nominal merit
    opp_1d = OpportunityVersion(
        opportunity_id="SPY_1D_SWING",
        revision=1,
        playbook_name="shla_liquidity_sweep",
        instrument_id=inst,
        direction="long",
        horizon="1d",
        lifecycle_status=CandidateLifecycle.SCORED.value,
        merit_score=80.0,
        confidence_score=0.85,
        thesis="Daily structural liquidity sweep at major demand zone",
        trigger_price=585.0,
        invalidation_price=578.0,
        as_of=now,
        available_at=now,
        quality_status=QualityStatus.VALID.value,
        producer="test",
        producer_version="0.1.0",
    )

    aggs = aggregate_opportunities_by_ticker([opp_15m, opp_1d])
    assert len(aggs) == 1
    # 1d must be chosen as primary opportunity anchor over 15m noise
    assert aggs[0].primary_opportunity.opportunity_id == "SPY_1D_SWING"
    assert aggs[0].trigger_price == 585.0
    assert aggs[0].peak_merit_score == 92.0

    # Test htf_only filter
    htf_aggs = aggregate_opportunities_by_ticker([opp_15m, opp_1d], htf_only=True)
    assert len(htf_aggs) == 1
    assert "15m" not in htf_aggs[0].active_horizons
    assert htf_aggs[0].active_horizons == ["1d"]



