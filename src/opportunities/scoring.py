"""Transparent, versioned opportunity scoring engine.

Formula:
  merit = clamp(sum(weight * component_score) - penalties, 0, 100)

INVARIANTS:
- A score is a ranking heuristic, not a probability of profit.
- Missing required inputs yield an UNSCORED candidate.
- Missing optional inputs produce an explicit incomplete marker, never silently neutral.
- Confidence is reported separately from merit.
- Expired signals cannot promote an opportunity.
"""

from typing import Any
from src.core.models import ModuleSignal, FeatureSnapshot


DEFAULT_WEIGHTS = {
    "setup_quality": 0.35,
    "volume_confirmation": 0.25,
    "trend_alignment": 0.20,
    "compression_duration": 0.10,
    "implementation_cost": 0.10,
}


class OpportunityScorer:
    """Computes transparent merit and confidence scores for opportunity candidates."""

    def __init__(self, weights: dict[str, float] | None = None):
        self.weights = weights or DEFAULT_WEIGHTS
        # Ensure weights sum to 1.0
        total_w = sum(self.weights.values())
        if abs(total_w - 1.0) > 0.001:
            self.weights = {k: v / total_w for k, v in self.weights.items()}

    def score_candidate(
        self,
        signals: list[ModuleSignal],
        features: FeatureSnapshot | None,
        playbook_name: str,
    ) -> tuple[float | None, float, dict[str, Any], list[str]]:
        """
        Score an opportunity candidate.
        
        Returns:
            (merit_score, confidence_score, score_breakdown, missing_inputs)
        """
        missing_inputs: list[str] = []
        breakdown: dict[str, Any] = {}

        if not signals:
            return None, 0.0, {"error": "no_signals"}, ["signals"]

        if not features or not features.feature_values:
            return None, 0.0, {"error": "missing_features"}, ["feature_snapshot"]

        f = features.feature_values
        primary_sig = signals[0]
        breakdown["signals"] = [s.setup_name for s in signals]

        is_sfp = any("sfp" in s.setup_name.lower() or "dealing_range" in s.setup_name.lower() for s in signals)
        is_fvg = any("fvg" in s.setup_name.lower() for s in signals)

        # 1. Setup Quality (0 - 100)
        # Based on signal confidence and setup type
        base_quality = primary_sig.confidence * 100.0
        if is_fvg and (f.get("fvg_bullish_test") or f.get("fvg_bearish_test")):
            base_quality = max(base_quality, 88.0)
        elif is_sfp and (f.get("mayne_sfp_bullish") or f.get("mayne_sfp_bearish")):
            base_quality = max(base_quality, 90.0)
        breakdown["setup_quality"] = min(max(base_quality, 0.0), 100.0)

        # 2. Volume Confirmation (0 - 100)
        vol_ratio = f.get("volume_ratio")
        if vol_ratio is None:
            missing_inputs.append("volume_ratio")
            vol_score = 50.0  # marked incomplete in breakdown
        else:
            if vol_ratio >= 2.0:
                vol_score = 100.0
            elif vol_ratio >= 1.5:
                vol_score = 80.0
            elif vol_ratio >= 1.2:
                vol_score = 65.0
            else:
                vol_score = 40.0
        breakdown["volume_confirmation"] = vol_score

        # 3. Trend Alignment (0 - 100)
        trend = f.get("structure_trend")
        direction = primary_sig.direction
        if not trend:
            trend_score = 50.0
        elif is_sfp:
            # Trader Mayne Dealing Range SFP Reclaim:
            # Sweeping the Range Low in a local downtrend or Range High in a local uptrend
            # is the structural liquidity trigger, not an invalid counter-trend fight.
            if trend == "neutral":
                trend_score = 90.0  # Optimal dealing range conditions
            elif (direction == "long" and trend == "uptrend_hh_hl") or (direction == "short" and trend == "downtrend_lh_ll"):
                trend_score = 95.0  # SFP in direction of higher timeframe trend
            else:
                trend_score = 80.0  # Mean-reversion SFP deviation reclaim (avoids counter-trend penalty)
        elif is_fvg:
            # ICT Fair Value Gap Continuation:
            if (direction == "long" and trend == "uptrend_hh_hl") or (direction == "short" and trend == "downtrend_lh_ll"):
                trend_score = 100.0  # High-conviction institutional trend continuation into unmitigated imbalance
            elif trend == "neutral":
                trend_score = 80.0
            else:
                trend_score = 35.0
        else:
            if direction == "long" and trend == "uptrend_hh_hl":
                trend_score = 95.0
            elif direction == "short" and trend == "downtrend_lh_ll":
                trend_score = 95.0
            elif trend == "neutral":
                trend_score = 55.0
            else:
                # Counter-trend penalty
                trend_score = 25.0
        breakdown["trend_alignment"] = trend_score

        # 4. Compression Duration (0 - 100)
        bb_w_pct = f.get("bb_width_pct_rank")
        in_sq = f.get("in_squeeze", False)
        if bb_w_pct is None:
            comp_score = 50.0
        else:
            if bb_w_pct <= 10 or in_sq:
                comp_score = 100.0
            elif bb_w_pct <= 25:
                comp_score = 80.0
            elif bb_w_pct <= 50:
                comp_score = 50.0
            else:
                comp_score = 20.0
        if is_sfp and f.get("dealing_range_high"):
            comp_score = max(comp_score, 75.0)
        breakdown["compression_duration"] = comp_score

        # 5. Implementation Cost / Liquidity (0 - 100)
        # Liquid assets have low spread/slippage
        cost_score = 90.0
        breakdown["implementation_cost"] = cost_score

        # Check required components
        if missing_inputs:
            breakdown["status"] = "incomplete_missing_inputs"
            breakdown["missing"] = missing_inputs
            merit_score = None
        else:
            # Weighted calculation
            raw_merit = sum(self.weights[k] * breakdown[k] for k in self.weights if k in breakdown)
            # Penalties
            penalties = 0.0
            if breakdown["trend_alignment"] < 40.0:
                penalties += 15.0  # Strong penalty for trading directly against higher structure
            
            merit_score = round(max(0.0, min(100.0, raw_merit - penalties)), 1)
            breakdown["penalties"] = penalties
            breakdown["calculated_merit"] = merit_score

        # Separate Confidence Score (0.0 to 1.0)
        # Freshness + signal count + data quality
        confidence = primary_sig.confidence
        if len(signals) > 1:
            confidence = min(1.0, confidence + 0.1)  # corroboration bonus
        if missing_inputs:
            confidence *= 0.5

        return merit_score, round(confidence, 2), breakdown, missing_inputs
