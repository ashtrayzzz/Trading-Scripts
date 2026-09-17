"""Instrument and asset identity resolution.

Key invariant: Venue and contract identity are explicit.
Never equate a spot symbol with a perpetual, or prices from different venues.
"""

from dataclasses import dataclass, field
from typing import Optional
from src.core.enums import InstrumentType


@dataclass(frozen=True)
class Asset:
    """Underlying economic asset (e.g. BTC, ETH, AAPL)."""
    asset_id: str
    name: str
    category: str  # "crypto", "equity", "index", "commodity"


@dataclass(frozen=True)
class Instrument:
    """A specific tradable contract on a specific venue."""
    instrument_id: str  # e.g., "bybit:BTCUSDT:perpetual", "breakoutprop:BTCUSD:cfd"
    asset_id: str
    venue: str          # "bybit", "breakoutprop", "yahoo", "ibkr"
    symbol: str         # Provider native symbol e.g. "BTC/USDT:USDT" or "AAPL"
    instrument_type: InstrumentType
    base_currency: str
    quote_currency: str
    contract_multiplier: float = 1.0
    tick_size: float = 0.01
    lot_size: float = 0.001
    is_active: bool = True
    metadata: dict = field(default_factory=dict)


# Mapping dictionary from Breakoutprop symbol to Bybit proxy symbol
# Breakoutprop uses pairs like BTCUSD, ETHUSD, SOLUSD, etc.
# Bybit USDT.P perpetuals are the designated closest proxies.
BREAKOUTPROP_TO_BYBIT_MAP: dict[str, str] = {
    "BTCUSD": "BTC/USDT:USDT",
    "ETHUSD": "ETH/USDT:USDT",
    "SOLUSD": "SOL/USDT:USDT",
    "XRPUSD": "XRP/USDT:USDT",
    "DOGEUSD": "DOGE/USDT:USDT",
    "ADAUSD": "ADA/USDT:USDT",
    "AVAXUSD": "AVAX/USDT:USDT",
    "LINKUSD": "LINK/USDT:USDT",
    "SUIUSD": "SUI/USDT:USDT",
    "NEARUSD": "NEAR/USDT:USDT",
    "APTUSD": "APT/USDT:USDT",
    "BNBUSD": "BNB/USDT:USDT",
    "DOTUSD": "DOT/USDT:USDT",
    "LTCUSD": "LTC/USDT:USDT",
    "BCHUSD": "BCH/USDT:USDT",
    "UNIUSD": "UNI/USDT:USDT",
    "PEPEUSD": "1000PEPE/USDT:USDT",
    "SHIBUSD": "1000SHIB/USDT:USDT",
}

# Reverse mapping: Bybit perpetual symbol -> Breakoutprop symbol
BYBIT_TO_BREAKOUTPROP_MAP: dict[str, str] = {
    bybit_sym: bp_sym for bp_sym, bybit_sym in BREAKOUTPROP_TO_BYBIT_MAP.items()
}


def build_instrument_id(venue: str, symbol: str, instrument_type: InstrumentType) -> str:
    """Build a deterministic, canonical instrument ID."""
    clean_sym = symbol.replace("/", "_").replace(":", "_").replace(" ", "")
    return f"{venue.lower()}:{clean_sym}:{instrument_type.value}"


def map_bybit_to_breakoutprop(bybit_symbol: str) -> Optional[str]:
    """Resolve Bybit proxy symbol to Breakoutprop tradable symbol."""
    return BYBIT_TO_BREAKOUTPROP_MAP.get(bybit_symbol)


def map_breakoutprop_to_bybit(breakout_symbol: str) -> Optional[str]:
    """Resolve Breakoutprop symbol to Bybit proxy symbol."""
    return BREAKOUTPROP_TO_BYBIT_MAP.get(breakout_symbol)
