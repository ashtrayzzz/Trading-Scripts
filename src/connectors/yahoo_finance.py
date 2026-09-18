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

# Comprehensive multi-asset universe (Futures, Index/Sector ETFs, Global Equities, Canadian TSX)
DEFAULT_EQUITIES = [
    # 1. Major Futures Contracts (Indices, Commodities, Metals, Energy, Bonds, FX)
    ("ES=F", "ES", "future", "USD", "E-mini S&P 500 Futures"),
    ("NQ=F", "NQ", "future", "USD", "E-mini Nasdaq 100 Futures"),
    ("RTY=F", "RTY", "future", "USD", "E-mini Russell 2000 Futures"),
    ("YM=F", "YM", "future", "USD", "E-mini Dow Jones Futures"),
    ("CL=F", "CL", "future", "USD", "Crude Oil WTI Futures"),
    ("NG=F", "NG", "future", "USD", "Natural Gas Futures"),
    ("GC=F", "GC", "future", "USD", "Gold Futures"),
    ("SI=F", "SI", "future", "USD", "Silver Futures"),
    ("HG=F", "HG", "future", "USD", "Copper Futures"),
    ("ZB=F", "ZB", "future", "USD", "30-Year U.S. Treasury Bond Futures"),
    ("ZN=F", "ZN", "future", "USD", "10-Year U.S. Treasury Note Futures"),
    ("DX-Y.NYB", "DXY", "index", "USD", "U.S. Dollar Index"),

    # 2. Benchmark Index & Macro ETFs
    ("SPY", "SPY", "etf", "USD", "SPDR S&P 500 ETF"),
    ("QQQ", "QQQ", "etf", "USD", "Invesco QQQ Trust"),
    ("IWM", "IWM", "etf", "USD", "iShares Russell 2000 ETF"),
    ("DIA", "DIA", "etf", "USD", "SPDR Dow Jones Industrial ETF"),
    ("TLT", "TLT", "etf", "USD", "iShares 20+ Year Treasury Bond ETF"),
    ("GLD", "GLD", "etf", "USD", "SPDR Gold Shares"),
    ("SLV", "SLV", "etf", "USD", "iShares Silver Trust"),
    ("USO", "USO", "etf", "USD", "United States Oil Fund"),

    # 3. SPDR Industry & Sector ETFs
    ("XLK", "XLK", "etf", "USD", "Technology Select Sector SPDR"),
    ("XLF", "XLF", "etf", "USD", "Financial Select Sector SPDR"),
    ("XLE", "XLE", "etf", "USD", "Energy Select Sector SPDR"),
    ("XLV", "XLV", "etf", "USD", "Health Care Select Sector SPDR"),
    ("XLI", "XLI", "etf", "USD", "Industrial Select Sector SPDR"),
    ("XLY", "XLY", "etf", "USD", "Consumer Discretionary Select SPDR"),
    ("XLP", "XLP", "etf", "USD", "Consumer Staples Select SPDR"),
    ("XLU", "XLU", "etf", "USD", "Utilities Select Sector SPDR"),
    ("XLB", "XLB", "etf", "USD", "Materials Select Sector SPDR"),
    ("XLRE", "XLRE", "etf", "USD", "Real Estate Select Sector SPDR"),
    ("SMH", "SMH", "etf", "USD", "VanEck Semiconductor ETF"),
    ("XBI", "XBI", "etf", "USD", "SPDR S&P Biotech ETF"),
    ("KRE", "KRE", "etf", "USD", "SPDR S&P Regional Banking ETF"),
    ("GDX", "GDX", "etf", "USD", "VanEck Gold Miners ETF"),

    # 4. Mega-Cap Tech, Cloud & AI Software
    ("AAPL", "AAPL", "equity", "USD", "Apple Inc."),
    ("MSFT", "MSFT", "equity", "USD", "Microsoft Corporation"),
    ("GOOGL", "GOOGL", "equity", "USD", "Alphabet Inc."),
    ("AMZN", "AMZN", "equity", "USD", "Amazon.com Inc."),
    ("META", "META", "equity", "USD", "Meta Platforms Inc."),
    ("ORCL", "ORCL", "equity", "USD", "Oracle Corporation"),
    ("CRM", "CRM", "equity", "USD", "Salesforce Inc."),
    ("ADBE", "ADBE", "equity", "USD", "Adobe Inc."),
    ("NOW", "NOW", "equity", "USD", "ServiceNow Inc."),
    ("PLTR", "PLTR", "equity", "USD", "Palantir Technologies"),
    ("SNOW", "SNOW", "equity", "USD", "Snowflake Inc."),

    # 5. Semiconductors, Hardware & Compute
    ("NVDA", "NVDA", "equity", "USD", "NVIDIA Corporation"),
    ("AMD", "AMD", "equity", "USD", "Advanced Micro Devices"),
    ("AVGO", "AVGO", "equity", "USD", "Broadcom Inc."),
    ("QCOM", "QCOM", "equity", "USD", "Qualcomm Inc."),
    ("INTC", "INTC", "equity", "USD", "Intel Corporation"),
    ("MU", "MU", "equity", "USD", "Micron Technology"),
    ("TXN", "TXN", "equity", "USD", "Texas Instruments"),
    ("TSM", "TSM", "equity", "USD", "Taiwan Semiconductor"),
    ("ARM", "ARM", "equity", "USD", "Arm Holdings"),
    ("SMCI", "SMCI", "equity", "USD", "Super Micro Computer"),
    ("ASML", "ASML", "equity", "USD", "ASML Holding"),
    ("AMAT", "AMAT", "equity", "USD", "Applied Materials"),

    # 6. Financials, Banking & Fintech / Crypto-Proxies
    ("JPM", "JPM", "equity", "USD", "JPMorgan Chase & Co."),
    ("BAC", "BAC", "equity", "USD", "Bank of America Corp."),
    ("GS", "GS", "equity", "USD", "Goldman Sachs Group"),
    ("MS", "MS", "equity", "USD", "Morgan Stanley"),
    ("V", "V", "equity", "USD", "Visa Inc."),
    ("MA", "MA", "equity", "USD", "Mastercard Inc."),
    ("COIN", "COIN", "equity", "USD", "Coinbase Global"),
    ("MSTR", "MSTR", "equity", "USD", "MicroStrategy Inc."),
    ("HOOD", "HOOD", "equity", "USD", "Robinhood Markets"),
    ("PYPL", "PYPL", "equity", "USD", "PayPal Holdings"),
    ("SOFI", "SOFI", "equity", "USD", "SoFi Technologies"),
    ("PANW", "PANW", "equity", "USD", "Palo Alto Networks"),
    ("CRWD", "CRWD", "equity", "USD", "CrowdStrike Holdings"),

    # 7. Healthcare, Pharma & Medical Devices
    ("LLY", "LLY", "equity", "USD", "Eli Lilly and Company"),
    ("NVO", "NVO", "equity", "USD", "Novo Nordisk"),
    ("JNJ", "JNJ", "equity", "USD", "Johnson & Johnson"),
    ("UNH", "UNH", "equity", "USD", "UnitedHealth Group"),
    ("PFE", "PFE", "equity", "USD", "Pfizer Inc."),
    ("ABBV", "ABBV", "equity", "USD", "AbbVie Inc."),
    ("MRK", "MRK", "equity", "USD", "Merck & Co."),
    ("ISRG", "ISRG", "equity", "USD", "Intuitive Surgical"),

    # 8. Energy, Oil & Gas Majors
    ("XOM", "XOM", "equity", "USD", "ExxonMobil Corporation"),
    ("CVX", "CVX", "equity", "USD", "Chevron Corporation"),
    ("COP", "COP", "equity", "USD", "ConocoPhillips"),
    ("SLB", "SLB", "equity", "USD", "SLB (Schlumberger)"),
    ("EOG", "EOG", "equity", "USD", "EOG Resources"),
    ("OXY", "OXY", "equity", "USD", "Occidental Petroleum"),

    # 9. Industrials, Aerospace, Defense & Machinery
    ("CAT", "CAT", "equity", "USD", "Caterpillar Inc."),
    ("DE", "DE", "equity", "USD", "Deere & Company"),
    ("GE", "GE", "equity", "USD", "GE Aerospace"),
    ("BA", "BA", "equity", "USD", "Boeing Company"),
    ("LMT", "LMT", "equity", "USD", "Lockheed Martin"),
    ("RTX", "RTX", "equity", "USD", "RTX Corporation"),
    ("TSLA", "TSLA", "equity", "USD", "Tesla Inc."),
    ("F", "F", "equity", "USD", "Ford Motor Company"),
    ("GM", "GM", "equity", "USD", "General Motors"),

    # 10. Consumer, Retail, Entertainment & Mobility
    ("WMT", "WMT", "equity", "USD", "Walmart Inc."),
    ("COST", "COST", "equity", "USD", "Costco Wholesale"),
    ("HD", "HD", "equity", "USD", "Home Depot Inc."),
    ("TGT", "TGT", "equity", "USD", "Target Corporation"),
    ("NKE", "NKE", "equity", "USD", "Nike Inc."),
    ("MCD", "MCD", "equity", "USD", "McDonald's Corporation"),
    ("SBUX", "SBUX", "equity", "USD", "Starbucks Corporation"),
    ("DIS", "DIS", "equity", "USD", "Walt Disney Company"),
    ("NFLX", "NFLX", "equity", "USD", "Netflix Inc."),
    ("UBER", "UBER", "equity", "USD", "Uber Technologies"),

    # 11. Canadian ETFs & Wealthsimple Blue Chips (TSX)
    ("XIU.TO", "XIU", "etf", "CAD", "iShares S&P/TSX 60 Index ETF"),
    ("VFV.TO", "VFV", "etf", "CAD", "Vanguard S&P 500 Index ETF (CAD)"),
    ("XIC.TO", "XIC", "etf", "CAD", "iShares Core S&P/TSX Composite ETF"),
    ("XEG.TO", "XEG", "etf", "CAD", "iShares S&P/TSX Capped Energy ETF"),
    ("ZEB.TO", "ZEB", "etf", "CAD", "BMO Equal Weight Banks Index ETF"),
    ("SHOP.TO", "SHOP", "equity", "CAD", "Shopify Inc. (TSX)"),
    ("RY.TO", "RY", "equity", "CAD", "Royal Bank of Canada"),
    ("TD.TO", "TD", "equity", "CAD", "Toronto-Dominion Bank"),
    ("BNS.TO", "BNS", "equity", "CAD", "Bank of Nova Scotia"),
    ("BMO.TO", "BMO", "equity", "CAD", "Bank of Montreal"),
    ("CM.TO", "CM", "equity", "CAD", "Canadian Imperial Bank of Commerce"),
    ("ENB.TO", "ENB", "equity", "CAD", "Enbridge Inc."),
    ("TRP.TO", "TRP", "equity", "CAD", "TC Energy Corporation"),
    ("SU.TO", "SU", "equity", "CAD", "Suncor Energy Inc."),
    ("CNQ.TO", "CNQ", "equity", "CAD", "Canadian Natural Resources"),
    ("CNR.TO", "CNR", "equity", "CAD", "Canadian National Railway"),
    ("CP.TO", "CP", "equity", "CAD", "Canadian Pacific Kansas City"),
    ("BAM.TO", "BAM", "equity", "CAD", "Brookfield Asset Management"),
    ("BN.TO", "BN", "equity", "CAD", "Brookfield Corporation"),
    ("ATD.TO", "ATD", "equity", "CAD", "Alimentation Couche-Tard"),
    ("CSU.TO", "CSU", "equity", "CAD", "Constellation Software"),
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
    """Yahoo Finance connector for stocks, ETFs, futures, and indices."""

    def __init__(self):
        super().__init__(venue_name="yahoo_finance")

    def fetch_instruments(self, symbols: list[str | tuple] | None = None) -> list[Instrument]:
        """Fetch instrument metadata for equity, ETF, future, and index tickers."""
        instruments = []
        target_symbols = symbols or DEFAULT_EQUITIES

        for item in target_symbols:
            if isinstance(item, tuple):
                sym, asset, inst_type_str, quote_curr, name = item
                type_lower = inst_type_str.lower()
                if type_lower == "future":
                    itype = InstrumentType.FUTURE
                elif type_lower == "index":
                    itype = InstrumentType.INDEX
                elif type_lower == "etf":
                    itype = InstrumentType.ETF
                else:
                    itype = InstrumentType.EQUITY
            else:
                sym = item
                asset = sym.split(".")[0].replace("=F", "")
                quote_curr = "CAD" if sym.endswith(".TO") else "USD"
                name = f"{sym} Asset"
                if sym.endswith("=F"):
                    itype = InstrumentType.FUTURE
                elif sym.startswith("^") or "NYB" in sym:
                    itype = InstrumentType.INDEX
                elif any(sym.startswith(p) for p in ("SPY", "QQQ", "IWM", "DIA", "XL", "SMH", "TLT", "GLD", "SLV", "USO", "XIU", "VFV", "XIC", "XEG", "ZEB", "GDX", "XBI", "KRE")):
                    itype = InstrumentType.ETF
                else:
                    itype = InstrumentType.EQUITY

            multiplier = 50.0 if sym == "ES=F" else (20.0 if sym == "NQ=F" else (100.0 if sym == "GC=F" else (1000.0 if sym == "CL=F" else 1.0)))
            tick = 0.25 if sym in ("ES=F", "NQ=F") else 0.01

            inst = Instrument(
                instrument_id=f"yahoo:{sym}:{itype.value}",
                asset_id=asset,
                venue="yahoo",
                symbol=sym,
                instrument_type=itype,
                base_currency=asset,
                quote_currency=quote_curr,
                contract_multiplier=multiplier,
                tick_size=tick,
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
