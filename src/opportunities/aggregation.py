"""Ticker-level opportunity aggregation and scan deduplication engine.

Aggregates multiple horizon opportunities and strategy setups for a single ticker
into a unified institutional candidate profile with multi-timeframe confluence.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from src.core.enums import CandidateLifecycle
from src.core.models import ModuleSignal, OpportunityVersion
from src.core.time import utc_now


@dataclass
class TimeframeDetail:
    """Detailed Where, Why, and How breakdown for a specific signal on a specific timeframe."""
    horizon: str
    merit_score: float | None
    confidence_score: float
    trigger_price: float | None
    invalidation_price: float | None
    target_2r: float | None
    target_3r: float | None
    risk_distance: float | None
    risk_pct: float | None
    where_summary: str
    why_summary: str
    how_summary: str
    as_of: datetime
    opportunity_id: str
    opportunity_version: OpportunityVersion


@dataclass
class TriggeredSignalGroup:
    """A distinct strategy system triggered on a ticker across one or more timeframes."""
    setup_name: str
    system_acronym: str
    direction: str  # 'long' or 'short'
    timeframes: list[str]  # e.g. ['15m', '1h']
    timeframe_details: dict[str, TimeframeDetail] = field(default_factory=dict)

    @property
    def is_multi_timeframe(self) -> bool:
        return len(self.timeframes) > 1

    @property
    def confluence_badge(self) -> str:
        if len(self.timeframes) > 1:
            tf_str = ", ".join(self.timeframes)
            return f"Confluence across {len(self.timeframes)} Timeframes ({tf_str})"
        return f"Single Timeframe ({self.timeframes[0]})"


@dataclass
class TickerOpportunityAggregate:
    """Unified aggregate opportunity for a single instrument."""
    instrument_id: str
    ticker: str
    clean_symbol: str
    sector: str
    primary_opportunity: OpportunityVersion
    all_opportunities: list[OpportunityVersion]
    peak_merit_score: float
    avg_merit_score: float
    active_horizons: list[str]
    horizon_count: int
    directional_confluence: str  # e.g. '▲ LONG (3 Timeframes Aligned)', '▼ SHORT (2 Timeframes)', '⚡ CONFLICTED'
    primary_direction: str  # 'long', 'short', or 'neutral'
    triggered_signals: list[TriggeredSignalGroup]
    active_setups: list[str]
    setup_count: int
    is_flagged: bool
    breakoutprop_eligible: bool
    personal_eligible: bool
    as_of: datetime
    trigger_price: float | None
    invalidation_price: float | None
    target_2r: float | None
    target_3r: float | None
    reconciled_direction: str = "long"
    is_conflicted: bool = False
    htf_dominant_direction: str | None = None
    reconciliation_summary: str = ""
    is_counter_trend_warning: bool = False
    weighted_long_conviction: float = 0.0
    weighted_short_conviction: float = 0.0
    latest_price: float | None = None
    price_drift_pct: float | None = None
    current_executable_rr: float | None = None
    merit_delta: float | None = None


def get_latest_active_opportunities(
    session: Session,
    include_superseded: bool = False,
    status_filter: str = "active",  # "active", "invalidated", or "all"
) -> list[OpportunityVersion]:
    """Fetch only the latest revision of opportunities according to status_filter.

    Deduplicates scans where multiple revisions exist for the same opportunity_id,
    returning only the record with max(revision).
    """
    now = utc_now()

    # Subquery for maximum revision per opportunity_id
    subq = (
        session.query(
            OpportunityVersion.opportunity_id,
            func.max(OpportunityVersion.revision).label("max_rev"),
        )
        .group_by(OpportunityVersion.opportunity_id)
        .subquery()
    )

    query = (
        session.query(OpportunityVersion)
        .join(
            subq,
            (OpportunityVersion.opportunity_id == subq.c.opportunity_id)
            & (OpportunityVersion.revision == subq.c.max_rev),
        )
    )

    superseded_val = getattr(CandidateLifecycle, "SUPERSEDED", "superseded")
    if hasattr(superseded_val, "value"):
        superseded_val = superseded_val.value

    inv_val = getattr(CandidateLifecycle, "INVALIDATED", "invalidated")
    if hasattr(inv_val, "value"):
        inv_val = inv_val.value

    if status_filter == "invalidated":
        query = query.filter(
            OpportunityVersion.lifecycle_status.in_([inv_val, "invalidated"]),
            OpportunityVersion.direction.in_(["long", "short"]),
        )
    elif status_filter == "active":
        query = query.filter(
            OpportunityVersion.lifecycle_status.notin_([superseded_val, inv_val, "superseded", "invalidated"]),
            (OpportunityVersion.expires_at == None) | (OpportunityVersion.expires_at > now),
            OpportunityVersion.direction.in_(["long", "short"]),
            OpportunityVersion.trigger_price != OpportunityVersion.invalidation_price,
        )
    elif not include_superseded:
        query = query.filter(
            OpportunityVersion.lifecycle_status != superseded_val,
            (OpportunityVersion.expires_at == None) | (OpportunityVersion.expires_at > now),
            OpportunityVersion.direction.in_(["long", "short"]),
            OpportunityVersion.trigger_price != OpportunityVersion.invalidation_price,
        )

    opportunities = (
        query.order_by(
            OpportunityVersion.merit_score.desc().nullslast(),
            OpportunityVersion.created_at.desc(),
        )
        .all()
    )

    # Secondary deduplication: if multiple distinct opportunity_ids exist for the exact same
    # (instrument_id, direction, horizon) due to legacy timestamped IDs, keep the newest one
    deduped_map: dict[tuple[str, str, str], OpportunityVersion] = {}
    for opp in opportunities:
        d = (opp.direction or "").lower()
        if d not in ("long", "short"):
            continue
        if opp.trigger_price is None or opp.invalidation_price is None:
            continue
        if abs(opp.trigger_price - opp.invalidation_price) < 1e-4:
            continue
        key = (opp.instrument_id, d, opp.horizon)
        if key not in deduped_map:
            deduped_map[key] = opp
        else:
            existing = deduped_map[key]
            # Keep whichever has the newer as_of or higher revision
            if (opp.as_of or opp.created_at) > (existing.as_of or existing.created_at):
                deduped_map[key] = opp

    return sorted(
        deduped_map.values(),
        key=lambda o: (o.merit_score or 0.0, o.created_at),
        reverse=True,
    )


def clean_ticker_name(instrument_id: str) -> str:
    """Format instrument_id into a clean, institutional ticker string."""
    raw = instrument_id.split(":")[1] if ":" in instrument_id else instrument_id
    if "perpetual" in instrument_id.lower() or "bybit" in instrument_id.lower():
        clean = raw.replace("_USDT_USDT", "/USDT").replace("_USDT", "/USDT")
        return clean
    return raw


def extract_all_systems(opp: OpportunityVersion) -> list[tuple[str, str]]:
    """Extract all institutional strategy systems referenced in thesis, playbook, or signals."""
    breakdown = opp.score_breakdown or {}
    signals = breakdown.get("signals", [])
    text = f"{opp.playbook_name} {opp.thesis or ''} {' '.join(signals)}".lower()

    systems: list[tuple[str, str]] = []
    if any(k in text for k in ["vcei", "squeeze", "compression"]):
        systems.append(("Volatility Squeeze", "VCEI"))
    if any(k in text for k in ["shla", "sweep", "orderblock", "range_break"]):
        systems.append(("Liquidity Sweeps", "SHLA"))
    if any(k in text for k in ["avia", "volume_profile", "poc", "auction"]):
        systems.append(("Volume Profile", "AVIA"))
    if any(k in text for k in ["tcmb", "trend_cloud", "ripster", "trend_pullback"]):
        systems.append(("Trend Clouds", "TCMB"))
    if any(k in text for k in ["avwap", "anchored_vwap", "vwap"]):
        systems.append(("Anchored VWAP", "AVWAP"))
    if any(k in text for k in ["nern", "lorentzian", "knn", "ml"]):
        systems.append(("Machine Learning (KNN)", "NERN"))
    if any(k in text for k in ["ifvg", "fvg", "imbalance", "bisi", "sibi"]):
        systems.append(("Fair Value Gaps", "IFVG"))
    if any(k in text for k in ["dref", "mayne", "dealing_range", "sfp", "equilibrium"]):
        systems.append(("Dealing Range SFP (Mayne)", "DREF"))

    if not systems:
        clean_name = opp.playbook_name.replace("playbook_", "").replace("_", " ").title()
        systems.append((f"Playbook Setup ({clean_name})", "SYSTEM"))

    return systems


def parse_system_acronym(setup_name: str) -> tuple[str, str]:
    """Return institutional system name and acronym for a single setup name."""
    s = setup_name.lower()
    if "vcei" in s or "squeeze" in s or "compression" in s:
        return "Volatility Squeeze", "VCEI"
    elif "shla" in s or "sweep" in s or "orderblock" in s or "range_break" in s:
        return "Liquidity Sweeps", "SHLA"
    elif "avia" in s or "volume_profile" in s or "poc" in s:
        return "Volume Profile", "AVIA"
    elif "tcmb" in s or "trend_cloud" in s or "ripster" in s or "trend_pullback" in s:
        return "Trend Clouds", "TCMB"
    elif "avwap" in s or "anchored_vwap" in s or "vwap" in s:
        return "Anchored VWAP", "AVWAP"
    elif "nern" in s or "lorentzian" in s or "knn" in s:
        return "Machine Learning (KNN)", "NERN"
    elif "ifvg" in s or "fvg" in s or "imbalance" in s or "bisi" in s or "sibi" in s:
        return "Fair Value Gaps", "IFVG"
    elif "dref" in s or "mayne" in s or "dealing_range" in s or "sfp" in s or "equilibrium" in s:
        return "Dealing Range SFP (Mayne)", "DREF"
    return setup_name.replace("_", " ").title(), "SYSTEM"


def build_where_why_how(
    opp: OpportunityVersion,
    latest_price: float | None = None,
) -> tuple[str, str, str, float | None, float | None, float | None, float | None]:
    """Generate structured Where, Why, and How breakdowns for an opportunity."""
    entry = opp.trigger_price
    stop = opp.invalidation_price
    direction = (opp.direction or "long").lower()
    is_invalidated = opp.lifecycle_status in ("invalidated", CandidateLifecycle.INVALIDATED.value)

    target_2r = None
    target_3r = None
    risk_dist = None
    risk_pct = None

    valid_geometry = (
        entry is not None
        and stop is not None
        and entry > 0
        and abs(entry - stop) >= 1e-4
    )

    if valid_geometry:
        dist = abs(entry - stop)
        risk_dist = dist
        risk_pct = (dist / entry) * 100
        if direction == "long":
            target_2r = entry + 2.0 * dist
            target_3r = entry + 3.0 * dist
        else:
            target_2r = entry - 2.0 * dist
            target_3r = entry - 3.0 * dist

    system_name, acronym = parse_system_acronym(opp.playbook_name)
    breakdown = dict(opp.score_breakdown or {})
    live_telem = breakdown.get("live_tracking", {})
    lp = latest_price or live_telem.get("latest_price")
    drift_str = ""
    if lp and valid_geometry and entry:
        drift_pct = live_telem.get("drift_pct", ((lp - entry) / entry) * 100)
        curr_rr = live_telem.get("current_rr")
        sign = "+" if drift_pct >= 0 else ""
        if curr_rr is not None:
            drift_str = f" | Live: \\${lp:.4f} ({sign}{drift_pct:.2f}% drift | Current R:R: {curr_rr:.1f}R vs 2.0R)"
        else:
            drift_str = f" | Live: \\${lp:.4f} ({sign}{drift_pct:.2f}% drift)"

    trigger_dt = opp.as_of or opp.created_at
    trigger_time_str = trigger_dt.strftime("%H:%M UTC on %Y-%m-%d") if trigger_dt else "—"

    # WHERE: Price levels and risk geometry (escaped \$ to prevent KaTeX italics glitch)
    if is_invalidated:
        where_summary = (
            f"BUSTED / INVALIDATED: {opp.counter_evidence or 'Price breached structural invalidation stop level.'} "
            f"(Triggered at {trigger_time_str} at \\${entry:.4f}, Invalidation Stop was \\${stop:.4f})"
        )
    elif valid_geometry and risk_dist is not None and risk_pct is not None and target_2r is not None and target_3r is not None:
        if acronym == "DREF":
            if direction == "long":
                where_summary = (
                    f"Dealing Range Low Reclaim (Triggered at {trigger_time_str} at \\${entry:.4f}) | Invalidation Stop: \\${stop:.4f} (Deviation Wick) | "
                    f"Target 1 (50% EQ): \\${target_2r:.4f} | Target 2 (Range High): \\${target_3r:.4f} "
                    f"(Risk: \\${risk_dist:.4f} / {risk_pct:.2f}%{drift_str})"
                )
            else:
                where_summary = (
                    f"Dealing Range High Reclaim (Triggered at {trigger_time_str} at \\${entry:.4f}) | Invalidation Stop: \\${stop:.4f} (Deviation Wick) | "
                    f"Target 1 (50% EQ): \\${target_2r:.4f} | Target 2 (Range Low): \\${target_3r:.4f} "
                    f"(Risk: \\${risk_dist:.4f} / {risk_pct:.2f}%{drift_str})"
                )
        elif acronym == "IFVG":
            where_summary = (
                f"FVG Retest Entry (Triggered at {trigger_time_str} at \\${entry:.4f}) | Invalidation Stop: \\${stop:.4f} | "
                f"2R Target: \\${target_2r:.4f} | 3R Target: \\${target_3r:.4f} "
                f"(Consequent Encroachment / CE Defended | Risk: \\${risk_dist:.4f} / {risk_pct:.2f}%{drift_str})"
            )
        else:
            where_summary = (
                f"Trigger Entry: \\${entry:.4f} (Triggered at {trigger_time_str}) | Invalidation Stop: \\${stop:.4f} | "
                f"2R Target: \\${target_2r:.4f} | 3R Target: \\${target_3r:.4f} "
                f"(Risk: \\${risk_dist:.4f} / {risk_pct:.2f}%{drift_str})"
            )
    else:
        where_summary = "Invalid Risk Geometry: Invalidation stop must be distinct from trigger entry."

    # WHY: Structural catalyst, indicators, and thesis (cleaned of raw exchange syntax)
    breakdown = opp.score_breakdown or {}
    tech_signals = breakdown.get("signals", [])
    clean_ticker = clean_ticker_name(opp.instrument_id)

    raw_thesis = opp.thesis or ""
    import re
    if ":" in raw_thesis:
        raw_thesis = re.sub(r"[a-zA-Z0-9_]+:[a-zA-Z0-9_:]+", clean_ticker, raw_thesis)
    raw_thesis = re.sub(r"Entry trigger:.*", "", raw_thesis, flags=re.IGNORECASE).strip()
    raw_thesis = raw_thesis.replace("candidate", "setup")
    for word in [
        "volume_profile_breakout", "momentum_divergence", "volatility_squeeze",
        "orderblock_sweep", "trend_pullback", "range_break", "broad_ev_discovery",
        "fvg_retest_continuation", "dealing_range_sfp_reclaim", "luxalgo_liquidity_sweep",
        "squeeze_pro_expansion", "ripster_cloud_pullback", "lorentzian_ml_prediction"
    ]:
        raw_thesis = raw_thesis.replace(word, word.replace("_", " ").title())

    why_reasons = []
    if raw_thesis:
        why_reasons.append(raw_thesis)
    if tech_signals:
        clean_sigs = [s.replace("_", " ").title() for s in tech_signals]
        why_reasons.append(f"Confirmed by: {', '.join(clean_sigs)}.")
    if opp.counter_evidence:
        why_reasons.append(f"Cautionary note: {opp.counter_evidence}")

    why_summary = " ".join(why_reasons) if why_reasons else f"{acronym} strategy criteria satisfied on {opp.horizon} timeframe."

    # HOW: Execution rules and trade management (escaped \$ for clean text flow)
    if is_invalidated:
        how_summary = "Trade setup is invalidated. Invalidation stop was breached; do not execute order."
    elif not valid_geometry or target_2r is None or target_3r is None:
        how_summary = "Execution rules unavailable due to invalid risk geometry (entry trigger and invalidation stop must be distinct)."
    elif acronym == "DREF":
        if direction == "long":
            how_summary = (
                f"Enter long on candle body close reclaiming Range Low at \\${entry:.4f} (Triggered at {trigger_time_str}). "
                f"Place hard stop loss below the deviation sweep wick at \\${stop:.4f}. "
                f"Take 50% profit at 50% Equilibrium (EQ at \\${target_2r:.4f}) and advance stop to breakeven, "
                f"trailing remainder towards Range High (\\${target_3r:.4f})."
            )
        else:
            how_summary = (
                f"Enter short on candle body close reclaiming Range High at \\${entry:.4f} (Triggered at {trigger_time_str}). "
                f"Place hard stop loss above the deviation sweep wick at \\${stop:.4f}. "
                f"Take 50% profit at 50% Equilibrium (EQ at \\${target_2r:.4f}) and advance stop to breakeven, "
                f"trailing remainder towards Range Low (\\${target_3r:.4f})."
            )
    elif acronym == "IFVG":
        how_summary = (
            f"Enter {direction} on candle test of the unmitigated Fair Value Gap at \\${entry:.4f} (Triggered at {trigger_time_str}). "
            f"Place hard stop loss beyond the FVG boundary candle at \\${stop:.4f}. "
            f"Defend 50% Consequent Encroachment (CE). Scale 50% off at 2R (\\${target_2r:.4f}) and trail remaining "
            f"stop to breakeven towards 3R (\\${target_3r:.4f})."
        )
    elif direction == "long":
        how_summary = (
            f"Enter long on candle close or limit order at \\${entry:.4f} if price structure holds above stop "
            f"(Triggered at {trigger_time_str}). "
            f"Place hard stop loss at \\${stop:.4f}. Scale 50% off at 2R (\\${target_2r:.4f}) and trail remaining stop "
            f"to breakeven towards 3R (\\${target_3r:.4f})."
        )
    else:
        how_summary = (
            f"Enter short on candle close or limit order at \\${entry:.4f} if price structure remains capped below stop "
            f"(Triggered at {trigger_time_str}). "
            f"Place hard stop loss at \\${stop:.4f}. Scale 50% off at 2R (\\${target_2r:.4f}) and trail remaining stop "
            f"to breakeven towards 3R (\\${target_3r:.4f})."
        )

    return where_summary, why_summary, how_summary, target_2r, target_3r, risk_dist, risk_pct


def how_summary_or_default(how_str: str, opp: OpportunityVersion) -> str:
    """Helper to ensure execution rules are actionable."""
    if how_str:
        return how_str
    return f"Execute {opp.direction.upper()} on {opp.horizon} according to standard risk parameters."


def aggregate_opportunities_by_ticker(
    opportunities: list[OpportunityVersion],
    watchlist_ids: set[str] | None = None,
    sector_lookup: dict[str, str] | None = None,
    exclude_sub_hourly: bool = False,
) -> list[TickerOpportunityAggregate]:
    watchlist_ids = watchlist_ids or set()
    sector_lookup = sector_lookup or {}

    # Strictly filter for actionable directional opportunities with valid risk geometry
    opportunities = [
        o for o in opportunities
        if (o.direction or "").lower() in ("long", "short")
        and o.trigger_price is not None
        and o.invalidation_price is not None
        and abs(o.trigger_price - o.invalidation_price) >= 1e-4
    ]

    if exclude_sub_hourly:
        opportunities = [o for o in opportunities if o.horizon not in ("15m", "5m", "1m")]

    horizon_order = {"15m": 1, "1h": 2, "4h": 3, "1d": 4, "1w": 5, "1M": 6}

    # Group by instrument_id
    grouped: dict[str, list[OpportunityVersion]] = defaultdict(list)
    for opp in opportunities:
        grouped[opp.instrument_id].append(opp)

    aggregates: list[TickerOpportunityAggregate] = []

    for inst_id, opp_list in grouped.items():
        # Sort opportunities for this ticker by merit descending
        sorted_opps = sorted(
            opp_list,
            key=lambda o: (o.merit_score or 0.0, o.confidence_score),
            reverse=True,
        )
        primary_opp = sorted_opps[0]

        ticker = clean_ticker_name(inst_id)
        clean_sym = inst_id.split(":")[1] if ":" in inst_id else inst_id

        # Peak and average merit
        valid_merits = [o.merit_score for o in sorted_opps if o.merit_score is not None]
        peak_merit = max(valid_merits) if valid_merits else 0.0
        avg_merit = sum(valid_merits) / len(valid_merits) if valid_merits else 0.0

        # Unique horizons sorted
        unique_horizons = sorted(
            list(set(o.horizon for o in sorted_opps)),
            key=lambda h: horizon_order.get(h, 99),
        )

        # Multi-timeframe trend & conflict reconciliation
        TIMEFRAME_WEIGHTS = {
            "1M": 6.0,
            "1w": 5.0,
            "1d": 4.0,
            "4h": 2.5,
            "1h": 1.5,
            "15m": 0.6,
            "5m": 0.3,
            "1m": 0.1,
        }
        htf_horizons = {"1d", "4h", "1w", "1M"}

        long_opps = [o for o in sorted_opps if o.direction.lower() == "long"]
        short_opps = [o for o in sorted_opps if o.direction.lower() == "short"]

        weighted_long = sum(TIMEFRAME_WEIGHTS.get(o.horizon, 1.0) * (o.merit_score or 50.0) for o in long_opps)
        weighted_short = sum(TIMEFRAME_WEIGHTS.get(o.horizon, 1.0) * (o.merit_score or 50.0) for o in short_opps)

        htf_longs = [o for o in long_opps if o.horizon in htf_horizons]
        htf_shorts = [o for o in short_opps if o.horizon in htf_horizons]

        is_conflicted = len(long_opps) > 0 and len(short_opps) > 0
        htf_dominant = None
        is_counter_trend_warning = False

        if not is_conflicted:
            if len(long_opps) > 0:
                reconciled_direction = "long"
                primary_direction = "long"
                htf_dominant = "long" if htf_longs else None
                confluence = f"▲ LONG ({len(unique_horizons)} Timeframes Aligned)" if len(unique_horizons) > 1 else f"▲ LONG ({unique_horizons[0]})"
                reconciliation_summary = f"Unanimous bullish alignment across {', '.join(unique_horizons)}."
            elif len(short_opps) > 0:
                reconciled_direction = "short"
                primary_direction = "short"
                htf_dominant = "short" if htf_shorts else None
                confluence = f"▼ SHORT ({len(unique_horizons)} Timeframes Aligned)" if len(unique_horizons) > 1 else f"▼ SHORT ({unique_horizons[0]})"
                reconciliation_summary = f"Unanimous bearish alignment across {', '.join(unique_horizons)}."
            else:
                reconciled_direction = "neutral"
                primary_direction = "neutral"
                confluence = "— NEUTRAL"
                reconciliation_summary = "Neutral market structure."
        else:
            # Conflicted signals: Evaluate HTF Seniority and Weighted Conviction
            if htf_longs and not htf_shorts:
                htf_dominant = "long"
                reconciled_direction = "long"
                primary_direction = "long"
                is_counter_trend_warning = True
                short_tfs = ", ".join(sorted(set(o.horizon for o in short_opps), key=lambda h: horizon_order.get(h, 99)))
                confluence = f"▲ LONG (HTF Seniority — {short_tfs} Pullback Caution)"
                reconciliation_summary = (
                    f"Higher timeframe (Daily/4H) establishes dominant BULLISH trend structure. "
                    f"Opposing short setup on {short_tfs} represents a local intraday pullback / scalp, "
                    f"not a macro reversal. Core execution priority remains LONG."
                )
            elif htf_shorts and not htf_longs:
                htf_dominant = "short"
                reconciled_direction = "short"
                primary_direction = "short"
                is_counter_trend_warning = True
                long_tfs = ", ".join(sorted(set(o.horizon for o in long_opps), key=lambda h: horizon_order.get(h, 99)))
                confluence = f"▼ SHORT (HTF Seniority — {long_tfs} Bounce Caution)"
                reconciliation_summary = (
                    f"Higher timeframe (Daily/4H) establishes dominant BEARISH trend structure. "
                    f"Opposing long setup on {long_tfs} represents a local intraday bounce / scalp, "
                    f"not a macro reversal. Core execution priority remains SHORT."
                )
            else:
                # Both or neither have HTF setups: Evaluate weighted mass
                if weighted_long >= weighted_short * 1.25:
                    reconciled_direction = "long"
                    primary_direction = "long"
                    is_counter_trend_warning = True
                    confluence = f"▲ LONG (Weighted Conviction: {int(weighted_long)} vs {int(weighted_short)})"
                    reconciliation_summary = f"Long setups carry superior weighted conviction ({int(weighted_long)} pts) over short pullbacks ({int(weighted_short)} pts)."
                elif weighted_short >= weighted_long * 1.25:
                    reconciled_direction = "short"
                    primary_direction = "short"
                    is_counter_trend_warning = True
                    confluence = f"▼ SHORT (Weighted Conviction: {int(weighted_short)} vs {int(weighted_long)})"
                    reconciliation_summary = f"Short setups carry superior weighted conviction ({int(weighted_short)} pts) over long bounces ({int(weighted_long)} pts)."
                else:
                    reconciled_direction = primary_opp.direction.lower()
                    primary_direction = primary_opp.direction.lower()
                    confluence = f"⚡ CONFLICTED ({len(long_opps)} Long, {len(short_opps)} Short — Range Friction)"
                    reconciliation_summary = (
                        "Evenly matched opposing pressures across timeframes. High probability of range chop; "
                        "wait for directional breakout or trade small intraday scalps only."
                    )

        # Build triggered signal groups
        # Group by (system_acronym, direction)
        signal_groups: dict[tuple[str, str], TriggeredSignalGroup] = {}

        for opp in sorted_opps:
            where_str, why_str, how_str, t2, t3, r_dist, r_pct = build_where_why_how(opp)

            detail = TimeframeDetail(
                horizon=opp.horizon,
                merit_score=opp.merit_score,
                confidence_score=opp.confidence_score,
                trigger_price=opp.trigger_price,
                invalidation_price=opp.invalidation_price,
                target_2r=t2,
                target_3r=t3,
                risk_distance=r_dist,
                risk_pct=r_pct,
                where_summary=where_str,
                why_summary=why_str,
                how_summary=how_summary_or_default(how_str, opp),
                as_of=opp.as_of or opp.created_at,
                opportunity_id=opp.opportunity_id,
                opportunity_version=opp,
            )

            systems = extract_all_systems(opp)
            for system_name, acronym in systems:
                key = (acronym, opp.direction.lower())
                if key not in signal_groups:
                    signal_groups[key] = TriggeredSignalGroup(
                        setup_name=system_name,
                        system_acronym=acronym,
                        direction=opp.direction.lower(),
                        timeframes=[opp.horizon],
                        timeframe_details={opp.horizon: detail},
                    )
                else:
                    grp = signal_groups[key]
                    if opp.horizon not in grp.timeframes:
                        grp.timeframes.append(opp.horizon)
                        grp.timeframes.sort(key=lambda h: horizon_order.get(h, 99))
                    grp.timeframe_details[opp.horizon] = detail

        # List of active setup systems
        active_systems = list(dict.fromkeys(g.setup_name for g in signal_groups.values()))

        # Watchlist check
        is_flagged = any(
            o.opportunity_id in watchlist_ids or inst_id in watchlist_ids
            for o in sorted_opps
        )

        # Eligibility check for primary candidate
        breakoutprop_eligible = (
            "crypto" in inst_id.lower() or "bybit" in inst_id.lower()
        )
        personal_eligible = True

        # Targets & risk geometry for primary candidate
        _, _, _, p_t2, p_t3, _, _ = build_where_why_how(primary_opp)

        sector = sector_lookup.get(inst_id, "Market Asset")

        # Live tracking and drift extraction
        p_sb = primary_opp.score_breakdown or {}
        p_live = p_sb.get("live_tracking", {})
        latest_px = p_live.get("latest_price")
        drift_p = p_live.get("drift_pct")
        exec_rr = p_live.get("current_rr")
        merit_d = p_sb.get("merit_delta")

        aggregates.append(
            TickerOpportunityAggregate(
                instrument_id=inst_id,
                ticker=ticker,
                clean_symbol=clean_sym,
                sector=sector,
                primary_opportunity=primary_opp,
                all_opportunities=sorted_opps,
                peak_merit_score=peak_merit,
                avg_merit_score=round(avg_merit, 1),
                active_horizons=unique_horizons,
                horizon_count=len(unique_horizons),
                directional_confluence=confluence,
                primary_direction=primary_direction,
                triggered_signals=list(signal_groups.values()),
                active_setups=active_systems,
                setup_count=len(sorted_opps),
                is_flagged=is_flagged,
                breakoutprop_eligible=breakoutprop_eligible,
                personal_eligible=personal_eligible,
                as_of=primary_opp.as_of or primary_opp.created_at,
                trigger_price=primary_opp.trigger_price,
                invalidation_price=primary_opp.invalidation_price,
                target_2r=p_t2,
                target_3r=p_t3,
                reconciled_direction=reconciled_direction,
                is_conflicted=is_conflicted,
                htf_dominant_direction=htf_dominant,
                reconciliation_summary=reconciliation_summary,
                is_counter_trend_warning=is_counter_trend_warning,
                weighted_long_conviction=round(weighted_long, 1),
                weighted_short_conviction=round(weighted_short, 1),
                latest_price=latest_px,
                price_drift_pct=drift_p,
                current_executable_rr=exec_rr,
                merit_delta=merit_d,
            )
        )

    # Rank aggregates by peak merit score descending
    return sorted(aggregates, key=lambda a: a.peak_merit_score, reverse=True)
