"""Yahoo Finance connector for equities and ETFs (Wealthsimple + IBKR)."""

from datetime import datetime
import structlog
import yfinance as yf

from src.connectors.base import BaseConnector, calculate_bar_hash
from src.core.enums import InstrumentType, QualityStatus, TimeInterval
from src.core.identity import Instrument, build_instrument_id
from src.core.models import MarketObservation
from src.core.time import (
    compute_bar_close_time,
    ensure_utc,
    is_bar_closed,
    to_iso_utc,
    utc_now,
)

logger = structlog.get_logger()

# Popular equities & ETFs for Personal Accounts
DEFAULT_EQUITIES = [
    # US equities & ETFs (IBKR)
    ("SPY", "SPY", "equity", "USD", "SPDR S&P 500 ETF"),
    ("QQQ", "QQQ", "equity", "USD", "Invesco QQQ Trust"),
    ("AAPL", "AAPL", "equity", "USD", "Apple Inc."),
    ("MSFT", "MSFT", "equity", "USD", "Microsoft Corporation"),
    ("NVDA", "NVDA", "equity", "USD", "NVIDIA Corporation"),
    ("TSLA", "TSLA", "equity", "USD", "Tesla Inc."),
    # Canadian equities & ETFs (Wealthsimple)
    ("XIU.TO", "XIU", "equity", "CAD", "iShares S&P/TSX 60 Index ETF"),
    ("VFV.TO", "VFV", "equity", "CAD", "Vanguard S&P 500 Index ETF (CAD)"),
    ("SHOP.TO", "SHOP", "equity", "CAD", "Shopify Inc. (TSX)"),
    ("RY.TO", "RY", "equity", "CAD", "Royal Bank of Canada"),
]

INTERVAL_MAP = {
    TimeInterval.M15: "15m",
    TimeInterval.H1: "60m",
    TimeInterval.H4: "1d",  # Yahoo free tier lacks 4h, fallback to 1d or synthesize
    TimeInterval.D1: "1d",
    TimeInterval.W1: "1wk",
    TimeInterval.M1: "1mo",
    "15m": "15m",
    "1h": "60m",
    "4h": "1d",
    "1d": "1d",
    "1w": "1wk",
    "1M": "1mo",
}


class YahooFinanceConnector(BaseConnector):
    """Yahoo Finance connector for stocks and ETFs."""

    def __init__(self):
        super().__init__(venue_name="yahoo_finance")

    def fetch_instruments(self, symbols: list[str] | None = None) -> list[Instrument]:
        """Fetch instrument metadata for equity tickers."""
        instruments = []
        target_symbols = symbols or DEFAULT_EQUITIES

        for item in target_symbols:
            sym = item[0] if isinstance(item, tuple) else item
            inst = Instrument(
                instrument_id=f"yahoo:{sym}:equity",
                symbol=sym,
                name=f"{sym} Equity/ETF",
                instrument_type=InstrumentType.ETF if sym in ("SPY", "QQQ", "XIU.TO") else InstrumentType.EQUITY,
                base_asset=sym.split(".")[0],
                quote_asset="CAD" if sym.endswith(".TO") else "USD",
                price_precision=2,
                quantity_precision=2,
                min_quantity=1.0,
                contract_multiplier=1.0,
                is_active=True,
                extra={"source": "yahoo_finance"},
            )
            instruments.append(inst)

        return instruments

    def fetch_bars(
        self,
        instrument: Instrument,
        interval: str | TimeInterval = TimeInterval.D1,
        limit: int = 60,
        since: datetime | None = None,
    ) -> list[MarketObservation]:
        """Fetch closed bars for an equity/ETF."""
        interval_str = interval.value if isinstance(interval, TimeInterval) else interval
        yf_interval = INTERVAL_MAP.get(interval_str, "1d")

        try:
            ticker = yf.Ticker(instrument.symbol)
            # Fetch adequate history
            if yf_interval == "15m":
                period = "1mo"
            elif yf_interval == "60m":
                period = "3mo"
            elif yf_interval in ("1wk", "1mo"):
                period = "5y"
            else:
                period = "1y"

            df = ticker.history(period=period, interval=yf_interval)
            if df.empty:
                return []
        except Exception as e:
            logger.error("Error fetching Yahoo Finance bars", symbol=instrument.symbol, error=str(e))
            return []

        observations: list[MarketObservation] = []
        now = utc_now()
        inst_id = getattr(instrument, "instrument_id", getattr(instrument, "id", None))

        # Limit to the requested count
        recent_df = df.tail(limit + 1)

        for index, row in recent_df.iterrows():
            # index is DatetimeIndex
            open_time = ensure_utc(index.to_pydatetime())
            close_time = compute_bar_close_time(open_time, interval_str)

            if not is_bar_closed(open_time, interval_str, as_of=now):
                continue

            o = float(row["Open"])
            h = float(row["High"])
            l = float(row["Low"])
            c = float(row["Close"])
            v = float(row["Volume"])

            content_hash = calculate_bar_hash(
                inst_id,
                interval_str,
                to_iso_utc(open_time),
                o, h, l, c, v
            )

            obs = MarketObservation(
                instrument_id=inst_id,
                interval=interval_str,
                open_time=open_time,
                close_time=close_time,
                open=o,
                high=h,
                low=l,
                close=c,
                volume=v,
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
