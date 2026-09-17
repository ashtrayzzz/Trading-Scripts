"""Candidate opportunity assembly and versioning."""

from datetime import datetime
from collections import defaultdict
import uuid
from sqlalchemy.orm import Session

from src.core.enums import CandidateLifecycle, QualityStatus, SignalDirection
from src.core.models import FeatureSnapshot, MarketObservation, ModuleSignal, OpportunityVersion, OutcomeRecord
from src.core.time import interval_to_timedelta, utc_now
from src.opportunities.lifecycle import OpportunityLifecycleManager
from src.opportunities.scoring import OpportunityScorer


class OpportunityAssembler:
    """Assembles and scores versioned opportunities from module signals."""

    def __init__(self, scorer: OpportunityScorer | None = None):
        self.scorer = scorer or OpportunityScorer()
        self.lifecycle_mgr = OpportunityLifecycleManager()

    def assemble_opportunities(
        self,
        session: Session,
        playbook_name: str,
        horizon: str,
        as_of: datetime | None = None,
    ) -> list[OpportunityVersion]:
        """
        Group active signals, evaluate existing setups for invalidation, and create or revise OpportunityVersions.
        """
        now = utc_now()
        cutoff = as_of or now

        # 1. Run lifecycle audit to detect stop loss breaches or target completions on live bars
        self.lifecycle_mgr.evaluate_active_opportunities(session, horizon=horizon, as_of=cutoff)

        # 2. Fetch active, non-expired signals for this horizon
        active_signals = (
            session.query(ModuleSignal)
            .filter(
                ModuleSignal.horizon == horizon,
                (ModuleSignal.expires_at == None) | (ModuleSignal.expires_at > cutoff),
            )
            .all()
        )

        # Group by (instrument_id, direction) strictly for actionable directional trades
        grouped: dict[tuple[str, str], list[ModuleSignal]] = defaultdict(list)
        for sig in active_signals:
            d = (sig.direction or "").lower()
            if d not in (SignalDirection.LONG.value, SignalDirection.SHORT.value, "long", "short"):
                # Neutral context / background anomalies are non-actionable features, not trade setups
                continue
            grouped[(sig.instrument_id, d)].append(sig)

        # 3. Detect dissolved setups: active opportunities that no longer triggered on the latest candle
        active_previous = (
            session.query(OpportunityVersion)
            .filter(
                OpportunityVersion.horizon == horizon,
                OpportunityVersion.lifecycle_status.in_([
                    CandidateLifecycle.SCORED.value,
                    CandidateLifecycle.ACTIVE.value,
                    "scored",
                    "active",
                ]),
                (OpportunityVersion.expires_at == None) | (OpportunityVersion.expires_at > cutoff),
            )
            .all()
        )
        for prev in active_previous:
            key = (prev.instrument_id, prev.direction.lower())
            if key not in grouped:
                latest_bar = (
                    session.query(MarketObservation)
                    .filter(
                        MarketObservation.instrument_id == prev.instrument_id,
                        MarketObservation.interval == prev.horizon,
                    )
                    .order_by(MarketObservation.close_time.desc())
                    .first()
                )
                if latest_bar and latest_bar.close_time > prev.as_of:
                    prev.lifecycle_status = CandidateLifecycle.INVALIDATED.value
                    prev.counter_evidence = (
                        f"Setup invalidated: Technical confluence criteria dissolved on candle "
                        f"{latest_bar.close_time.strftime('%Y-%m-%d %H:%M UTC')}."
                    )
                    sb = dict(prev.score_breakdown or {})
                    sb["invalidation_telemetry"] = {
                        "status": "invalidated",
                        "reason": "criteria_dissolved",
                        "breach_price": round(latest_bar.close, 4),
                        "breach_time": latest_bar.close_time.isoformat(),
                        "bars_held": 1,
                        "exit_r": 0.0,
                        "initial_merit": prev.merit_score,
                        "evaluated_at": now.isoformat(),
                    }
                    prev.score_breakdown = sb

                    existing_outcome = (
                        session.query(OutcomeRecord)
                        .filter_by(opportunity_version_id=prev.id, is_actual=False)
                        .first()
                    )
                    if not existing_outcome:
                        outcome = OutcomeRecord(
                            opportunity_version_id=prev.id,
                            is_actual=False,
                            net_pnl=0.0,
                            r_multiple=0.0,
                            max_favorable_excursion=0.0,
                            max_adverse_excursion=0.0,
                            duration_bars=1,
                            exit_reason="criteria_dissolved",
                            producer="lifecycle_manager",
                            as_of=latest_bar.close_time,
                            available_at=now,
                            quality_status=QualityStatus.VALID.value,
                        )
                        session.add(outcome)

        created_versions: list[OpportunityVersion] = []

        for (inst_id, direction), sigs in grouped.items():
            primary_sig = sigs[0]

            # Fetch corresponding feature snapshot
            feat_snap_id = primary_sig.feature_snapshot_ids[0] if primary_sig.feature_snapshot_ids else None
            features = session.query(FeatureSnapshot).filter_by(id=feat_snap_id).first() if feat_snap_id else None

            # Enforce Risk Geometry Invariants:
            entry_px = primary_sig.trigger_price
            stop_px = primary_sig.invalidation_price
            if entry_px is None or stop_px is None or entry_px <= 0:
                continue

            dist = abs(entry_px - stop_px)
            min_dist = max(0.0005 * entry_px, 1e-4)
            is_inverted = (direction == "long" and stop_px >= entry_px) or (direction == "short" and stop_px <= entry_px)

            if dist < min_dist or is_inverted:
                fv = features.feature_values if (features and features.feature_values) else {}
                atr_val = fv.get("atr_14")
                safe_buffer = max(0.5 * (atr_val or (0.01 * entry_px)), 0.002 * entry_px)
                if direction == "long":
                    stop_px = entry_px - safe_buffer
                else:
                    stop_px = entry_px + safe_buffer
                dist = abs(entry_px - stop_px)
                primary_sig.invalidation_price = stop_px

            if dist < 1e-4:
                # Still invalid geometry; cannot assemble executable trade
                continue

            # Score the candidate
            merit, confidence, breakdown, missing = self.scorer.score_candidate(
                signals=sigs,
                features=features,
                playbook_name=playbook_name,
            )

            lifecycle = CandidateLifecycle.INCOMPLETE.value if missing else CandidateLifecycle.SCORED.value

            # Build clean thesis narrative
            from src.opportunities.aggregation import clean_ticker_name
            clean_inst = clean_ticker_name(inst_id)
            setups_str = ", ".join(s.setup_name.replace("_", " ").title() for s in sigs)
            thesis = f"{direction.upper()} setup on {clean_inst} ({horizon}) driven by {setups_str}."

            # Check for counter-evidence
            counter_evidence = None
            if features and features.feature_values:
                fv = features.feature_values
                trend = fv.get("structure_trend")
                is_sfp = any("sfp" in s.setup_name.lower() or "dealing_range" in s.setup_name.lower() for s in sigs)
                if is_sfp:
                    # SFP setups intentionally sweep range extremes before reclaiming
                    counter_evidence = None
                elif direction == "long" and trend == "downtrend_lh_ll":
                    counter_evidence = "Trading against prevailing 4H/Daily downward market structure."
                elif direction == "short" and trend == "uptrend_hh_hl":
                    counter_evidence = "Trading against prevailing 4H/Daily upward market structure."

            # Check if an existing active opportunity exists
            existing_latest = (
                session.query(OpportunityVersion)
                .filter_by(instrument_id=inst_id, direction=direction, horizon=horizon)
                .order_by(OpportunityVersion.revision.desc())
                .first()
            )

            if existing_latest:
                # Check for identical scan output on the same candle:
                same_candle = existing_latest.as_of == primary_sig.as_of
                same_prices = (
                    existing_latest.trigger_price == entry_px
                    and existing_latest.invalidation_price == stop_px
                )
                same_merit = existing_latest.merit_score == merit

                if same_candle and same_prices and same_merit:
                    # Refresh freshness metadata without inserting a redundant duplicate revision
                    existing_latest.available_at = now
                    existing_latest.expires_at = now + interval_to_timedelta(horizon) * 4
                    created_versions.append(existing_latest)
                    continue

                opp_id = existing_latest.opportunity_id
                next_rev = existing_latest.revision + 1
                merit_delta = round(merit - (existing_latest.merit_score or merit), 1)
                breakdown = dict(breakdown or {})
                breakdown["merit_delta"] = merit_delta
                # Mark previous revision superseded
                superseded_val = getattr(CandidateLifecycle, "SUPERSEDED", "superseded")
                existing_latest.lifecycle_status = superseded_val.value if hasattr(superseded_val, "value") else str(superseded_val)
            else:
                clean_sym = inst_id.split(":")[1] if ":" in inst_id else inst_id
                clean_sym = clean_sym.replace("_USDT_USDT", "USDT").replace("_USDT", "USDT").replace("/", "").replace("-", "")
                time_str = now.strftime("%Y%m%d_%H%M")
                clean_dir = direction.upper()
                base_opp_id = f"{clean_sym}_{horizon}_{clean_dir}_{time_str}"
                opp_id = base_opp_id[:64]

                existing_opp = session.query(OpportunityVersion).filter_by(opportunity_id=opp_id).first()
                if existing_opp:
                    opp_id = f"{base_opp_id[:57]}_{uuid.uuid4().hex[:6]}"
                next_rev = 1

            opp_version = OpportunityVersion(
                opportunity_id=opp_id,
                revision=next_rev,
                playbook_name=playbook_name,
                instrument_id=inst_id,
                direction=direction,
                horizon=horizon,
                lifecycle_status=lifecycle,
                merit_score=merit,
                confidence_score=confidence,
                thesis=thesis,
                counter_evidence=counter_evidence,
                trigger_price=entry_px,
                invalidation_price=stop_px,
                score_breakdown=breakdown,
                signal_ids=[str(s.id) for s in sigs],
                producer="opportunity_assembler",
                producer_version="0.1.0",
                as_of=primary_sig.as_of,
                available_at=now,
                quality_status=QualityStatus.VALID.value,
                expires_at=now + interval_to_timedelta(horizon) * 4,
            )

            session.add(opp_version)
            created_versions.append(opp_version)

        session.commit()
        return created_versions
