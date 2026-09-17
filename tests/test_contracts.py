"""Tests verifying core data contracts, determinism, and envelope fields."""

from datetime import datetime
from src.core.enums import QualityStatus
from src.core.models import MarketObservation
from src.core.time import utc_now
from src.intelligence.features import compute_features_for_bars


def test_shared_envelope_fields(db_session, test_instrument):
    """Verify all envelope fields are present on models."""
    now = utc_now()
    obs = MarketObservation(
        instrument_id=test_instrument.id,
        interval="1h",
        open_time=now,
        close_time=now,
        open=100.0,
        high=105.0,
        low=99.0,
        close=104.0,
        volume=1000.0,
        producer="test",
        producer_version="0.1.0",
        as_of=now,
        available_at=now,
        quality_status=QualityStatus.VALID.value,
    )
    db_session.add(obs)
    db_session.commit()

    saved = db_session.query(MarketObservation).filter_by(id=obs.id).first()
    assert saved is not None
    assert saved.schema_version == 1
    assert saved.producer == "test"
    assert saved.quality_status == QualityStatus.VALID.value
    assert isinstance(saved.created_at, datetime)


def test_feature_computation_determinism(synthetic_bars):
    """
    STAGE 1 ACCEPTANCE CRITERION:
    The same inputs and version reproduce the exact same deterministic outputs.
    """
    inst_id = synthetic_bars[0].instrument_id
    horizon = "1h"

    # Compute run 1
    snap1 = compute_features_for_bars(inst_id, horizon, synthetic_bars)
    # Compute run 2
    snap2 = compute_features_for_bars(inst_id, horizon, synthetic_bars)

    assert snap1 is not None
    assert snap2 is not None

    f1 = snap1.feature_values
    f2 = snap2.feature_values

    # Every indicator must match exactly
    for key in f1:
        assert key in f2
        val1 = f1[key]
        val2 = f2[key]
        if isinstance(val1, float):
            assert abs(val1 - val2) < 1e-9, f"Mismatch in {key}: {val1} != {val2}"
        else:
            assert val1 == val2, f"Mismatch in {key}: {val1} != {val2}"
