"""Abstract base account definition."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from src.core.enums import AccountType, RiskState, VerificationStatus
from src.core.models import OpportunityVersion


@dataclass
class AccountState:
    """Current dynamic account state snapshot."""
    equity: float
    balance: float
    currency: str = "USD"
    open_positions: list[dict[str, Any]] = field(default_factory=list)
    pending_orders: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class SizingResult:
    """Calculated position size and risk metrics for a trade candidate."""
    allowed: bool
    suggested_quantity: float
    notional_value: float
    estimated_risk_usd: float
    drawdown_consumption_pct: float
    reason_codes: list[str] = field(default_factory=list)


class BaseAccount(ABC):
    """Abstract interface for all accounts."""

    def __init__(
        self,
        account_id: str,
        account_type: AccountType,
        name: str,
        currency: str = "USD",
        verification_status: VerificationStatus = VerificationStatus.UNVERIFIED_WEB_RESEARCH,
    ):
        self.account_id = account_id
        self.account_type = account_type
        self.name = name
        self.currency = currency
        self.verification_status = verification_status

    @abstractmethod
    def get_operating_risk_state(self, state: AccountState) -> tuple[RiskState, list[str]]:
        """Calculate the current operating risk state (normal/reduced/paused/unknown)."""
        pass

    @abstractmethod
    def evaluate_sizing_and_risk(
        self,
        opportunity: OpportunityVersion,
        state: AccountState,
    ) -> SizingResult:
        """Calculate allowed position size, scenario loss, and risk impact."""
        pass
