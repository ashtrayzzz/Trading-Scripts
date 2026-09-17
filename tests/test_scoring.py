"""Tests for opportunity scoring invariants and missing input handling."""

from src.core.enums import SignalDirection
from src.core.models import FeatureSnapshot, ModuleSignal
from src.core.time import utc_now
from src.opportunities.scoring import OpportunityScorer


def test_missing_features_yields_unscored():
    """
    ARCHITECTURE REQUIREMENT:
    Missing required inputs produce an unscored candidate.
    """
    scorer = OpportunityScorer()
    sig = ModuleSignal(
        module_name="test",
        setup_name="breakout",
        instrument_id="BTC",
        direction=SignalDirection.LONG.value,
        horizon="1h",
        trigger_price=50000.0,
        invalidation_price=49000.0,
        confidence=0.8,
        producer="test",
        producer_version="0.1.0",
        as_of=utc_now(),
        available_at=utc_now(),
    )

    merit, conf, breakdown, missing = scorer.score_candidate(
        signals=[sig],
        features=None,  # Missing required features!
        playbook_name="test_playbook",
    )

    assert merit is None
    assert "feature_snapshot" in missing
    assert breakdown.get("error") == "missing_features"


def test_missing_optional_input_marked_incomplete():
    """
    ARCHITECTURE REQUIREMENT:
    Missing optional inputs produce an explicit incomplete marker;
    never silently redistribute their weights or treat as neutral.
    """
    scorer = OpportunityScorer()
    sig = ModuleSignal(
        module_name="test",
        setup_name="breakout",
        instrument_id="BTC",
        direction=SignalDirection.LONG.value,
        horizon="1h",
        trigger_price=50000.0,
        invalidation_price=49000.0,
        confidence=0.8,
        producer="test",
        producer_version="0.1.0",
        as_of=utc_now(),
        available_at=utc_now(),
    )

    # Incomplete feature snapshot (missing volume_ratio)
    snap = FeatureSnapshot(
        instrument_id="BTC",
        horizon="1h",
        feature_version="0.1.0",
        feature_values={"structure_trend": "uptrend_hh_hl"},  # missing volume_ratio and bb_width
        source_bar_ids=[],
        producer="test",
        producer_version="0.1.0",
        as_of=utc_now(),
        available_at=utc_now(),
    )

    merit, conf, breakdown, missing = scorer.score_candidate(
        signals=[sig],
        features=snap,
        playbook_name="test_playbook",
    )

    assert merit is None
    assert "volume_ratio" in missing
    assert breakdown["status"] == "incomplete_missing_inputs"


def test_valid_scoring_calculation():
    """Verify standard merit formula with penalties."""
    scorer = OpportunityScorer()
    sig = ModuleSignal(
        module_name="test",
        setup_name="breakout",
        instrument_id="BTC",
        direction=SignalDirection.LONG.value,
        horizon="1h",
        trigger_price=50000.0,
        invalidation_price=49000.0,
        confidence=0.85,
        producer="test",
        producer_version="0.1.0",
        as_of=utc_now(),
        available_at=utc_now(),
    )

    snap = FeatureSnapshot(
        instrument_id="BTC",
        horizon="1h",
        feature_version="0.1.0",
        feature_values={
            "volume_ratio": 2.2,
            "structure_trend": "uptrend_hh_hl",
            "bb_width_pct_rank": 15.0,
            "in_squeeze": True,
        },
        source_bar_ids=[],
        producer="test",
        producer_version="0.1.0",
        as_of=utc_now(),
        available_at=utc_now(),
    )

    merit, conf, breakdown, missing = scorer.score_candidate(
        signals=[sig],
        features=snap,
        playbook_name="test_playbook",
    )

    assert merit is not None
    assert 70.0 <= merit <= 100.0
    assert conf >= 0.8
    assert len(missing) == 0
