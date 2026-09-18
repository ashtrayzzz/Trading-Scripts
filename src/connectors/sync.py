"""Unified data synchronization and market scan pipeline."""

import concurrent.futures
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
    bybit_limit: int | None = None,
    bar_limit: int = 60,
    log_fn: Callable[..., None] | None = None,
) -> dict[str, int]:
    """
    Fetch the newest closed bars from Bybit and Yahoo Finance across the entire active ticker universe.
    Uses concurrent thread pool workers for maximum speed and safe rate-limit compliance.
    """
    stats = {"bybit_bars": 0, "yahoo_bars": 0, "total_bars": 0, "instruments_synced": 0}

    def notify(pct: float, msg: str):
        if log_fn:
            try:
                log_fn(pct, msg)
            except TypeError:
                log_fn(msg)

    # 1. Bybit crypto perpetuals
    try:
        notify(0.05, "Connecting to Bybit linear perpetuals feed...")
        bybit = BybitConnector()
        insts = bybit.fetch_instruments()
        bybit.save_instruments(session, insts)

        target_insts = insts if bybit_limit is None else insts[:bybit_limit]
        stats["instruments_synced"] += len(target_insts)

        def fetch_bybit_instrument_bars(inst):
            all_inst_bars = []
            for interval in ["4h", "1d", "1w", "1h"]:
                bars = bybit.fetch_bars(inst, interval=interval, limit=bar_limit)
                all_inst_bars.extend(bars)
            return inst.symbol, all_inst_bars

        completed_bybit = 0
        total_bybit = len(target_insts)
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            future_to_inst = {executor.submit(fetch_bybit_instrument_bars, inst): inst for inst in target_insts}
            for future in concurrent.futures.as_completed(future_to_inst):
                sym, bars = future.result()
                saved = bybit.save_bars(session, bars)
                stats["bybit_bars"] += saved
                completed_bybit += 1
                progress = 0.05 + (0.40 * (completed_bybit / total_bybit))
                notify(progress, f"Bybit: {sym} ({completed_bybit}/{total_bybit} pairs)")
    except Exception as e:
        logger.error("Bybit sync error", error=str(e))
        notify(0.45, f"Warning: Bybit sync encountered: {e}")

    # 2. Yahoo Finance equities & ETFs
    try:
        notify(0.45, "Connecting to Yahoo Finance equities & ETFs feed...")
        yf = YahooFinanceConnector()
        y_insts = yf.fetch_instruments()
        yf.save_instruments(session, y_insts)
        stats["instruments_synced"] += len(y_insts)

        def fetch_yahoo_instrument_bars(inst):
            all_inst_bars = []
            for interval in ["1h", "1d", "1w"]:
                bars = yf.fetch_bars(inst, interval=interval, limit=bar_limit)
                all_inst_bars.extend(bars)
            return inst.symbol, all_inst_bars

        completed_yf = 0
        total_yf = len(y_insts)
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            future_to_inst = {executor.submit(fetch_yahoo_instrument_bars, inst): inst for inst in y_insts}
            for future in concurrent.futures.as_completed(future_to_inst):
                sym, bars = future.result()
                saved = yf.save_bars(session, bars)
                stats["yahoo_bars"] += saved
                completed_yf += 1
                progress = 0.45 + (0.35 * (completed_yf / total_yf))
                notify(progress, f"Yahoo Finance: {sym} ({completed_yf}/{total_yf} assets)")
    except Exception as e:
        logger.error("Yahoo Finance sync error", error=str(e))
        notify(0.80, f"Warning: Yahoo Finance sync encountered: {e}")

    stats["total_bars"] = stats["bybit_bars"] + stats["yahoo_bars"]
    return stats


def sync_and_rescan_all(
    session: Session,
    horizons: list[str] | None = None,
    log_fn: Callable[..., None] | None = None,
) -> dict[str, int]:
    """
    End-to-end pipeline:
    1. Pulls newest closed bars concurrently from live APIs across the full universe.
    2. Runs technical scanner across all horizons.
    3. Assembles and scores opportunity versions with latest market state.
    """
    horizons = horizons or ["4h", "1d", "1w", "1h"]
    sync_stats = sync_all_feeds(session, log_fn=log_fn)

    def notify(pct: float, msg: str):
        if log_fn:
            try:
                log_fn(pct, msg)
            except TypeError:
                log_fn(msg)

    notify(0.82, f"Synced {sync_stats['total_bars']} bars across {sync_stats['instruments_synced']} instruments. Scanning setups...")

    instruments = session.query(InstrumentRecord).filter_by(is_active=True).all()
    inst_ids = [i.id for i in instruments]

    scanner = TechnicalScanner(mode=PlaybookMode.FILTERED)
    assembler = OpportunityAssembler()
    now = utc_now()

    total_signals = 0
    total_opps = 0

    for idx, h in enumerate(horizons):
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
        progress = 0.82 + (0.18 * ((idx + 1) / len(horizons)))
        notify(progress, f"Scanned {h} horizon ({len(res.signals)} signals, {len(opps)} setups)")

    return {
        "new_bars": sync_stats["total_bars"],
        "bybit_bars": sync_stats["bybit_bars"],
        "yahoo_bars": sync_stats["yahoo_bars"],
        "instruments_synced": sync_stats["instruments_synced"],
        "signals_emitted": total_signals,
        "opportunities_assembled": total_opps,
    }
