"""Tests for connector invariants: closed-bar enforcement and idempotent ingestion."""

from datetime import timedelta
from src.connectors.base import BaseConnector
from src.core.enums import QualityStatus
from src.core.models import MarketObservation
from src.core.time import is_bar_closed, utc_now


class DummyConnector(BaseConnector):
    def fetch_instruments(self):
        return []
    def fetch_bars(self, instrument, interval, limit=100, since=None):
        return []


def test_closed_bar_filter():
    """
    PRIORITY VALIDATION SCENARIO:
    Partial candle or late bar must not produce closed-bar signals.
    """
    now = utc_now()

    # Bar that opened 5 minutes ago on a 1-hour timeframe (currently forming!)
    recent_open = now - timedelta(minutes=5)
    assert not is_bar_closed(recent_open, "1h", as_of=now)

    # Bar that opened 2 hours ago on a 1-hour timeframe (fully closed!)
    closed_open = now - timedelta(hours=2)
    assert is_bar_closed(closed_open, "1h", as_of=now)


def test_ingestion_idempotency(db_session, test_instrument):
    """
    STAGE 1 ACCEPTANCE CRITERION:
    Re-importing data or fills is idempotent.
    """
    connector = DummyConnector(venue_name="test_venue")
    now = utc_now() - timedelta(hours=3)
    close_t = now + timedelta(hours=1)

    obs = MarketObservation(
        instrument_id=test_instrument.id,
        interval="1h",
        open_time=now,
        close_time=close_t,
        open=100.0,
        high=105.0,
        low=99.0,
        close=104.0,
        volume=500.0,
        content_hash="unique_hash_1",
        producer="test",
        producer_version="0.1.0",
        as_of=close_t,
        available_at=utc_now(),
        quality_status=QualityStatus.VALID.value,
    )

    # First save: should persist 1 record
    saved1 = connector.save_bars(db_session, [obs])
    assert saved1 == 1

    # Second save of identical bar: should skip and save 0 new records
    saved2 = connector.save_bars(db_session, [obs])
    assert saved2 == 0

    count = db_session.query(MarketObservation).filter_by(instrument_id=test_instrument.id).count()
    assert count == 1
