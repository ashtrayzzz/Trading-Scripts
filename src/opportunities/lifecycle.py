"""Opportunity lifecycle evaluation, stop loss invalidation, and performance telemetry logging."""

from datetime import datetime
from typing import Any
from sqlalchemy import desc
from sqlalchemy.orm import Session

from src.core.enums import CandidateLifecycle, QualityStatus
from src.core.models import MarketObservation, OpportunityVersion, OutcomeRecord
from src.core.time import utc_now


class OpportunityLifecycleManager:
    """Monitors active opportunities, detects stop loss breaches, tracks MFE/MAE excursions,

    and logs telemetry to OutcomeRecord and OpportunityVersion metadata.
    """

    def __init__(self):
        pass

    def evaluate_active_opportunities(
        self,
        session: Session,
        horizon: str | None = None,
        as_of: datetime | None = None,
    ) -> dict[str, int]:
        """Audit all active opportunities against subsequent candle data.

        - If price breaches invalidation stop: transition to INVALIDATED, compute MFE/MAE,
          and log structured telemetry.
        - If price reached target 2R/3R: transition to EXPIRED (Target Achieved) to prevent FOMO.
        - Updates live drift percentage and executable R:R against latest close price.
        """
        now = utc_now()
        cutoff = as_of or now

        query = session.query(OpportunityVersion).filter(
            OpportunityVersion.lifecycle_status.in_([
                CandidateLifecycle.SCORED.value,
                CandidateLifecycle.ACTIVE.value,
                "scored",
                "active",
            ]),
            (OpportunityVersion.expires_at == None) | (OpportunityVersion.expires_at > cutoff),
        )

        if horizon:
            query = query.filter(OpportunityVersion.horizon == horizon)

        active_opps = query.all()

        counts = {
            "evaluated": len(active_opps),
            "invalidated": 0,
            "targets_hit": 0,
            "active": 0,
        }

        for opp in active_opps:
            entry = opp.trigger_price
            stop = opp.invalidation_price
            direction = (opp.direction or "long").lower()

            if entry is None or stop is None or entry <= 0:
                continue

            dist = abs(entry - stop)
            if dist < 1e-4:
                continue

            target_2r = entry + 2.0 * dist if direction == "long" else entry - 2.0 * dist
            target_3r = entry + 3.0 * dist if direction == "long" else entry - 3.0 * dist

            # Fetch chronological observations on or after opp.as_of
            obs_query = (
                session.query(MarketObservation)
                .filter(
                    MarketObservation.instrument_id == opp.instrument_id,
                    MarketObservation.interval == opp.horizon,
                    MarketObservation.close_time >= opp.as_of,
                )
                .order_by(MarketObservation.close_time.asc())
                .all()
            )

            # If no observations after opp.as_of, fetch latest available bar
            if not obs_query:
                latest_bar = (
                    session.query(MarketObservation)
                    .filter(
                        MarketObservation.instrument_id == opp.instrument_id,
                        MarketObservation.interval == opp.horizon,
                    )
                    .order_by(MarketObservation.close_time.desc())
                    .first()
                )
                if latest_bar:
                    obs_query = [latest_bar]

            if not obs_query:
                counts["active"] += 1
                continue

            latest_obs = obs_query[-1]
            latest_price = latest_obs.close

            # Compute Live Drift and Real-Time Executable R:R
            drift_pct = ((latest_price - entry) / entry) * 100
            current_risk_dist = abs(latest_price - stop)
            current_target_dist = abs(target_2r - latest_price)
            current_rr = current_target_dist / current_risk_dist if current_risk_dist > 1e-4 else 0.0

            live_tracking = {
                "latest_price": round(latest_price, 4),
                "drift_pct": round(drift_pct, 2),
                "current_risk_dist": round(current_risk_dist, 4),
                "current_target_dist": round(current_target_dist, 4),
                "current_rr": round(current_rr, 2),
                "updated_at": now.isoformat(),
            }

            # Update live tracking metadata on the opportunity
            breakdown = dict(opp.score_breakdown or {})
            breakdown["live_tracking"] = live_tracking
            opp.score_breakdown = breakdown

            # Track MFE & MAE excursion across all observations since detection
            if direction == "long":
                highest_seen = max(o.high for o in obs_query)
                lowest_seen = min(o.low for o in obs_query)
                mfe_r = (highest_seen - entry) / dist
                mae_r = (entry - lowest_seen) / dist
            else:
                highest_seen = max(o.high for o in obs_query)
                lowest_seen = min(o.low for o in obs_query)
                mfe_r = (entry - lowest_seen) / dist
                mae_r = (highest_seen - entry) / dist

            is_invalidated = False
            breach_obs = None
            breach_price = None

            is_target_hit = False
            target_obs = None

            # Chronological check for stop breach or target hit
            for idx, obs in enumerate(obs_query):
                if direction == "long":
                    if obs.low <= stop or obs.close <= stop:
                        is_invalidated = True
                        breach_obs = obs
                        breach_price = min(obs.low, stop)
                        duration_bars = idx + 1
                        break
                    elif obs.high >= target_2r:
                        is_target_hit = True
                        target_obs = obs
                        duration_bars = idx + 1
                        break
                else:
                    if obs.high >= stop or obs.close >= stop:
                        is_invalidated = True
                        breach_obs = obs
                        breach_price = max(obs.high, stop)
                        duration_bars = idx + 1
                        break
                    elif obs.low <= target_2r:
                        is_target_hit = True
                        target_obs = obs
                        duration_bars = idx + 1
                        break

            if is_invalidated and breach_obs:
                opp.lifecycle_status = CandidateLifecycle.INVALIDATED.value
                time_str = breach_obs.close_time.strftime("%Y-%m-%d %H:%M UTC")
                opp.counter_evidence = (
                    f"Setup invalidated: Price breached stop level "
                    f"(\\${breach_price:.4f} vs stop \\${stop:.4f}) at {time_str}."
                )

                # Persist detailed invalidation telemetry into score_breakdown
                invalidation_telemetry = {
                    "status": "invalidated",
                    "reason": "stop_breached",
                    "breach_price": round(breach_price, 4),
                    "breach_time": breach_obs.close_time.isoformat(),
                    "bars_held": duration_bars,
                    "mfe_price": round(highest_seen if direction == "long" else lowest_seen, 4),
                    "mfe_r": round(max(mfe_r, 0.0), 2),
                    "mae_price": round(lowest_seen if direction == "long" else highest_seen, 4),
                    "mae_r": round(max(mae_r, 0.0), 2),
                    "exit_r": -1.0,
                    "initial_merit": opp.merit_score,
                    "initial_risk_pct": round((dist / entry) * 100, 2),
                    "evaluated_at": now.isoformat(),
                }
                breakdown["invalidation_telemetry"] = invalidation_telemetry
                opp.score_breakdown = breakdown

                # Check if an OutcomeRecord already exists for this opportunity
                existing_outcome = (
                    session.query(OutcomeRecord)
                    .filter_by(opportunity_version_id=opp.id, is_actual=False)
                    .first()
                )
                if not existing_outcome:
                    outcome = OutcomeRecord(
                        opportunity_version_id=opp.id,
                        is_actual=False,
                        net_pnl=0.0,
                        r_multiple=-1.0,
                        max_favorable_excursion=round(max(mfe_r, 0.0), 2),
                        max_adverse_excursion=round(max(mae_r, 0.0), 2),
                        duration_bars=duration_bars,
                        exit_reason="stop_breached",
                        producer="lifecycle_manager",
                        as_of=breach_obs.close_time,
                        available_at=now,
                        quality_status=QualityStatus.VALID.value,
                    )
                    session.add(outcome)
                counts["invalidated"] += 1

            elif is_target_hit and target_obs:
                opp.lifecycle_status = CandidateLifecycle.EXPIRED.value
                time_str = target_obs.close_time.strftime("%Y-%m-%d %H:%M UTC")
                opp.counter_evidence = (
                    f"Target 2R reached (\\${target_2r:.4f}) at {time_str}; "
                    f"trade opportunity completed its run."
                )

                target_telemetry = {
                    "status": "target_achieved",
                    "reason": "target_2r_hit",
                    "exit_price": round(target_2r, 4),
                    "exit_time": target_obs.close_time.isoformat(),
                    "bars_held": duration_bars,
                    "mfe_r": 2.0,
                    "mae_r": round(max(mae_r, 0.0), 2),
                    "exit_r": 2.0,
                    "initial_merit": opp.merit_score,
                    "initial_risk_pct": round((dist / entry) * 100, 2),
                    "evaluated_at": now.isoformat(),
                }
                breakdown["invalidation_telemetry"] = target_telemetry
                opp.score_breakdown = breakdown

                existing_outcome = (
                    session.query(OutcomeRecord)
                    .filter_by(opportunity_version_id=opp.id, is_actual=False)
                    .first()
                )
                if not existing_outcome:
                    outcome = OutcomeRecord(
                        opportunity_version_id=opp.id,
                        is_actual=False,
                        net_pnl=0.0,
                        r_multiple=2.0,
                        max_favorable_excursion=2.0,
                        max_adverse_excursion=round(max(mae_r, 0.0), 2),
                        duration_bars=duration_bars,
                        exit_reason="target_2r_achieved",
                        producer="lifecycle_manager",
                        as_of=target_obs.close_time,
                        available_at=now,
                        quality_status=QualityStatus.VALID.value,
                    )
                    session.add(outcome)
                counts["targets_hit"] += 1
            else:
                counts["active"] += 1

        session.commit()
        return counts

    @staticmethod
    def get_invalidation_telemetry_logs(
        session: Session,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Fetch structured invalidation and performance telemetry logs for analysis and tuning."""
        from src.opportunities.aggregation import clean_ticker_name

        invalidated_opps = (
            session.query(OpportunityVersion)
            .filter(
                OpportunityVersion.lifecycle_status.in_([
                    CandidateLifecycle.INVALIDATED.value,
                    CandidateLifecycle.EXPIRED.value,
                    "invalidated",
                ])
            )
            .order_by(desc(OpportunityVersion.created_at))
            .limit(limit)
            .all()
        )

        logs = []
        for opp in invalidated_opps:
            sb = opp.score_breakdown or {}
            telem = sb.get("invalidation_telemetry")
            if not telem and opp.lifecycle_status != "invalidated":
                continue

            ticker = clean_ticker_name(opp.instrument_id)
            log_item = {
                "opportunity_id": opp.opportunity_id,
                "revision": opp.revision,
                "instrument_id": opp.instrument_id,
                "ticker": ticker,
                "playbook_name": opp.playbook_name,
                "horizon": opp.horizon,
                "direction": opp.direction.upper(),
                "merit_score": opp.merit_score,
                "confidence_score": opp.confidence_score,
                "trigger_price": opp.trigger_price,
                "invalidation_price": opp.invalidation_price,
                "status": opp.lifecycle_status.upper(),
                "reason": telem.get("reason", "manual_or_timeout") if telem else "stop_breached",
                "breach_price": telem.get("breach_price") if telem else opp.invalidation_price,
                "breach_time": telem.get("breach_time") if telem else (opp.expires_at.isoformat() if opp.expires_at else None),
                "bars_held": telem.get("bars_held", 0) if telem else 0,
                "mfe_r": telem.get("mfe_r", 0.0) if telem else 0.0,
                "mae_r": telem.get("mae_r", 0.0) if telem else 0.0,
                "exit_r": telem.get("exit_r", -1.0) if telem else -1.0,
                "initial_risk_pct": telem.get("initial_risk_pct") if telem else None,
                "counter_evidence": opp.counter_evidence,
                "as_of": opp.as_of.isoformat() if opp.as_of else None,
                "confluence_quality": sb.get("confluence_quality"),
                "volume_confirmation": sb.get("volume_confirmation"),
                "trend_alignment": sb.get("trend_alignment"),
                "compression_duration": sb.get("compression_duration"),
                "confluence_count": sb.get("confluence_count", 0),
            }
            logs.append(log_item)

        return logs

    def manually_invalidate_opportunity(
        self,
        session: Session,
        opportunity_id: str,
        reason: str = "manual_invalidation",
        user_notes: str | None = None,
    ) -> OpportunityVersion | None:
        """Allow a trader to manually invalidate a setup directly from the UI or CLI."""
        now = utc_now()
        opp = (
            session.query(OpportunityVersion)
            .filter(
                (OpportunityVersion.opportunity_id == opportunity_id) | (OpportunityVersion.id == opportunity_id),
                OpportunityVersion.lifecycle_status.in_([
                    CandidateLifecycle.SCORED.value,
                    CandidateLifecycle.ACTIVE.value,
                    "scored",
                    "active",
                ]),
            )
            .order_by(OpportunityVersion.revision.desc())
            .first()
        )
        if not opp:
            opp = (
                session.query(OpportunityVersion)
                .filter((OpportunityVersion.opportunity_id == opportunity_id) | (OpportunityVersion.id == opportunity_id))
                .order_by(OpportunityVersion.revision.desc())
                .first()
            )
            if not opp:
                return None

        # Fetch latest candle close
        latest_obs = (
            session.query(MarketObservation)
            .filter(
                MarketObservation.instrument_id == opp.instrument_id,
                MarketObservation.interval == opp.horizon,
            )
            .order_by(MarketObservation.close_time.desc())
            .first()
        )
        current_price = latest_obs.close if latest_obs else (opp.trigger_price or 0.0)

        entry = opp.trigger_price or current_price
        stop = opp.invalidation_price or current_price
        dist = abs(entry - stop)
        dist = max(dist, 1e-4)
        direction = (opp.direction or "long").lower()

        mfe_r = 0.0
        mae_r = 0.0
        if direction == "long":
            mfe_r = max((current_price - entry) / dist, 0.0)
            mae_r = max((entry - current_price) / dist, 0.0)
        else:
            mfe_r = max((entry - current_price) / dist, 0.0)
            mae_r = max((current_price - entry) / dist, 0.0)

        opp.lifecycle_status = CandidateLifecycle.INVALIDATED.value
        note_str = f" Notes: {user_notes}" if user_notes else ""
        opp.counter_evidence = (
            f"Manually invalidated by trader ({reason}) at {now.strftime('%Y-%m-%d %H:%M UTC')} "
            f"(Current Price: \\${current_price:.4f}).{note_str}"
        )

        sb = dict(opp.score_breakdown or {})
        telemetry = {
            "status": "invalidated",
            "reason": f"manual_{reason}" if not reason.startswith("manual") else reason,
            "breach_price": round(current_price, 4),
            "breach_time": now.isoformat(),
            "bars_held": 1,
            "mfe_price": round(current_price, 4),
            "mfe_r": round(mfe_r, 2),
            "mae_price": round(current_price, 4),
            "mae_r": round(mae_r, 2),
            "exit_r": 0.0,
            "initial_merit": opp.merit_score,
            "initial_risk_pct": round((dist / entry) * 100, 2) if entry else 0.0,
            "user_notes": user_notes,
            "evaluated_at": now.isoformat(),
        }
        sb["invalidation_telemetry"] = telemetry
        opp.score_breakdown = sb

        # Check / log OutcomeRecord
        outcome = (
            session.query(OutcomeRecord)
            .filter_by(opportunity_version_id=opp.id, is_actual=False)
            .first()
        )
        if not outcome:
            outcome = OutcomeRecord(
                opportunity_version_id=opp.id,
                is_actual=False,
                net_pnl=0.0,
                r_multiple=0.0,
                max_favorable_excursion=round(mfe_r, 2),
                max_adverse_excursion=round(mae_r, 2),
                duration_bars=1,
                exit_reason=f"manual_{reason}" if not reason.startswith("manual") else reason,
                producer="trader_manual_override",
                as_of=now,
                available_at=now,
                quality_status=QualityStatus.VALID.value,
            )
            session.add(outcome)

        session.commit()
        return opp
