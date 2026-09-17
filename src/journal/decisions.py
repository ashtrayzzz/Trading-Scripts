"""Decision, plan, fill, and outcome journaling service."""

from datetime import datetime
from sqlalchemy.orm import Session

from src.core.enums import DecisionType, QualityStatus
from src.core.models import (
    DecisionRecord,
    FillRecord,
    OutcomeRecord,
    RiskReservation,
    TradePlan,
)
from src.core.time import utc_now


class JournalService:
    """Manages audit trail from opportunity decisions to trade plans and recorded fills."""

    def record_decision(
        self,
        session: Session,
        opportunity_version_id: str,
        decision: DecisionType | str,
        reason: str = "",
        eligibility_snapshot_id: str | None = None,
        actor: str = "user",
    ) -> DecisionRecord:
        """Record human review decision."""
        now = utc_now()
        dec_val = decision.value if isinstance(decision, DecisionType) else decision

        record = DecisionRecord(
            opportunity_version_id=opportunity_version_id,
            eligibility_snapshot_id=eligibility_snapshot_id,
            decision=dec_val,
            reason=reason,
            actor=actor,
            producer="human_interface",
            producer_version="0.1.0",
            as_of=now,
            available_at=now,
            quality_status=QualityStatus.VALID.value,
        )
        session.add(record)
        session.commit()
        return record

    def update_decision_commentary(
        self,
        session: Session,
        decision_id: str,
        new_reason: str,
    ) -> DecisionRecord | None:
        """Update commentary on an existing human decision record."""
        record = session.query(DecisionRecord).filter_by(id=decision_id).first()
        if not record:
            return None
        record.reason = new_reason
        session.commit()
        return record

    def delete_decision(
        self,
        session: Session,
        decision_id: str,
    ) -> bool:
        """
        Delete a human decision record and safely release any associated unexecuted
        trade plans and risk reservations.
        """
        record = session.query(DecisionRecord).filter_by(id=decision_id).first()
        if not record:
            return False

        # Find linked trade plans
        plans = session.query(TradePlan).filter_by(decision_id=decision_id).all()
        for p in plans:
            # Delete or cancel risk reservations
            reservations = session.query(RiskReservation).filter_by(plan_id=p.id).all()
            for r in reservations:
                session.delete(r)
            session.delete(p)

        session.delete(record)
        session.commit()
        return True

    def create_trade_plan(
        self,
        session: Session,
        decision_id: str,
        account_id: str,
        instrument_id: str,
        direction: str,
        entry_price: float,
        stop_loss: float,
        quantity: float,
        target_price: float | None = None,
    ) -> tuple[TradePlan, RiskReservation]:
        """
        Create immutable trade plan and atomically reserve risk budget.
        """
        now = utc_now()
        risk_per_unit = abs(entry_price - stop_loss)
        estimated_risk_usd = risk_per_unit * quantity

        plan = TradePlan(
            decision_id=decision_id,
            account_id=account_id,
            instrument_id=instrument_id,
            direction=direction,
            entry_price=entry_price,
            stop_loss=stop_loss,
            target_price=target_price,
            quantity=quantity,
            estimated_risk_usd=estimated_risk_usd,
            status="ready",
            producer="plan_builder",
            producer_version="0.1.0",
            as_of=now,
            available_at=now,
            quality_status=QualityStatus.VALID.value,
        )
        session.add(plan)
        session.flush()

        # Atomic risk reservation
        reservation = RiskReservation(
            account_id=account_id,
            plan_id=plan.id,
            reserved_risk_usd=estimated_risk_usd,
            status="reserved",
            producer="risk_engine",
            producer_version="0.1.0",
            as_of=now,
            available_at=now,
            quality_status=QualityStatus.VALID.value,
        )
        session.add(reservation)
        session.commit()

        return plan, reservation

    def import_fill(
        self,
        session: Session,
        account_id: str,
        external_fill_id: str,
        instrument_id: str,
        side: str,
        quantity: float,
        price: float,
        fee: float = 0.0,
        plan_id: str | None = None,
    ) -> FillRecord:
        """
        Import trade fill idempotently. If external fill ID exists, return existing.
        """
        existing = (
            session.query(FillRecord)
            .filter_by(account_id=account_id, external_fill_id=external_fill_id)
            .first()
        )
        if existing:
            return existing

        now = utc_now()
        fill = FillRecord(
            account_id=account_id,
            external_fill_id=external_fill_id,
            instrument_id=instrument_id,
            plan_id=plan_id,
            side=side,
            quantity=quantity,
            price=price,
            fee=fee,
            fill_time=now,
            producer="fill_importer",
            producer_version="0.1.0",
            as_of=now,
            available_at=now,
            quality_status=QualityStatus.VALID.value,
        )
        session.add(fill)
        session.commit()
        return fill

    def record_outcome(
        self,
        session: Session,
        plan_id: str,
        net_pnl: float,
        exit_reason: str,
        max_favorable_excursion: float | None = None,
        max_adverse_excursion: float | None = None,
        duration_bars: int | None = None,
    ) -> OutcomeRecord:
        """Record trade outcome metrics."""
        now = utc_now()
        plan = session.query(TradePlan).filter_by(id=plan_id).first()
        initial_risk = plan.estimated_risk_usd if plan and plan.estimated_risk_usd > 0 else None
        r_mult = round(net_pnl / initial_risk, 2) if initial_risk else None

        outcome = OutcomeRecord(
            plan_id=plan_id,
            is_actual=True,
            net_pnl=net_pnl,
            r_multiple=r_mult,
            max_favorable_excursion=max_favorable_excursion,
            max_adverse_excursion=max_adverse_excursion,
            duration_bars=duration_bars,
            exit_reason=exit_reason,
            producer="outcome_evaluator",
            producer_version="0.1.0",
            as_of=now,
            available_at=now,
            quality_status=QualityStatus.VALID.value,
        )
        session.add(outcome)
        session.commit()
        return outcome
