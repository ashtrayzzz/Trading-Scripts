"""Sequential account eligibility evaluation engine."""

from datetime import datetime, timedelta
from typing import Any
from sqlalchemy.orm import Session

from src.accounts.base import AccountState, BaseAccount
from src.core.enums import (
    AssessmentStage,
    EligibilityResult,
    QualityStatus,
    RiskState,
)
from src.core.models import EligibilitySnapshot, OpportunityVersion
from src.core.time import utc_now


class AccountEligibilityEngine:
    """
    Evaluates opportunities against an account profile in strict sequence:
    1. Verified rules & fresh state
    2. Instrument permissions
    3. Data quality
    4. Remaining headroom
    5. Existing & pending exposure
    6. Proposed trade scenario sizing
    7. Suitability & risk fit
    """

    def evaluate(
        self,
        account: BaseAccount,
        opportunity: OpportunityVersion,
        state: AccountState,
        stage: AssessmentStage = AssessmentStage.PRELIMINARY,
        as_of: datetime | None = None,
    ) -> EligibilitySnapshot:
        """Evaluate an opportunity candidate against an account."""
        now = utc_now()
        reasons: list[str] = []

        # Gate 1: Verified rules & fresh state
        # In prototype/early stage, accounts are marked unverified, which is noted
        if account.verification_status != "verified":
            reasons.append(f"rules_unverified_{account.verification_status}")

        # Gate 2: Instrument permissions
        inst_id = opportunity.instrument_id
        if "bybit" in inst_id and account.account_type == "breakoutprop":
            # Allowed as proxy
            pass
        elif "yahoo" in inst_id and account.account_type == "breakoutprop":
            # Breakoutprop doesn't trade arbitrary equities
            return self._build_snapshot(
                account, opportunity, stage, EligibilityResult.BLOCKED,
                ["instrument_type_not_permitted_for_account"] + reasons, None, None, None, now
            )

        # Gate 3: Data quality
        quality_status = getattr(opportunity, "quality_status", None) or QualityStatus.VALID.value
        if quality_status != QualityStatus.VALID.value:
            return self._build_snapshot(
                account, opportunity, stage, EligibilityResult.UNKNOWN,
                [f"opportunity_data_quality_{quality_status}"] + reasons, None, None, None, now
            )

        # Gate 4: Remaining headroom & risk state
        risk_state, state_reasons = account.get_operating_risk_state(state)
        if risk_state == RiskState.PAUSED:
            return self._build_snapshot(
                account, opportunity, stage, EligibilityResult.BLOCKED,
                state_reasons + ["account_risk_state_paused"] + reasons, None, None, None, now
            )

        # Gate 5: Sizing & Scenario calculation
        sizing = account.evaluate_sizing_and_risk(opportunity, state)
        if not sizing.allowed:
            return self._build_snapshot(
                account, opportunity, stage, EligibilityResult.BLOCKED,
                sizing.reason_codes + reasons, None, None, None, now
            )

        # If all gates pass -> ELIGIBLE
        final_result = EligibilityResult.ELIGIBLE
        all_reasons = reasons + sizing.reason_codes

        return self._build_snapshot(
            account=account,
            opportunity=opportunity,
            stage=stage,
            result=final_result,
            reason_codes=all_reasons,
            suggested_size=sizing.suggested_quantity,
            scenario_loss=sizing.estimated_risk_usd,
            dd_consumption=sizing.drawdown_consumption_pct,
            now=now,
            details={
                "notional_value": sizing.notional_value,
                "currency": account.currency,
                "risk_state": risk_state.value,
            },
        )

    def _build_snapshot(
        self,
        account: BaseAccount,
        opportunity: OpportunityVersion,
        stage: AssessmentStage,
        result: EligibilityResult,
        reason_codes: list[str],
        suggested_size: float | None,
        scenario_loss: float | None,
        dd_consumption: float | None,
        now: datetime,
        details: dict[str, Any] | None = None,
    ) -> EligibilitySnapshot:
        """Construct an immutable EligibilitySnapshot record."""
        opp_id = getattr(opportunity, "id", None) or getattr(opportunity, "opportunity_id", "opp_unknown")
        return EligibilitySnapshot(
            opportunity_version_id=opp_id,
            account_id=account.account_id,
            stage=stage.value,
            result=result.value,
            reason_codes=reason_codes,
            suggested_position_size=suggested_size,
            estimated_scenario_loss=scenario_loss,
            drawdown_consumption_pct=dd_consumption,
            details=details or {},
            producer="eligibility_engine",
            producer_version="0.1.0",
            as_of=now,
            available_at=now,
            quality_status=QualityStatus.VALID.value,
            expires_at=opportunity.expires_at,
        )
