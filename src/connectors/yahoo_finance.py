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

# Popular equities & ETFs for Personal Accounts (Wealthsimple + IBKR)
DEFAULT_EQUITIES = [
    # US Index, Sector & Macro ETFs (IBKR)
    ("SPY", "SPY", "etf", "USD", "SPDR S&P 500 ETF"),
    ("QQQ", "QQQ", "etf", "USD", "Invesco QQQ Trust"),
    ("IWM", "IWM", "etf", "USD", "iShares Russell 2000 ETF"),
    ("DIA", "DIA", "etf", "USD", "SPDR Dow Jones Industrial ETF"),
    ("SMH", "SMH", "etf", "USD", "VanEck Semiconductor ETF"),
    ("TLT", "TLT", "etf", "USD", "iShares 20+ Year Treasury Bond ETF"),
    ("GLD", "GLD", "etf", "USD", "SPDR Gold Shares"),
    ("SLV", "SLV", "etf", "USD", "iShares Silver Trust"),
    ("USO", "USO", "etf", "USD", "United States Oil Fund"),
    # US Mega-Cap Tech, AI & High-Beta Momentum (IBKR)
    ("AAPL", "AAPL", "equity", "USD", "Apple Inc."),
    ("MSFT", "MSFT", "equity", "USD", "Microsoft Corporation"),
    ("NVDA", "NVDA", "equity", "USD", "NVIDIA Corporation"),
    ("TSLA", "TSLA", "equity", "USD", "Tesla Inc."),
    ("AMZN", "AMZN", "equity", "USD", "Amazon.com Inc."),
    ("GOOGL", "GOOGL", "equity", "USD", "Alphabet Inc."),
    ("META", "META", "equity", "USD", "Meta Platforms Inc."),
    ("AMD", "AMD", "equity", "USD", "Advanced Micro Devices"),
    ("COIN", "COIN", "equity", "USD", "Coinbase Global"),
    ("MSTR", "MSTR", "equity", "USD", "MicroStrategy Inc."),
    ("PLTR", "PLTR", "equity", "USD", "Palantir Technologies"),
    ("NFLX", "NFLX", "equity", "USD", "Netflix Inc."),
    ("SMCI", "SMCI", "equity", "USD", "Super Micro Computer"),
    # Canadian Core ETFs & Blue Chips (Wealthsimple)
    ("XIU.TO", "XIU", "etf", "CAD", "iShares S&P/TSX 60 Index ETF"),
    ("VFV.TO", "VFV", "etf", "CAD", "Vanguard S&P 500 Index ETF (CAD)"),
    ("XIC.TO", "XIC", "etf", "CAD", "iShares Core S&P/TSX Composite ETF"),
    ("XEG.TO", "XEG", "etf", "CAD", "iShares S&P/TSX Capped Energy ETF"),
    ("SHOP.TO", "SHOP", "equity", "CAD", "Shopify Inc. (TSX)"),
    ("RY.TO", "RY", "equity", "CAD", "Royal Bank of Canada"),
    ("TD.TO", "TD", "equity", "CAD", "Toronto-Dominion Bank"),
    ("ENB.TO", "ENB", "equity", "CAD", "Enbridge Inc."),
    ("CNR.TO", "CNR", "equity", "CAD", "Canadian National Railway"),
    ("BNS.TO", "BNS", "equity", "CAD", "Bank of Nova Scotia"),
    ("SU.TO", "SU", "equity", "CAD", "Suncor Energy Inc."),
    ("BAM.TO", "BAM", "equity", "CAD", "Brookfield Asset Management"),
    ("ATD.TO", "ATD", "equity", "CAD", "Alimentation Couche-Tard"),
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

    def fetch_instruments(self, symbols: list[str | tuple] | None = None) -> list[Instrument]:
        """Fetch instrument metadata for equity tickers."""
        instruments = []
        target_symbols = symbols or DEFAULT_EQUITIES

        for item in target_symbols:
            if isinstance(item, tuple):
                sym, asset, inst_type_str, quote_curr, name = item
                itype = InstrumentType.ETF if inst_type_str.lower() == "etf" else InstrumentType.EQUITY
            else:
                sym = item
                asset = sym.split(".")[0]
                quote_curr = "CAD" if sym.endswith(".TO") else "USD"
                name = f"{sym} Equity/ETF"
                itype = InstrumentType.ETF if any(sym.startswith(p) for p in ("SPY", "QQQ", "IWM", "DIA", "SMH", "TLT", "GLD", "SLV", "USO", "XIU", "VFV", "XIC", "XEG")) else InstrumentType.EQUITY
            inst = Instrument(
                instrument_id=f"yahoo:{sym}:equity",
                asset_id=asset,
                venue="yahoo",
                symbol=sym,
                instrument_type=itype,
                base_currency=asset,
                quote_currency=quote_curr,
                contract_multiplier=1.0,
                tick_size=0.01,
                lot_size=1.0,
                is_active=True,
                metadata={"name": name, "source": "yahoo_finance"},
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
