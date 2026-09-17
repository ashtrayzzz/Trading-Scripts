"""SQLAlchemy models implementing system data contracts with immutable versioning."""

from datetime import datetime
from typing import Any
import uuid

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from src.core.enums import (
    AssessmentStage,
    CandidateLifecycle,
    DecisionType,
    EligibilityResult,
    InstrumentType,
    QualityStatus,
    RiskState,
    SignalDirection,
    VerificationStatus,
)
from src.core.time import utc_now


def generate_uuid(prefix: str = "") -> str:
    """Generate a prefixed UUID string."""
    u = uuid.uuid4().hex
    return f"{prefix}_{u}" if prefix else u


class Base(DeclarativeBase):
    """Base class for all models."""
    pass


class EnvelopeMixin:
    """Shared envelope fields required on all versioned and derived records."""
    schema_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    producer: Mapped[str] = mapped_column(String(64), nullable=False)
    producer_version: Mapped[str] = mapped_column(String(32), default="0.1.0", nullable=False)
    run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    input_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    quality_status: Mapped[str] = mapped_column(String(24), default=QualityStatus.VALID.value, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class InstrumentRecord(Base, EnvelopeMixin):
    """Canonical instrument specifications."""
    __tablename__ = "instruments"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    asset_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    venue: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    symbol: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    instrument_type: Mapped[str] = mapped_column(String(24), default=InstrumentType.PERPETUAL.value, nullable=False)
    base_currency: Mapped[str] = mapped_column(String(16), nullable=False)
    quote_currency: Mapped[str] = mapped_column(String(16), nullable=False)
    contract_multiplier: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    tick_size: Mapped[float] = mapped_column(Float, default=0.01, nullable=False)
    lot_size: Mapped[float] = mapped_column(Float, default=0.001, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    extra_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class MarketObservation(Base, EnvelopeMixin):
    """Canonical OHLCV bar observations. Unique per instrument/interval/open_time."""
    __tablename__ = "market_observations"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: generate_uuid("obs"))
    instrument_id: Mapped[str] = mapped_column(String(64), ForeignKey("instruments.id"), nullable=False, index=True)
    interval: Mapped[str] = mapped_column(String(16), nullable=False)
    open_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    close_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    open: Mapped[float] = mapped_column(Float, nullable=False)
    high: Mapped[float] = mapped_column(Float, nullable=False)
    low: Mapped[float] = mapped_column(Float, nullable=False)
    close: Mapped[float] = mapped_column(Float, nullable=False)
    volume: Mapped[float] = mapped_column(Float, nullable=False)
    quote_volume: Mapped[float | None] = mapped_column(Float, nullable=True)
    trades_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_closed: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        Index("idx_market_obs_inst_int_time", "instrument_id", "interval", "open_time", unique=True),
    )


class FeatureSnapshot(Base, EnvelopeMixin):
    """Deterministic feature values computed from closed bars."""
    __tablename__ = "feature_snapshots"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: generate_uuid("feat"))
    instrument_id: Mapped[str] = mapped_column(String(64), ForeignKey("instruments.id"), nullable=False, index=True)
    horizon: Mapped[str] = mapped_column(String(16), nullable=False)
    feature_version: Mapped[str] = mapped_column(String(32), default="0.1.0", nullable=False)
    feature_values: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    source_bar_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)

    __table_args__ = (
        Index("idx_feature_snapshot_inst_horizon_asof", "instrument_id", "horizon", "as_of"),
    )


class ModuleSignal(Base, EnvelopeMixin):
    """Setup signals emitted by technical scanners."""
    __tablename__ = "module_signals"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: generate_uuid("sig"))
    module_name: Mapped[str] = mapped_column(String(32), nullable=False)
    setup_name: Mapped[str] = mapped_column(String(64), nullable=False)
    instrument_id: Mapped[str] = mapped_column(String(64), ForeignKey("instruments.id"), nullable=False, index=True)
    direction: Mapped[str] = mapped_column(String(16), default=SignalDirection.LONG.value, nullable=False)
    horizon: Mapped[str] = mapped_column(String(16), nullable=False)
    trigger_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    invalidation_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    conditions: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    feature_snapshot_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)


class OpportunityVersion(Base, EnvelopeMixin):
    """Ranked opportunity candidate revision."""
    __tablename__ = "opportunity_versions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: generate_uuid("oppv"))
    opportunity_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    playbook_name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    instrument_id: Mapped[str] = mapped_column(String(64), ForeignKey("instruments.id"), nullable=False, index=True)
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    horizon: Mapped[str] = mapped_column(String(16), nullable=False)
    lifecycle_status: Mapped[str] = mapped_column(String(24), default=CandidateLifecycle.SCORED.value, nullable=False)
    merit_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence_score: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    thesis: Mapped[str] = mapped_column(Text, nullable=False)
    counter_evidence: Mapped[str | None] = mapped_column(Text, nullable=True)
    trigger_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    invalidation_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    score_breakdown: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    signal_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)

    __table_args__ = (
        Index("idx_oppv_opp_rev", "opportunity_id", "revision", unique=True),
    )


class AccountRuleVersion(Base, EnvelopeMixin):
    """Account rules and policies governing risk."""
    __tablename__ = "account_rule_versions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: generate_uuid("arv"))
    account_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    account_type: Mapped[str] = mapped_column(String(32), nullable=False)
    product_stage: Mapped[str] = mapped_column(String(64), nullable=False)
    base_currency: Mapped[str] = mapped_column(String(16), default="USD", nullable=False)
    max_drawdown_floor: Mapped[float] = mapped_column(Float, nullable=False)
    daily_loss_limit: Mapped[float] = mapped_column(Float, nullable=False)
    operational_buffer: Mapped[float] = mapped_column(Float, default=50.0, nullable=False)
    per_trade_risk_limit: Mapped[float] = mapped_column(Float, default=100.0, nullable=False)
    leverage_map: Mapped[dict[str, float]] = mapped_column(JSON, default=dict, nullable=False)
    verification_status: Mapped[str] = mapped_column(String(32), default=VerificationStatus.UNVERIFIED_WEB_RESEARCH.value, nullable=False)


class AccountSnapshot(Base, EnvelopeMixin):
    """Point-in-time account state."""
    __tablename__ = "account_snapshots"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: generate_uuid("asnap"))
    account_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    equity: Mapped[float] = mapped_column(Float, nullable=False)
    balance: Mapped[float] = mapped_column(Float, nullable=False)
    currency: Mapped[str] = mapped_column(String(16), default="USD", nullable=False)
    open_positions: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    pending_orders: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    source: Mapped[str] = mapped_column(String(32), default="manual", nullable=False)


class RiskStateSnapshot(Base, EnvelopeMixin):
    """Calculated operating risk state for an account."""
    __tablename__ = "risk_state_snapshots"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: generate_uuid("rstate"))
    account_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    account_snapshot_id: Mapped[str] = mapped_column(String(64), ForeignKey("account_snapshots.id"), nullable=False)
    risk_state: Mapped[str] = mapped_column(String(24), default=RiskState.NORMAL.value, nullable=False)
    current_headroom: Mapped[float] = mapped_column(Float, nullable=False)
    daily_loss_headroom: Mapped[float] = mapped_column(Float, nullable=False)
    used_risk_budget: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    remaining_risk_budget: Mapped[float] = mapped_column(Float, nullable=False)
    reason_codes: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)


class EligibilitySnapshot(Base, EnvelopeMixin):
    """Result of evaluating an opportunity against an account's risk rules."""
    __tablename__ = "eligibility_snapshots"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: generate_uuid("elig"))
    opportunity_version_id: Mapped[str] = mapped_column(String(64), ForeignKey("opportunity_versions.id"), nullable=False, index=True)
    account_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    stage: Mapped[str] = mapped_column(String(24), default=AssessmentStage.PRELIMINARY.value, nullable=False)
    result: Mapped[str] = mapped_column(String(24), default=EligibilityResult.ELIGIBLE.value, nullable=False)
    reason_codes: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    suggested_position_size: Mapped[float | None] = mapped_column(Float, nullable=True)
    estimated_scenario_loss: Mapped[float | None] = mapped_column(Float, nullable=True)
    drawdown_consumption_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class DecisionRecord(Base, EnvelopeMixin):
    """Human review decision on an opportunity."""
    __tablename__ = "decisions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: generate_uuid("dec"))
    opportunity_version_id: Mapped[str] = mapped_column(String(64), ForeignKey("opportunity_versions.id"), nullable=False, index=True)
    eligibility_snapshot_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("eligibility_snapshots.id"), nullable=True)
    decision: Mapped[str] = mapped_column(String(24), default=DecisionType.ACCEPTED.value, nullable=False)
    reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    actor: Mapped[str] = mapped_column(String(64), default="user", nullable=False)


class TradePlan(Base, EnvelopeMixin):
    """Immutable trade plan created when a candidate is accepted."""
    __tablename__ = "trade_plans"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: generate_uuid("plan"))
    decision_id: Mapped[str] = mapped_column(String(64), ForeignKey("decisions.id"), nullable=False, index=True)
    account_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    instrument_id: Mapped[str] = mapped_column(String(64), ForeignKey("instruments.id"), nullable=False)
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    stop_loss: Mapped[float] = mapped_column(Float, nullable=False)
    target_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    quantity: Mapped[float] = mapped_column(Float, nullable=False)
    estimated_risk_usd: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="ready", nullable=False)


class RiskReservation(Base, EnvelopeMixin):
    """Atomic risk budget reservation for a ready trade plan."""
    __tablename__ = "risk_reservations"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: generate_uuid("res"))
    account_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    plan_id: Mapped[str] = mapped_column(String(64), ForeignKey("trade_plans.id"), nullable=False, index=True)
    reserved_risk_usd: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="reserved", nullable=False)  # reserved, released, converted


class FillRecord(Base, EnvelopeMixin):
    """Executed fill imported or entered."""
    __tablename__ = "fills"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: generate_uuid("fill"))
    account_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    external_fill_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    instrument_id: Mapped[str] = mapped_column(String(64), ForeignKey("instruments.id"), nullable=False)
    plan_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("trade_plans.id"), nullable=True)
    side: Mapped[str] = mapped_column(String(16), nullable=False)
    quantity: Mapped[float] = mapped_column(Float, nullable=False)
    price: Mapped[float] = mapped_column(Float, nullable=False)
    fee: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    fee_currency: Mapped[str] = mapped_column(String(16), default="USD", nullable=False)
    fill_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    __table_args__ = (
        Index("idx_fill_account_external_id", "account_id", "external_fill_id", unique=True),
    )


class OutcomeRecord(Base, EnvelopeMixin):
    """Outcome evaluation for a trade plan or opportunity."""
    __tablename__ = "outcomes"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: generate_uuid("outc"))
    opportunity_version_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("opportunity_versions.id"), nullable=True)
    plan_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("trade_plans.id"), nullable=True)
    is_actual: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    net_pnl: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    r_multiple: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_favorable_excursion: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_adverse_excursion: Mapped[float | None] = mapped_column(Float, nullable=True)
    duration_bars: Mapped[int | None] = mapped_column(Integer, nullable=True)
    exit_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)


class JobRunRecord(Base, EnvelopeMixin):
    """Audit log of pipeline job runs."""
    __tablename__ = "job_runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: generate_uuid("job"))
    job_name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(24), default="running", nullable=False)
    records_processed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
