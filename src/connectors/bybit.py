"""Bybit connector for crypto perpetual contracts (Breakoutprop proxy)."""

from datetime import datetime
import ccxt
import structlog

from src.connectors.base import BaseConnector, calculate_bar_hash
from src.core.enums import InstrumentType, QualityStatus, TimeInterval
from src.core.identity import (
    BREAKOUTPROP_TO_BYBIT_MAP,
    Instrument,
    build_instrument_id,
)
from src.core.models import MarketObservation
from src.core.time import (
    compute_bar_close_time,
    from_timestamp_ms,
    is_bar_closed,
    to_iso_utc,
    utc_now,
)

logger = structlog.get_logger()


class BybitConnector(BaseConnector):
    """Bybit connector providing free, public market data for crypto perpetuals."""

    def __init__(self):
        super().__init__(venue_name="bybit")
        self.client = ccxt.bybit({
            "enableRateLimit": True,
            "options": {"defaultType": "linear"},
        })

    def fetch_instruments(self, target_symbols: list[str] | None = None) -> list[Instrument]:
        """
        Fetch Bybit linear perpetual instruments matching Breakoutprop assets.
        """
        symbols_to_load = target_symbols or list(BREAKOUTPROP_TO_BYBIT_MAP.values())
        instruments = []

        try:
            markets = self.client.load_markets()
        except Exception as e:
            logger.error("Failed to load Bybit markets", error=str(e))
            # Fallback to predefined instrument definitions if offline/rate-limited
            for bp_sym, bybit_sym in BREAKOUTPROP_TO_BYBIT_MAP.items():
                asset = bp_sym.replace("USD", "")
                inst_id = build_instrument_id("bybit", bybit_sym, InstrumentType.PERPETUAL)
                instruments.append(
                    Instrument(
                        instrument_id=inst_id,
                        asset_id=asset,
                        venue="bybit",
                        symbol=bybit_sym,
                        instrument_type=InstrumentType.PERPETUAL,
                        base_currency=asset,
                        quote_currency="USDT",
                        contract_multiplier=1.0,
                        tick_size=0.1 if "BTC" in asset else 0.01,
                        lot_size=0.001,
                        metadata={"breakoutprop_symbol": bp_sym},
                    )
                )
            return instruments

        for sym in symbols_to_load:
            if sym in markets:
                m = markets[sym]
                asset = m.get("base", sym.split("/")[0])
                inst_id = build_instrument_id("bybit", sym, InstrumentType.PERPETUAL)
                precision = m.get("precision", {})
                limits = m.get("limits", {})
                
                # Check for Breakoutprop mapping
                bp_sym = None
                for bp_k, byb_v in BREAKOUTPROP_TO_BYBIT_MAP.items():
                    if byb_v == sym:
                        bp_sym = bp_k
                        break

                instruments.append(
                    Instrument(
                        instrument_id=inst_id,
                        asset_id=asset,
                        venue="bybit",
                        symbol=sym,
                        instrument_type=InstrumentType.PERPETUAL,
                        base_currency=m.get("base", asset),
                        quote_currency=m.get("quote", "USDT"),
                        contract_multiplier=float(m.get("contractSize", 1.0) or 1.0),
                        tick_size=float(precision.get("price", 0.01) or 0.01),
                        lot_size=float(limits.get("amount", {}).get("min", 0.001) or 0.001),
                        metadata={"breakoutprop_symbol": bp_sym or f"{asset}USD"},
                    )
                )
        return instruments

    def fetch_bars(
        self,
        instrument: Instrument,
        interval: str | TimeInterval = TimeInterval.H1,
        limit: int = 100,
        since: datetime | None = None,
    ) -> list[MarketObservation]:
        """
        Fetch closed OHLCV bars from Bybit via ccxt.
        
        INVARIANT: Unfinished bars currently forming in real-time are strictly dropped.
        """
        interval_str = interval.value if isinstance(interval, TimeInterval) else interval
        since_ms = int(since.timestamp() * 1000) if since else None
        
        try:
            raw_bars = self.client.fetch_ohlcv(
                symbol=instrument.symbol,
                timeframe=interval_str,
                since=since_ms,
                limit=limit + 1,  # +1 to account for dropped open candle
            )
        except Exception as e:
            logger.error("Error fetching bars from Bybit", symbol=instrument.symbol, error=str(e))
            return []

        observations: list[MarketObservation] = []
        now = utc_now()
        inst_id = getattr(instrument, "instrument_id", getattr(instrument, "id", None))

        for bar in raw_bars:
            ts_ms, o, h, l, c, v = bar
            open_time = from_timestamp_ms(ts_ms)
            close_time = compute_bar_close_time(open_time, interval_str)

            # Drop current unclosed candle
            if not is_bar_closed(open_time, interval_str, as_of=now):
                continue

            content_hash = calculate_bar_hash(
                inst_id,
                interval_str,
                to_iso_utc(open_time),
                float(o),
                float(h),
                float(l),
                float(c),
                float(v),
            )

            obs = MarketObservation(
                instrument_id=inst_id,
                interval=interval_str,
                open_time=open_time,
                close_time=close_time,
                open=float(o),
                high=float(h),
                low=float(l),
                close=float(c),
                volume=float(v),
                is_closed=True,
                content_hash=content_hash,
                producer=self.venue_name,
                producer_version="0.1.0",
                as_of=close_time,
                available_at=now,
                quality_status=QualityStatus.VALID.value,
            )
            observations.append(obs)

        return observations
