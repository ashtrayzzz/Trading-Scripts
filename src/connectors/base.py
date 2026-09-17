"""Abstract base connector interface for market feeds."""

from abc import ABC, abstractmethod
import hashlib
from datetime import datetime
from typing import Sequence

from sqlalchemy.orm import Session

from src.core.enums import QualityStatus, TimeInterval
from src.core.identity import Instrument
from src.core.models import InstrumentRecord, MarketObservation
from src.core.time import compute_bar_close_time, is_bar_closed, utc_now


def calculate_bar_hash(instrument_id: str, interval: str, open_time_iso: str, o: float, h: float, l: float, c: float, v: float) -> str:
    """Compute sha256 checksum for bar contents to ensure idempotency and integrity."""
    raw = f"{instrument_id}:{interval}:{open_time_iso}:{o:.8f}:{h:.8f}:{l:.8f}:{c:.8f}:{v:.8f}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class BaseConnector(ABC):
    """
    Abstract connector interface.
    
    Invariants:
    - Only closed bars are returned and persisted.
    - Repeated ingestion of the same bar is idempotent.
    - All timestamps are UTC.
    """

    def __init__(self, venue_name: str):
        self.venue_name = venue_name

    @abstractmethod
    def fetch_instruments(self) -> list[Instrument]:
        """Fetch tradable instruments available from this source."""
        pass

    @abstractmethod
    def fetch_bars(
        self,
        instrument: Instrument,
        interval: str | TimeInterval,
        limit: int = 100,
        since: datetime | None = None,
    ) -> list[MarketObservation]:
        """
        Fetch OHLCV closed bars. Unclosed bars MUST be filtered out.
        """
        pass

    def save_instruments(self, session: Session, instruments: Sequence[Instrument]) -> int:
        """Upsert instruments into canonical store."""
        count = 0
        now = utc_now()
        for inst in instruments:
            existing = session.query(InstrumentRecord).filter_by(id=inst.instrument_id).first()
            if existing:
                existing.is_active = inst.is_active
                existing.contract_multiplier = inst.contract_multiplier
                existing.tick_size = inst.tick_size
                existing.lot_size = inst.lot_size
                existing.as_of = now
                existing.available_at = now
            else:
                record = InstrumentRecord(
                    id=inst.instrument_id,
                    asset_id=inst.asset_id,
                    venue=inst.venue,
                    symbol=inst.symbol,
                    instrument_type=inst.instrument_type.value,
                    base_currency=inst.base_currency,
                    quote_currency=inst.quote_currency,
                    contract_multiplier=inst.contract_multiplier,
                    tick_size=inst.tick_size,
                    lot_size=inst.lot_size,
                    is_active=inst.is_active,
                    extra_metadata=inst.metadata,
                    producer=self.venue_name,
                    producer_version="0.1.0",
                    as_of=now,
                    available_at=now,
                    quality_status=QualityStatus.VALID.value,
                )
                session.add(record)
                count += 1
        session.commit()
        return count

    def save_bars(self, session: Session, observations: Sequence[MarketObservation]) -> int:
        """
        Save observations idempotently. If an observation for (instrument_id, interval, open_time)
        exists, skip or verify checksum.
        """
        saved_count = 0
        for obs in observations:
            # Enforce closed bar check strictly
            if not is_bar_closed(obs.open_time, obs.interval):
                continue

            existing = (
                session.query(MarketObservation)
                .filter_by(
                    instrument_id=obs.instrument_id,
                    interval=obs.interval,
                    open_time=obs.open_time,
                )
                .first()
            )
            if existing:
                # Idempotent skip if hash matches
                if existing.content_hash == obs.content_hash:
                    continue
                # If content changed for a closed bar, update and set status degraded/auditable
                existing.open = obs.open
                existing.high = obs.high
                existing.low = obs.low
                existing.close = obs.close
                existing.volume = obs.volume
                existing.content_hash = obs.content_hash
                existing.available_at = utc_now()
            else:
                session.add(obs)
                saved_count += 1

        session.commit()
        return saved_count
