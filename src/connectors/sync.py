"""Unified data synchronization and market scan pipeline."""

from datetime import datetime
from typing import Callable
import structlog
from sqlalchemy.orm import Session

from src.connectors.bybit import BybitConnector
from src.connectors.yahoo_finance import YahooFinanceConnector
from src.core.enums import PlaybookMode
from src.core.identity import Instrument
from src.core.models import InstrumentRecord
from src.core.time import utc_now
from src.intelligence.base import ModuleContext
from src.intelligence.technical import TechnicalScanner
from src.opportunities.assembly import OpportunityAssembler

logger = structlog.get_logger()


def sync_all_feeds(
    session: Session,
    bybit_limit: int = 10,
    bar_limit: int = 60,
    log_fn: Callable[[str], None] | None = None,
) -> dict[str, int]:
    """
    Fetch the newest closed bars from Bybit and Yahoo Finance and persist new bars.
    
    Safe within all API rate limits:
    - Bybit: ~40 calls (limit: 120 req/min unauthenticated).
    - Yahoo Finance: ~30 calls (limit: ~2,000 req/hour).
    """
    stats = {"bybit_bars": 0, "yahoo_bars": 0, "total_bars": 0}

    # 1. Bybit crypto perpetuals
    try:
        if log_fn:
            log_fn("Connecting to Bybit linear perpetuals...")
        bybit = BybitConnector()
        insts = bybit.fetch_instruments()
        bybit.save_instruments(session, insts)

        target_insts = insts[:bybit_limit]
        for idx, inst in enumerate(target_insts):
            for interval in ["15m", "1h", "4h", "1d"]:
                bars = bybit.fetch_bars(inst, interval=interval, limit=bar_limit)
                saved = bybit.save_bars(session, bars)
                stats["bybit_bars"] += saved
            if log_fn:
                log_fn(f"Bybit: {inst.symbol} synced ({idx + 1}/{len(target_insts)})")
    except Exception as e:
        logger.error("Bybit sync error", error=str(e))
        if log_fn:
            log_fn(f"Warning: Bybit sync encountered: {e}")

    # 2. Yahoo Finance equities & ETFs
    try:
        if log_fn:
            log_fn("Connecting to Yahoo Finance equities & ETFs...")
        yf = YahooFinanceConnector()
        y_insts = yf.fetch_instruments()
        yf.save_instruments(session, y_insts)

        for idx, y_inst in enumerate(y_insts):
            for interval in ["1h", "1d", "1w"]:
                bars = yf.fetch_bars(y_inst, interval=interval, limit=bar_limit)
                saved = yf.save_bars(session, bars)
                stats["yahoo_bars"] += saved
            if log_fn:
                log_fn(f"Yahoo: {y_inst.symbol} synced ({idx + 1}/{len(y_insts)})")
    except Exception as e:
        logger.error("Yahoo Finance sync error", error=str(e))
        if log_fn:
            log_fn(f"Warning: Yahoo Finance sync encountered: {e}")

    stats["total_bars"] = stats["bybit_bars"] + stats["yahoo_bars"]
    return stats


def sync_and_rescan_all(
    session: Session,
    horizons: list[str] | None = None,
    log_fn: Callable[[str], None] | None = None,
) -> dict[str, int]:
    """
    End-to-end pipeline:
    1. Pulls newest closed bars from live APIs (Bybit + Yahoo).
    2. Runs technical scanner across all horizons.
    3. Assembles and scores opportunity versions with latest market state.
    """
    horizons = horizons or ["15m", "1h", "4h", "1d"]
    sync_stats = sync_all_feeds(session, log_fn=log_fn)

    if log_fn:
        log_fn(f"Synced {sync_stats['total_bars']} new bars. Evaluating technical setups...")

    instruments = session.query(InstrumentRecord).filter_by(is_active=True).all()
    inst_ids = [i.id for i in instruments]

    scanner = TechnicalScanner(mode=PlaybookMode.FILTERED)
    assembler = OpportunityAssembler()
    now = utc_now()

    total_signals = 0
    total_opps = 0

    for h in horizons:
        ctx = ModuleContext(
            instrument_ids=inst_ids,
            horizon=h,
            decision_cutoff=now,
            run_id=f"sync_scan_{now.strftime('%Y%m%d%H%M%S')}",
        )
        res = scanner.evaluate(ctx, session)
        total_signals += len(res.signals)

        opps = assembler.assemble_opportunities(session, playbook_name=f"playbook_{h}", horizon=h)
        total_opps += len(opps)
        if log_fn:
            log_fn(f"Scanned {h} timeframe: {len(res.signals)} signals, {len(opps)} candidate setups.")

    return {
        "new_bars": sync_stats["total_bars"],
        "bybit_bars": sync_stats["bybit_bars"],
        "yahoo_bars": sync_stats["yahoo_bars"],
        "signals_emitted": total_signals,
        "opportunities_assembled": total_opps,
    }
