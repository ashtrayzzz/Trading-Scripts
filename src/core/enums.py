"""Core enums defined by data contracts and system architecture."""

from enum import StrEnum


class QualityStatus(StrEnum):
    """Quality of ingested or derived data."""
    VALID = "valid"
    DEGRADED = "degraded"
    UNKNOWN = "unknown"
    QUARANTINED = "quarantined"
    EXPIRED = "expired"


class RiskState(StrEnum):
    """Operating risk state for an account."""
    NORMAL = "normal"
    REDUCED = "reduced"
    PAUSED = "paused"
    UNKNOWN = "unknown"


class AssessmentStage(StrEnum):
    """Stage of risk/eligibility assessment."""
    PRELIMINARY = "preliminary"  # Screening without concrete trade plan
    FINAL = "final"              # Atomic check against immutable trade plan


class EligibilityResult(StrEnum):
    """Result of an account eligibility gate."""
    ELIGIBLE = "eligible"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"


class DecisionType(StrEnum):
    """Human review decision."""
    ACCEPTED = "accepted"
    DEFERRED = "deferred"
    REJECTED = "rejected"


class CandidateLifecycle(StrEnum):
    """Lifecycle of an opportunity candidate."""
    DETECTED = "detected"
    INCOMPLETE = "incomplete"
    SCORED = "scored"
    ACTIVE = "active"
    INVALIDATED = "invalidated"
    EXPIRED = "expired"
    SUPERSEDED = "superseded"


class InstrumentType(StrEnum):
    """Financial instrument classification."""
    SPOT = "spot"
    PERPETUAL = "perpetual"
    FUTURE = "future"
    INDEX = "index"
    EQUITY = "equity"
    ETF = "etf"


class SignalDirection(StrEnum):
    """Directional bias of a module signal."""
    LONG = "long"
    SHORT = "short"
    NEUTRAL_CONTEXT = "neutral_context"


class VerificationStatus(StrEnum):
    """Trust/verification provenance of rules and accounts."""
    VERIFIED = "verified"
    UNVERIFIED_WEB_RESEARCH = "unverified_web_research"
    UNVERIFIED_USER_INPUT = "unverified_user_input"


class AccountType(StrEnum):
    """Account profile archetype."""
    BREAKOUTPROP = "breakoutprop"
    PERSONAL = "personal"


class DataSourceType(StrEnum):
    """Vendor / source connector."""
    BYBIT = "bybit"
    YAHOO_FINANCE = "yahoo_finance"
    POLYGON = "polygon"
    IBKR = "ibkr"


class PlaybookMode(StrEnum):
    """Scanner evaluation mode."""
    DISCOVERY = "discovery"
    FILTERED = "filtered"


class TimeInterval(StrEnum):
    """Standard bar intervals."""
    M15 = "15m"
    H1 = "1h"
    H4 = "4h"
    D1 = "1d"
    W1 = "1w"
    M1 = "1M"
