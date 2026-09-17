"""Breakoutprop specific rule and constraint engine."""

from typing import Any
from src.accounts.base import AccountState, BaseAccount, SizingResult
from src.core.enums import AccountType, RiskState, VerificationStatus
from src.core.models import OpportunityVersion


DEFAULT_LEVERAGE_MAP: dict[str, float] = {
    "BTC": 10.0,
    "ETH": 5.0,
    "SOL": 5.0,
    "XRP": 5.0,
    "DOGE": 5.0,
    "DEFAULT": 5.0,
}


class BreakoutpropAccount(BaseAccount):
    """
    Breakoutprop Turbo Evaluation 10k account engine.
    
    Invariants:
    - 3% static drawdown floor ($9,700).
    - 3% daily loss limit (resets 00:30 UTC).
    - Hard pause when headroom exhausted.
    - Sizing dynamically adapts to stop distance to fit within drawdown budget.
    """

    def __init__(
        self,
        account_id: str = "bp_turbo_10k",
        name: str = "Breakoutprop Turbo 10k",
        starting_equity: float = 10000.0,
        max_drawdown_floor: float = 9700.0,
        daily_loss_limit: float = 300.0,
        operational_buffer: float = 50.0,
        per_trade_risk_limit: float = 100.0,
        leverage_map: dict[str, float] | None = None,
        verification_status: VerificationStatus = VerificationStatus.UNVERIFIED_WEB_RESEARCH,
    ):
        super().__init__(
            account_id=account_id,
            account_type=AccountType.BREAKOUTPROP,
            name=name,
            currency="USD",
            verification_status=verification_status,
        )
        self.starting_equity = starting_equity
        self.max_drawdown_floor = max_drawdown_floor
        self.daily_loss_limit = daily_loss_limit
        self.operational_buffer = operational_buffer
        self.per_trade_risk_limit = per_trade_risk_limit
        self.leverage_map = leverage_map or DEFAULT_LEVERAGE_MAP

    @property
    def total_drawdown_budget(self) -> float:
        """Total allowable drawdown from starting equity."""
        return self.starting_equity - self.max_drawdown_floor

    def calculate_headroom(self, state: AccountState, current_daily_loss: float = 0.0) -> tuple[float, float, float]:
        """
        Calculate available headroom before operational buffer and limits.
        
        Returns:
            (effective_headroom, static_dd_headroom, daily_loss_headroom)
        """
        static_dd_headroom = max(0.0, (state.equity - self.max_drawdown_floor) - self.operational_buffer)
        daily_headroom = max(0.0, (self.daily_loss_limit - current_daily_loss) - self.operational_buffer)
        effective = min(static_dd_headroom, daily_headroom)
        return effective, static_dd_headroom, daily_headroom

    def get_operating_risk_state(self, state: AccountState) -> tuple[RiskState, list[str]]:
        """Determine account operating level."""
        effective, dd_h, daily_h = self.calculate_headroom(state)
        reasons = []

        if effective <= 0.0:
            if dd_h <= 0.0:
                reasons.append("max_drawdown_floor_breached_or_buffered")
            if daily_h <= 0.0:
                reasons.append("daily_loss_limit_reached")
            return RiskState.PAUSED, reasons

        if effective < self.per_trade_risk_limit * 1.5:
            reasons.append("low_drawdown_headroom_reduced_risk")
            return RiskState.REDUCED, reasons

        return RiskState.NORMAL, ["normal_operations"]

    def evaluate_sizing_and_risk(
        self,
        opportunity: OpportunityVersion,
        state: AccountState,
    ) -> SizingResult:
        """
        Calculate allowed position sizing and risk scenario.
        
        Adapts position size to ensure scenario loss <= available headroom and per-trade limit.
        """
        risk_state, reasons = self.get_operating_risk_state(state)
        if risk_state == RiskState.PAUSED:
            return SizingResult(
                allowed=False,
                suggested_quantity=0.0,
                notional_value=0.0,
                estimated_risk_usd=0.0,
                drawdown_consumption_pct=0.0,
                reason_codes=reasons + ["account_risk_paused"],
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

        effective_headroom, _, _ = self.calculate_headroom(state)

        # Budget calculation: respect operating level
        base_budget = self.per_trade_risk_limit
        if risk_state == RiskState.REDUCED:
            base_budget *= 0.5

        # Available risk budget is bounded by remaining headroom
        target_risk_usd = min(base_budget, effective_headroom * 0.8)

        if target_risk_usd < 10.0:
            return SizingResult(
                allowed=False,
                suggested_quantity=0.0,
                notional_value=0.0,
                estimated_risk_usd=0.0,
                drawdown_consumption_pct=0.0,
                reason_codes=["insufficient_headroom_for_minimum_risk"],
            )

        # Theoretical notional: risk_usd / stop_dist_pct
        calculated_notional = target_risk_usd / stop_dist_pct

        # Check leverage limits
        asset = opportunity.instrument_id.split(":")[1].split("/")[0] if ":" in opportunity.instrument_id else "DEFAULT"
        leverage = self.leverage_map.get(asset, self.leverage_map.get("DEFAULT", 5.0))
        max_allowed_notional = state.equity * leverage

        final_notional = min(calculated_notional, max_allowed_notional)
        final_quantity = final_notional / entry
        actual_scenario_loss = final_notional * stop_dist_pct
        dd_consumption = (actual_scenario_loss / self.total_drawdown_budget) * 100.0

        size_reasons = []
        if calculated_notional > max_allowed_notional:
            size_reasons.append("position_size_capped_by_leverage_limit")

        return SizingResult(
            allowed=True,
            suggested_quantity=round(final_quantity, 4),
            notional_value=round(final_notional, 2),
            estimated_risk_usd=round(actual_scenario_loss, 2),
            drawdown_consumption_pct=round(dd_consumption, 1),
            reason_codes=size_reasons + ["size_calculated_within_drawdown_limits"],
        )
