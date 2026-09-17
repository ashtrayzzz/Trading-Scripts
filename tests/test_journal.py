"""Tests for journaling, fill idempotency, and atomic risk reservations."""

from src.core.enums import DecisionType
from src.core.models import OpportunityVersion
from src.core.time import utc_now
from src.journal.decisions import JournalService


def test_fill_import_idempotency(db_session):
    """
    PRIORITY VALIDATION SCENARIO:
    Repeated fill import preserves stable totals without double counting.
    """
    service = JournalService()
    account_id = "test_acc"
    ext_id = "ext_fill_12345"

    fill1 = service.import_fill(
        session=db_session,
        account_id=account_id,
        external_fill_id=ext_id,
        instrument_id="bybit:BTCUSDT:perpetual",
        side="buy",
        quantity=0.5,
        price=50000.0,
        fee=2.5,
    )

    # Re-import identical fill
    fill2 = service.import_fill(
        session=db_session,
        account_id=account_id,
        external_fill_id=ext_id,
        instrument_id="bybit:BTCUSDT:perpetual",
        side="buy",
        quantity=0.5,
        price=50000.0,
        fee=2.5,
    )

    assert fill1.id == fill2.id


def test_trade_plan_and_atomic_risk_reservation(db_session):
    """
    PRIORITY VALIDATION SCENARIO:
    Creating a trade plan atomically reserves committed risk budget.
    """
    service = JournalService()

    # Record decision
    dec = service.record_decision(
        session=db_session,
        opportunity_version_id="opp_v1",
        decision=DecisionType.ACCEPTED,
        reason="Approved by user",
    )

    # Create plan with 0.1 BTC from $50,000 to $49,000 stop (risk = $100)
    plan, res = service.create_trade_plan(
        session=db_session,
        decision_id=dec.id,
        account_id="bp_test_10k",
        instrument_id="bybit:BTCUSDT:perpetual",
        direction="long",
        entry_price=50000.0,
        stop_loss=49000.0,
        quantity=0.1,
    )

    assert plan.id is not None
    assert res.id is not None
    assert res.plan_id == plan.id
    assert abs(res.reserved_risk_usd - 100.0) < 1e-4
    assert res.status == "reserved"


def test_outcome_evaluation_r_multiple(db_session):
    """Verify outcome calculation and R-multiple metrics."""
    service = JournalService()
    dec = service.record_decision(db_session, "opp_v2", DecisionType.ACCEPTED)
    plan, _ = service.create_trade_plan(
        session=db_session,
        decision_id=dec.id,
        account_id="test_acc",
        instrument_id="bybit:BTCUSDT:perpetual",
        direction="long",
        entry_price=50000.0,
        stop_loss=49000.0,
        quantity=0.1,  # Risk is $100
    )

    # Trade won $250 (+2.5R)
    outcome = service.record_outcome(
        session=db_session,
        plan_id=plan.id,
        net_pnl=250.0,
        exit_reason="target_hit",
        max_favorable_excursion=300.0,
        max_adverse_excursion=-30.0,
        duration_bars=12,
    )

    assert outcome.net_pnl == 250.0
    assert outcome.r_multiple == 2.5


def test_update_decision_commentary(db_session):
    """Verify human review commentary can be edited and persisted."""
    service = JournalService()
    dec = service.record_decision(
        session=db_session,
        opportunity_version_id="opp_v3",
        decision=DecisionType.ACCEPTED,
        reason="Initial notes",
    )

    updated = service.update_decision_commentary(
        session=db_session,
        decision_id=dec.id,
        new_reason="Updated: Exiting half at 2R, trailing stop to break-even",
    )

    assert updated is not None
    assert updated.reason == "Updated: Exiting half at 2R, trailing stop to break-even"


def test_delete_decision_cascades_safely(db_session):
    """Verify deleting a decision removes associated trade plan and risk reservation."""
    service = JournalService()
    dec = service.record_decision(
        session=db_session,
        opportunity_version_id="opp_v4",
        decision=DecisionType.ACCEPTED,
        reason="To be deleted",
    )
    plan, res = service.create_trade_plan(
        session=db_session,
        decision_id=dec.id,
        account_id="bp_test_10k",
        instrument_id="bybit:BTCUSDT:perpetual",
        direction="long",
        entry_price=50000.0,
        stop_loss=49000.0,
        quantity=0.1,
    )

    success = service.delete_decision(session=db_session, decision_id=dec.id)
    assert success is True

    # Ensure decision, plan, and reservation are gone
    from src.core.models import DecisionRecord, TradePlan, RiskReservation
    assert db_session.query(DecisionRecord).filter_by(id=dec.id).first() is None
    assert db_session.query(TradePlan).filter_by(id=plan.id).first() is None
    assert db_session.query(RiskReservation).filter_by(id=res.id).first() is None

