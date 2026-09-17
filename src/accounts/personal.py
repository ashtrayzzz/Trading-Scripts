"""Generic Personal Account archetype (covering Wealthsimple, IBKR, and others)."""

from typing import Any
from src.accounts.base import AccountState, BaseAccount, SizingResult
from src.core.enums import AccountType, RiskState, VerificationStatus
from src.core.models import OpportunityVersion


class PersonalAccount(BaseAccount):
    """
    Generic personal account engine (Wealthsimple, IBKR, personal crypto).
    
    Invariants:
    - User-defined risk policy: per-trade risk %, aggregate risk %, leverage cap.
    - No proprietary firm drawdown floors unless explicitly configured.
    """

    def __init__(
        self,
        account_id: str,
        name: str,
        currency: str = "USD",
        per_trade_risk_pct: float = 1.0,  # 1% risk per trade
        max_aggregate_risk_pct: float = 6.0,
        max_positions: int = 5,
        leverage_cap: float = 1.0,  # 1x for cash accounts, 2x-4x for margin
        verification_status: VerificationStatus = VerificationStatus.UNVERIFIED_USER_INPUT,
    ):
        super().__init__(
            account_id=account_id,
            account_type=AccountType.PERSONAL,
            name=name,
            currency=currency,
            verification_status=verification_status,
        )
        self.per_trade_risk_pct = per_trade_risk_pct
        self.max_aggregate_risk_pct = max_aggregate_risk_pct
        self.max_positions = max_positions
        self.leverage_cap = leverage_cap

    def get_operating_risk_state(self, state: AccountState) -> tuple[RiskState, list[str]]:
        """Calculate operating state based on current open positions."""
        reasons = []
        if len(state.open_positions) >= self.max_positions:
            reasons.append("max_open_positions_reached")
            return RiskState.PAUSED, reasons

        return RiskState.NORMAL, ["normal_operations"]

    def evaluate_sizing_and_risk(
        self,
        opportunity: OpportunityVersion,
        state: AccountState,
    ) -> SizingResult:
        """Calculate allowed size based on user's percentage risk model."""
        risk_state, reasons = self.get_operating_risk_state(state)
        if risk_state == RiskState.PAUSED:
            return SizingResult(
                allowed=False,
                suggested_quantity=0.0,
                notional_value=0.0,
                estimated_risk_usd=0.0,
                drawdown_consumption_pct=0.0,
                reason_codes=reasons,
            )

        entry = opportunity.trigger_price
        stop = opportunity.invalidation_price
        if not entry or not stop or entry <= 0:
            return SizingResult(
                allowed=False,
                suggested_quantity=0.0,
                notional_value=0.0,
                estimated_risk_usd=0.0,
                drawdown_consumption_pct=0.0,
                reason_codes=["missing_entry_or_stop_price"],
            )

        stop_distance = abs(entry - stop)
        stop_dist_pct = stop_distance / entry

        if stop_dist_pct <= 0.0001:
            return SizingResult(
                allowed=False,
                suggested_quantity=0.0,
                notional_value=0.0,
                estimated_risk_usd=0.0,
                drawdown_consumption_pct=0.0,
                reason_codes=["stop_loss_too_tight"],
            )

        # Target risk amount: equity * per_trade_risk_pct
        target_risk = state.equity * (self.per_trade_risk_pct / 100.0)
        calculated_notional = target_risk / stop_dist_pct

        # Leverage constraint
        max_allowed_notional = state.equity * self.leverage_cap
        final_notional = min(calculated_notional, max_allowed_notional)
        final_quantity = final_notional / entry
        actual_risk = final_notional * stop_dist_pct

        size_reasons = []
        if calculated_notional > max_allowed_notional:
            size_reasons.append("position_size_capped_by_leverage_limit")

        return SizingResult(
            allowed=True,
            suggested_quantity=round(final_quantity, 4),
            notional_value=round(final_notional, 2),
            estimated_risk_usd=round(actual_risk, 2),
            drawdown_consumption_pct=round((actual_risk / state.equity) * 100.0, 1),
            reason_codes=size_reasons + ["sized_per_personal_risk_policy"],
        )
