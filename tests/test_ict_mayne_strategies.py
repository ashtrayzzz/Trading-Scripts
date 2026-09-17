"""Unit tests for ICT Fair Value Gaps (IFVG) and Trader Mayne Dealing Range SFP (DREF) strategies."""

from datetime import datetime, timedelta
import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.core.enums import CandidateLifecycle, PlaybookMode, QualityStatus, SignalDirection
from src.core.models import Base, FeatureSnapshot, MarketObservation, ModuleSignal, OpportunityVersion
from src.core.time import utc_now
from src.intelligence.base import ModuleContext
from src.intelligence.features import (
    compute_features_for_bars,
    detect_dealing_range_sfp,
    detect_fair_value_gaps,
)
from src.intelligence.llm_scaffold import STRATEGY_DEEP_DIVES
from src.intelligence.technical import TechnicalScanner
from src.opportunities.aggregation import (
    build_where_why_how,
    extract_all_systems,
    parse_system_acronym,
)


def test_fvg_detection_bullish_and_bearish():
    """Verify 3-candle Fair Value Gap detection, Consequent Encroachment (50% CE), and retest reactions."""
    # 1. Bullish FVG (BISI)
    # Candle 0: High 100, Low 95
    # Candle 1: Displacement up
    # Candle 2: High 115, Low 105 (Gap between 100 and 105, CE = 102.5)
    # Candle 3: Retest dipping into gap to 101, closing at 104
    data_bullish = [
        {"open": 96.0, "high": 100.0, "low": 95.0, "close": 98.0, "volume": 1000.0},
        {"open": 99.0, "high": 110.0, "low": 99.0, "close": 109.0, "volume": 2500.0},
        {"open": 110.0, "high": 115.0, "low": 105.0, "close": 114.0, "volume": 1200.0},
        {"open": 102.0, "high": 108.0, "low": 101.0, "close": 104.0, "volume": 1500.0},
    ]
    df_bull = pd.DataFrame(data_bullish)
    res_bull = detect_fair_value_gaps(df_bull, atr=2.5)

    assert res_bull["fvg_bullish_test"] is True
    assert res_bull["fvg_bullish_bottom"] == 100.0
    assert res_bull["fvg_bullish_top"] == 105.0
    assert res_bull["fvg_bullish_ce"] == 102.5

    # 2. Bearish FVG (SIBI)
    # Candle 0: High 105, Low 100
    # Candle 1: Displacement down
    # Candle 2: High 95, Low 85 (Gap between 95 and 100, CE = 97.5)
    # Candle 3: Retest rising into gap to 98, closing down at 93
    data_bearish = [
        {"open": 104.0, "high": 105.0, "low": 100.0, "close": 102.0, "volume": 1000.0},
        {"open": 101.0, "high": 101.0, "low": 90.0, "close": 91.0, "volume": 2500.0},
        {"open": 90.0, "high": 95.0, "low": 85.0, "close": 86.0, "volume": 1200.0},
        {"open": 96.0, "high": 98.0, "low": 92.0, "close": 93.0, "volume": 1500.0},
    ]
    df_bear = pd.DataFrame(data_bearish)
    res_bear = detect_fair_value_gaps(df_bear, atr=2.5)

    assert res_bear["fvg_bearish_test"] is True
    assert res_bear["fvg_bearish_bottom"] == 95.0
    assert res_bear["fvg_bearish_top"] == 100.0
    assert res_bear["fvg_bearish_ce"] == 97.5


def test_dealing_range_sfp_and_eq_targets():
    """Verify Trader Mayne Dealing Range, SFP sweeps, 50% EQ targets, and invalidation rules."""
    # Build 20 prior bars forming a range between High 110.0 and Low 90.0 (EQ = 100.0)
    data = []
    for i in range(20):
        data.append({
            "open": 98.0 + (i % 3),
            "high": 105.0 if i not in (5, 12) else 110.0,
            "low": 95.0 if i not in (8, 15) else 90.0,
            "close": 100.0,
            "volume": 1000.0,
        })

    # Test A: Bullish SFP Reclaim (wicks to 88 < 90, closes at 92 > 90 on 1.4x volume)
    bull_bar = {"open": 89.0, "high": 93.0, "low": 88.0, "close": 92.0, "volume": 1400.0}
    df_bull = pd.DataFrame(data + [bull_bar])

    res_bull = detect_dealing_range_sfp(df_bull, volume_ratio=1.40, atr=2.0)
    assert res_bull["mayne_sfp_bullish"] is True
    assert res_bull["dealing_range_high"] == 110.0
    assert res_bull["dealing_range_low"] == 90.0
    assert res_bull["dealing_range_eq"] == 100.0
    assert res_bull["mayne_sfp_target_eq"] == 100.0
    assert res_bull["mayne_sfp_target_terminal"] == 110.0
    assert pytest.approx(res_bull["mayne_sfp_invalidation"], 0.01) == 88.0 - (0.1 * 2.0)

    # Test B: Bearish SFP Reclaim (wicks to 112 > 110, closes at 108 < 110 on 1.4x volume)
    bear_bar = {"open": 111.0, "high": 112.0, "low": 107.0, "close": 108.0, "volume": 1400.0}
    df_bear = pd.DataFrame(data + [bear_bar])

    res_bear = detect_dealing_range_sfp(df_bear, volume_ratio=1.40, atr=2.0)
    assert res_bear["mayne_sfp_bearish"] is True
    assert res_bear["dealing_range_high"] == 110.0
    assert res_bear["dealing_range_low"] == 90.0
    assert res_bear["dealing_range_eq"] == 100.0
    assert res_bear["mayne_sfp_target_eq"] == 100.0
    assert res_bear["mayne_sfp_target_terminal"] == 90.0
    assert pytest.approx(res_bear["mayne_sfp_invalidation"], 0.01) == 112.0 + (0.1 * 2.0)


def test_technical_scanner_named_setups():
    """Verify TechnicalScanner detects and emits fvg_retest_continuation and dealing_range_sfp_reclaim."""
    scanner = TechnicalScanner(mode=PlaybookMode.FILTERED)

    features_fvg = {
        "latest_close": 104.0,
        "latest_open": 102.0,
        "latest_high": 108.0,
        "latest_low": 101.0,
        "atr_14": 2.0,
        "fvg_bullish_test": True,
        "fvg_bullish_bottom": 100.0,
        "fvg_bullish_top": 105.0,
        "fvg_bullish_ce": 102.5,
    }
    setups_fvg = scanner._detect_named_setups(features_fvg, "1h")
    fvg_hits = [s for s in setups_fvg if s["setup_name"] == "fvg_retest_continuation"]
    assert len(fvg_hits) == 1
    assert fvg_hits[0]["direction"] == SignalDirection.LONG.value
    assert fvg_hits[0]["conditions"]["fvg_ce"] == 102.5

    features_mayne = {
        "latest_close": 92.0,
        "latest_open": 89.0,
        "latest_high": 93.0,
        "latest_low": 88.0,
        "atr_14": 2.0,
        "mayne_sfp_bullish": True,
        "dealing_range_high": 110.0,
        "dealing_range_low": 90.0,
        "dealing_range_eq": 100.0,
        "mayne_sfp_invalidation": 87.8,
        "volume_ratio": 1.4,
    }
    setups_mayne = scanner._detect_named_setups(features_mayne, "4h")
    mayne_hits = [s for s in setups_mayne if s["setup_name"] == "dealing_range_sfp_reclaim"]
    assert len(mayne_hits) == 1
    assert mayne_hits[0]["direction"] == SignalDirection.LONG.value
    assert mayne_hits[0]["conditions"]["eq_target"] == 100.0
    assert mayne_hits[0]["invalidation_price"] == 87.8


def test_strategy_deep_dives_and_aggregation():
    """Verify IFVG and DREF are mapped with institutional acronyms and Where/Why/How narratives."""
    # Check STRATEGY_DEEP_DIVES metadata
    for k in ["IFVG", "DREF"]:
        assert k in STRATEGY_DEEP_DIVES
        meta = STRATEGY_DEEP_DIVES[k]
        assert "purposed_name" in meta
        assert "how_it_works" in meta
        assert "why_it_works" in meta
        assert len(meta["core_assumptions"]) >= 2
        assert "limitations" in meta
        assert "tailored_adjustments" in meta

    # Check parse_system_acronym
    sys_fvg, acr_fvg = parse_system_acronym("fvg_retest_continuation")
    assert acr_fvg == "IFVG"
    assert sys_fvg == "Fair Value Gaps"

    sys_mayne, acr_mayne = parse_system_acronym("dealing_range_sfp_reclaim")
    assert acr_mayne == "DREF"
    assert "Dealing Range" in sys_mayne

    now = utc_now()
    # Test Mayne DREF Where/Why/How narrative
    opp_mayne = OpportunityVersion(
        opportunity_id="MAYNE_DOGE_4H",
        revision=1,
        playbook_name="dealing_range_sfp_reclaim",
        instrument_id="bybit:DOGE_USDT_USDT:perpetual",
        direction="long",
        horizon="4h",
        lifecycle_status=CandidateLifecycle.SCORED.value,
        merit_score=88.5,
        confidence_score=0.90,
        thesis="LONG setup on bybit:DOGE_USDT_USDT:perpetual (4h) triggered by dealing_range_sfp_reclaim.",
        trigger_price=0.1800,
        invalidation_price=0.1750,
        as_of=now,
        available_at=now,
        quality_status=QualityStatus.VALID.value,
        producer="test",
        producer_version="0.1.0",
    )
    where_m, why_m, how_m, t2, t3, r_dist, r_pct = build_where_why_how(opp_mayne)
    assert r"\$" in where_m
    assert r"\$" in how_m
    assert "50% EQ" in where_m or "Equilibrium" in how_m
    assert "DOGE/USDT" in why_m

    # Test FVG IFVG Where/Why/How narrative
    opp_fvg = OpportunityVersion(
        opportunity_id="FVG_SOL_1H",
        revision=1,
        playbook_name="fvg_retest_continuation",
        instrument_id="bybit:SOL_USDT_USDT:perpetual",
        direction="long",
        horizon="1h",
        lifecycle_status=CandidateLifecycle.SCORED.value,
        merit_score=85.0,
        confidence_score=0.88,
        thesis="LONG setup on bybit:SOL_USDT_USDT:perpetual (1h) triggered by fvg_retest_continuation.",
        trigger_price=145.0,
        invalidation_price=142.0,
        as_of=now,
        available_at=now,
        quality_status=QualityStatus.VALID.value,
        producer="test",
        producer_version="0.1.0",
    )
    where_f, why_f, how_f, t2, t3, r_dist, r_pct = build_where_why_how(opp_fvg)
    assert r"\$" in where_f
    assert r"\$" in how_f
    assert "Fair Value Gap" in how_f or "FVG" in where_f
    assert "Consequent Encroachment" in where_f or "Consequent Encroachment" in how_f


def test_ict_and_mayne_opportunity_scoring_merit():
    """Verify OpportunityScorer evaluates ICT and Mayne setups with appropriate merit calibration."""
    from src.opportunities.scoring import OpportunityScorer
    scorer = OpportunityScorer()
    now = utc_now()

    # 1. Dealing Range SFP Reclaim signal
    sfp_sig = ModuleSignal(
        module_name="technical_scanner",
        setup_name="dealing_range_sfp_reclaim",
        instrument_id="bybit:DOGE_USDT_USDT:perpetual",
        direction="long",
        horizon="4h",
        trigger_price=0.1800,
        invalidation_price=0.1750,
        confidence=0.89,
        conditions={"sfp_type": "bullish_range_low_reclaim"},
        producer="technical_scanner",
        producer_version="0.1.0",
        as_of=now,
        available_at=now,
    )
    snap_sfp = FeatureSnapshot(
        instrument_id="bybit:DOGE_USDT_USDT:perpetual",
        horizon="4h",
        feature_version="0.2.0",
        feature_values={
            "volume_ratio": 1.4,
            "structure_trend": "neutral",
            "dealing_range_high": 0.2000,
            "dealing_range_low": 0.1780,
            "dealing_range_eq": 0.1890,
            "mayne_sfp_bullish": True,
            "bb_width_pct_rank": 35.0,
            "in_squeeze": False,
        },
        source_bar_ids=[],
        producer="feature_pipeline",
        producer_version="0.2.0",
        as_of=now,
        available_at=now,
    )

    merit_sfp, conf_sfp, breakdown_sfp, missing_sfp = scorer.score_candidate(
        signals=[sfp_sig],
        features=snap_sfp,
        playbook_name="playbook_4h",
    )
    assert len(missing_sfp) == 0
    assert merit_sfp is not None and merit_sfp >= 80.0
    assert "dealing_range_sfp_reclaim" in breakdown_sfp["signals"]
    assert breakdown_sfp["penalties"] == 0.0  # Zero counter-trend penalty on SFP reclaim

    # 2. FVG Retest Continuation signal
    fvg_sig = ModuleSignal(
        module_name="technical_scanner",
        setup_name="fvg_retest_continuation",
        instrument_id="bybit:SOL_USDT_USDT:perpetual",
        direction="long",
        horizon="1h",
        trigger_price=145.0,
        invalidation_price=142.0,
        confidence=0.86,
        conditions={"fvg_type": "bullish_bisi_retest"},
        producer="technical_scanner",
        producer_version="0.1.0",
        as_of=now,
        available_at=now,
    )
    snap_fvg = FeatureSnapshot(
        instrument_id="bybit:SOL_USDT_USDT:perpetual",
        horizon="1h",
        feature_version="0.2.0",
        feature_values={
            "volume_ratio": 1.5,
            "structure_trend": "uptrend_hh_hl",
            "fvg_bullish_test": True,
            "fvg_bullish_bottom": 142.5,
            "fvg_bullish_top": 146.0,
            "fvg_bullish_ce": 144.25,
            "bb_width_pct_rank": 20.0,
            "in_squeeze": True,
        },
        source_bar_ids=[],
        producer="feature_pipeline",
        producer_version="0.2.0",
        as_of=now,
        available_at=now,
    )

    merit_fvg, conf_fvg, breakdown_fvg, missing_fvg = scorer.score_candidate(
        signals=[fvg_sig],
        features=snap_fvg,
        playbook_name="playbook_1h",
    )
    assert len(missing_fvg) == 0
    assert merit_fvg is not None and merit_fvg >= 85.0
    assert "fvg_retest_continuation" in breakdown_fvg["signals"]
    assert breakdown_fvg["trend_alignment"] == 100.0  # FVG in direction of uptrend

