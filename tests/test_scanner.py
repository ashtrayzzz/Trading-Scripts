"""Tests for technical scanner and broad positive-EV discovery."""

from src.core.enums import PlaybookMode
from src.core.time import utc_now
from src.intelligence.base import ModuleContext
from src.intelligence.technical import TechnicalScanner


def test_scanner_breakout_detection(db_session, synthetic_bars, test_instrument):
    """Verify that the scanner detects compression and subsequent breakout."""
    scanner = TechnicalScanner(mode=PlaybookMode.FILTERED)
    ctx = ModuleContext(
        instrument_ids=[test_instrument.id],
        horizon="1h",
        decision_cutoff=utc_now(),
        run_id="test_run_1",
    )

    result = scanner.evaluate(ctx, db_session)
    assert result.status == "success"
    assert len(result.signals) > 0

    # Look for compression_breakout or range_break
    setup_names = [s.setup_name for s in result.signals]
    assert any("break" in name or "discovery" in name for name in setup_names)

    # Invariants on emitted signals
    for sig in result.signals:
        assert sig.trigger_price is not None
        assert sig.invalidation_price is not None
        assert sig.trigger_price > 0
        assert sig.invalidation_price > 0
        assert sig.confidence >= 0.5


def test_broad_discovery_mode(db_session, synthetic_bars, test_instrument):
    """Verify broad positive-EV scanner flags compression and volume surges."""
    scanner = TechnicalScanner(mode=PlaybookMode.DISCOVERY)
    ctx = ModuleContext(
        instrument_ids=[test_instrument.id],
        horizon="1h",
        decision_cutoff=utc_now(),
        run_id="test_discovery_run",
    )

    result = scanner.evaluate(ctx, db_session)
    discovery_sigs = [s for s in result.signals if s.setup_name == "broad_ev_discovery"]
    assert len(discovery_sigs) > 0
    anomalies = discovery_sigs[0].conditions.get("anomalies", [])
    assert len(anomalies) > 0
