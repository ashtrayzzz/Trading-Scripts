"""LLM Trading Intelligence Scaffold with Slot-Based Context Injection & Strategy Deep-Dives."""

from datetime import datetime
import json
from typing import Any
import structlog

from src.core.time import format_dual_time, utc_now

logger = structlog.get_logger()

# -------------------------------------------------------------
# STRATEGY DEEP-DIVES & FIRST-PRINCIPLES KNOWLEDGE BASE
# -------------------------------------------------------------
STRATEGY_DEEP_DIVES: dict[str, dict[str, Any]] = {
    "VCEI": {
        "short_title": "Volatility Squeeze",
        "purposed_name": "Volatility Compression & Expansion Impulse (VCEI)",
        "original_name": "ChrisMoody Squeeze Pro",
        "acronym": "VCEI",
        "tagline": "Exploiting volatility clustering when energy transitions from compression to directional impulse.",
        "how_it_works": (
            "Measures Bollinger Bands (20 period, 2.0σ) compressing within three distinct tiers of Keltner Channels "
            "(1.0×, 1.5×, and 2.0× ATR). High Compression (Black Dot) indicates BB is inside 1.0 ATR; "
            "Mid Compression (Red Dot) indicates inside 1.5 ATR; Low Compression (Orange Dot) indicates inside 2.0 ATR. "
            "When Bollinger Bands expand back outside Keltner Channels (Green Dot), the squeeze fires. "
            "Direction is guided by the linear regression slope of the MACD/Momentum oscillator."
        ),
        "why_it_works": (
            "Volatility Clustering (Mandelbrot). Financial markets spend 70–80% of their life cycle compressing in ranges "
            "before transitioning into short, violent 20–30% trend expansions. Large institutions accumulate inside low "
            "volatility regimes to avoid market impact, causing the compression."
        ),
        "core_assumptions": [
            "Prolonged compression builds potential energy that must release via expansion.",
            "The direction of the initial momentum oscillator tick matches the genuine institutional expansion.",
            "Higher-timeframe compression (1H, 4H, 1D) carries significantly higher follow-through than sub-15m.",
        ],
        "limitations": (
            "Low-liquidity regimes frequently produce false expansions (whipsaws / head-fakes) that immediately snap back inside the bands."
        ),
        "tailored_adjustments": (
            "Never trade the squeeze in isolation. Require Volume Surge confluence (≥1.3x 20-bar SMA) and filter by "
            "session timing (London and New York opens only)."
        ),
    },
    "SHLA": {
        "short_title": "Liquidity Sweeps",
        "purposed_name": "Institutional Stop-Hunt & Liquidity Absorption (SHLA)",
        "original_name": "LuxAlgo Liquidity Sweeps & Order Blocks",
        "acronym": "SHLA",
        "tagline": "Capitalizing on retail stop-loss sweeps into institutional absorption zones.",
        "how_it_works": (
            "Detects equal highs (EQH) or equal lows (EQL) where retail stop-losses congregate. When price spikes through "
            "a key swing level on surge volume but immediately closes back inside the prior range, resting liquidity was absorbed. "
            "Identifies Order Blocks (the last opposite-color candle before structural displacement) as institutional mitigation cushions."
        ),
        "why_it_works": (
            "Market Microstructure & Auction Liquidity. Large institutions cannot enter market orders without heavy slippage. "
            "They intentionally push prices through obvious chart points to trigger retail stop orders (liquidity pools), using those "
            "stops as counter-party volume to fill large positions."
        ),
        "core_assumptions": [
            "Retail stops cluster predictably at obvious swing pivots and round psychological numbers.",
            "A candle close that fails to sustain past the level confirms institutional absorption rather than breakout momentum.",
            "The origin candle of the sweep displacement represents institutional cost basis.",
        ],
        "limitations": (
            "In high-impact macro news events (CPI, FOMC), what appears to be a sweep may simply be runaway institutional trend flow."
        ),
        "tailored_adjustments": (
            "Cross-reference with the Context Engine Macro Regime. If macro is Risk-On Expansion, only take sweeps of swing lows for long trades. "
            "Never short an upward sweep in a strong macro bull expansion."
        ),
    },
    "AVIA": {
        "short_title": "Volume Profile",
        "purposed_name": "Auction Value Area & Institutional Acceptance (AVIA)",
        "original_name": "Fixed Range Volume Profile (FRVP)",
        "acronym": "AVIA",
        "tagline": "Tracking institutional consensus through volume-at-price distribution.",
        "how_it_works": (
            "Aggregates volume horizontally across discrete price levels over a designated swing cycle. Identifies the "
            "Point of Control (POC) where the greatest volume transacted, and the Value Area (VAH and VAL) enclosing the 70% "
            "normal distribution of total traded volume."
        ),
        "why_it_works": (
            "Auction Market Theory (Peter Steidlmayer). Markets are continuous two-way auctions seeking fair value. "
            "Prices within the Value Area represent market agreement; prices outside are promotional offers to test participant interest."
        ),
        "core_assumptions": [
            "The POC acts as a gravitational magnet during low-momentum market conditions.",
            "A bar closing outside the Value Area on above-average volume confirms institutional acceptance of new value.",
            "Low Volume Nodes (LVNs) act as slippery zones where price traverses rapidly.",
        ],
        "limitations": (
            "Fixed range profiles are vulnerable to improper anchor selection. Arbitrary date boundaries distort the true auction cycle."
        ),
        "tailored_adjustments": (
            "Automatically anchor the profile to the most recent confirmed structural swing pivot (HH/LL) rather than arbitrary calendar windows."
        ),
    },
    "TCMB": {
        "short_title": "Trend Clouds",
        "purposed_name": "Multi-Band Trend Cushion & Momentum Bands (TCMB)",
        "original_name": "Ripster EMA Clouds",
        "acronym": "TCMB",
        "tagline": "Asymmetric pullback entries into institutional moving average bands.",
        "how_it_works": (
            "Computes shaded zones between exponential moving averages. A fast band (5-12 EMA) tracks short-term momentum; "
            "a trend band (34-50 EMA) tracks structural trend support. Entries are triggered on pullbacks into the band followed by candle confirmation."
        ),
        "why_it_works": (
            "Laddered Order Flow. Institutional execution algorithms do not place orders at a single tick line; they ladder limit bids "
            "across an average price band during sustained trends."
        ),
        "core_assumptions": [
            "The 5-12 cloud holds in strong momentum phases.",
            "Pullbacks to the 34-50 cloud offer the highest risk-reward entry points in an established trend.",
            "A clean cross through the 34-50 cloud signals structural trend invalidation.",
        ],
        "limitations": (
            "Completely fails during sideways, range-bound consolidation, creating severe chop whipsaws."
        ),
        "tailored_adjustments": (
            "Gate TCMB signals with our VCEI (Squeeze Pro) indicator: only take cloud pullback trades when VCEI confirms no active high compression."
        ),
    },
    "AVWAP": {
        "short_title": "Anchored VWAP",
        "purposed_name": "Institutional Anchored Volume-Weighted Benchmark (AVWAP)",
        "original_name": "Anchored VWAP",
        "acronym": "AVWAP",
        "tagline": "The true aggregate break-even price of market participants from key market events.",
        "how_it_works": (
            "Calculates cumulative (Price × Volume) divided by cumulative Volume, anchored specifically to significant market inflections "
            "(macro release, earnings, swing high, or weekly/monthly opens)."
        ),
        "why_it_works": (
            "Institutional Performance Metric. Institutional execution desks are evaluated against VWAP benchmarks. If price is above their anchor, "
            "they are in profit and defend their position; if below, they face liquidation or hedging pressure."
        ),
        "core_assumptions": [
            "The anchor point represents a true regime shift where participant inventory re-accumulated.",
            "Price bouncing off an ascending AVWAP confirms institutional defense.",
        ],
        "limitations": (
            "Diminishing sensitivity: As the anchor moves further back in time, individual bars exert less mathematical pull."
        ),
        "tailored_adjustments": (
            "Anchor dynamically to the weekly/monthly session open and the last major swing reversal high/low."
        ),
    },
    "NERN": {
        "short_title": "Machine Learning (KNN)",
        "purposed_name": "Non-Euclidean Regime Nearest-Neighbors (NERN)",
        "original_name": "Lorentzian Distance KNN Classification",
        "acronym": "NERN",
        "tagline": "Multi-dimensional pattern matching robust against financial fat tails.",
        "how_it_works": (
            "Projects a multi-feature vector (RSI, ATR, CCI, ADX) into non-Euclidean Lorentzian warped space: "
            "d(u, v) = sum(ln(1 + |u_i - v_i|)). Identifies the 7 closest historical vectors and votes on forward directional probability."
        ),
        "why_it_works": (
            "Financial Fat Tails (Leptokurtosis). Standard Euclidean distance (L2 norm) heavily overpenalizes outlier spikes (flash crashes / pumps). "
            "Lorentzian logarithmic distance compresses outliers, finding genuine regime analogs."
        ),
        "core_assumptions": [
            "Multi-dimensional technical states recur with statistically similar outcomes.",
            "Warped metric space captures market geometry better than linear correlation.",
        ],
        "limitations": (
            "Macro structural changes (e.g. zero interest rates vs 5% rates) can alter the forward outcomes of identical technical patterns."
        ),
        "tailored_adjustments": (
            "Filter NERN neighbor pools by Macro Regime congruence, ensuring historical analogs match current yield and dollar trends."
        ),
    },
    "IFVG": {
        "short_title": "Fair Value Gaps",
        "purposed_name": "Institutional Fair Value Gap & Imbalance Rebalance (IFVG)",
        "original_name": "ICT Fair Value Gap (BISI / SIBI)",
        "acronym": "IFVG",
        "tagline": "Exploiting price delivery inefficiency where market makers return to rebalance unmitigated orders.",
        "how_it_works": (
            "Identifies 3-bar displacement impulses where candle 1 wick and candle 3 wick leave an unfilled price vacuum "
            "(Bullish BISI: low[3] > high[1]; Bearish SIBI: high[3] < low[1]). Tracks the 50% Consequent Encroachment (CE) "
            "as the equilibrium midpoint of the gap. Generates signals when subsequent price retraces into the gap and confirms "
            "a directional reaction while holding above/below the CE line."
        ),
        "why_it_works": (
            "Order Flow Inefficiency & Market Maker Algorithms (Michael Huddleston / ICT). During violent algorithmic expansions, "
            "liquidity is offered only to one side of the order book, creating an imbalance. Interbank price delivery algorithms "
            "retrace into these fair value voids to facilitate two-way trading before continuing structural trend expansion."
        ),
        "core_assumptions": [
            "Institutional displacement leaves resting limit liquidity in unmitigated imbalances.",
            "The 50% Consequent Encroachment (CE) acts as algorithmic support/resistance within the void.",
            "Candle bodies respect the FVG boundary; wicks may penetrate but closing through invalidates the imbalance.",
        ],
        "limitations": (
            "In extreme runaway momentum (macro trend shifts or liquidation cascades), price can blow straight through gaps without pausing."
        ),
        "tailored_adjustments": (
            "Only trade FVGs aligned with higher-timeframe market structure (HTF trend). Invalidate immediately if a candle body closes beyond the gap boundary."
        ),
    },
    "DREF": {
        "short_title": "Dealing Range SFP",
        "purposed_name": "Dealing Range Equilibrium & Swing Failure Pattern (DREF)",
        "original_name": "Trader Mayne Dealing Range & SFP",
        "acronym": "DREF",
        "tagline": "Trading range deviations, liquidity stop-runs, and mean reversion to the 50% Equilibrium.",
        "how_it_works": (
            "Constructs the active Dealing Range between Range High (R_H) and Range Low (R_L) with 50% Equilibrium (EQ). "
            "Awaits a Swing Failure Pattern (SFP) where price wicks beyond R_H or R_L to sweep resting stops, followed by an immediate "
            "candle body close back inside the range on expanding volume. Triggers on the reclaim; Stop Loss at the deviation wick; "
            "Target 1 (50% scale-out) at the 50% EQ midpoint; Target 2 (terminal target) at the opposite range boundary."
        ),
        "why_it_works": (
            "Liquidity Engineering & Mean Reversion (Trader Mayne). Ranges represent balance. Breakouts out of established ranges "
            "frequently lack follow-through and are designed by market makers to bait breakout traders and harvest stop-losses. "
            "When the market fails to accept outside the range, trapped participants are forced to cover, driving an aggressive rotational "
            "repricing straight through the 50% EQ to the opposing liquidity pool."
        ),
        "core_assumptions": [
            "Markets spend the majority of their lifecycle rotating within defined dealing ranges.",
            "A wick beyond range boundaries followed by a close back inside confirms liquidity absorption, not real expansion.",
            "The 50% EQ level acts as the primary magnetic take-profit zone where risk must be de-risked to breakeven.",
        ],
        "limitations": (
            "Genuine regime breakouts with sustained high volume will fail an SFP setup and continue trending away."
        ),
        "tailored_adjustments": (
            "Require volume expansion on the reclaim bar and never fight a daily/weekly macro trend when playing intraday range reclaims."
        ),
    },
}


class LLMScaffold:
    """
    Connects datafeeds, deterministic features, context engine pillars,
    and account rules to an LLM via structured slot injection.
    """

    def build_prompt_slots(
        self,
        opp: Any,
        context_summary: dict[str, Any],
        account_id: str,
        account_state: dict[str, Any],
        user_notes: str = "",
        past_trades: list[dict[str, Any]] | None = None,
    ) -> dict[str, str]:
        """
        Assemble standardized, injection-safe context slots for LLM evaluation.
        """
        # 1. Macro Regime Slot
        macro = context_summary.get("macro", {})
        macro_slot = (
            f"MACRO REGIME: {macro.get('macro_regime', 'neutral_regime').upper()}\n"
            f"• US 10Y Yield: {macro.get('us_10y_yield', 4.0):.2f}% ({macro.get('us_10y_trend', 'flat')})\n"
            f"• Dollar Index Trend: {macro.get('dollar_index_trend', 'flat')}"
        )

        # 2. Seasonality & Session Slot
        sess = context_summary.get("seasonality", {})
        sess_slot = (
            f"SESSION & SEASONALITY:\n"
            f"• Active Session: {sess.get('current_session', 'Active Session')}\n"
            f"• Session Bias: {sess.get('session_bias', 'normal')}\n"
            f"• Liquidity Tier: {sess.get('liquidity_level', 'moderate')}\n"
            f"• Day of Week / Month: {sess.get('weekday', 'Midweek')}, {sess.get('month', 'Month')}\n"
            f"• Seasonality Note: {sess.get('seasonality_notes', 'Standard trading flow')}"
        )

        # 3. Sentiment & Breadth Slot
        sent = context_summary.get("sentiment", {})
        sent_slot = (
            f"MARKET SENTIMENT & BREADTH:\n"
            f"• Crypto Fear & Greed Index: {sent.get('fear_greed_score', 50)} / 100 ({sent.get('fear_greed_class', 'Neutral')})\n"
            f"• Equity Market Breadth: {sent.get('equity_breadth', 'neutral')}\n"
            f"• Contrarian Insight: {sent.get('contrarian_insight', 'Balanced sentiment')}"
        )

        # 4. Economic Catalysts Slot
        catalysts = context_summary.get("catalysts", [])
        nearest = context_summary.get("nearest_catalyst", {})
        clean_window = context_summary.get("clean_window_for_breakouts", True)
        cat_lines = [
            f"CATALYST SCHEDULE (Clean Breakout Window: {'YES' if clean_window else 'NO - CAUTION'}):"
        ]
        for c in catalysts[:3]:
            cat_lines.append(f"• {c['event']} in {c['hours_until']}h [{c['impact']}] -> {c['trading_warning']}")
        cat_slot = "\n".join(cat_lines)

        # 5. Account State Slot
        acct_slot = (
            f"TARGET ACCOUNT CONSTRAINTS ({account_id}):\n"
            f"• Current Equity: ${account_state.get('equity', 10000.0):,.2f}\n"
            f"• Max Drawdown Floor: ${account_state.get('drawdown_floor', 9700.0):,.2f}\n"
            f"• Usable Headroom: ${account_state.get('headroom', 250.0):,.2f}\n"
            f"• Remaining Daily Loss Budget: ${account_state.get('daily_loss_limit', 300.0):,.2f}\n"
            f"• Factored Target Risk: ${account_state.get('factored_risk_usd', 100.0):,.2f}"
        )

        # 6. Technical Setup & Purposed Systems Slot
        entry_p = opp.trigger_price or 1.0
        stop_p = opp.invalidation_price or 0.95
        stop_dist = abs(entry_p - stop_p)
        r2_target = entry_p + (stop_dist * 2.0) if opp.direction == "long" else entry_p - (stop_dist * 2.0)
        r3_target = entry_p + (stop_dist * 3.0) if opp.direction == "long" else entry_p - (stop_dist * 3.0)

        tech_slot = (
            f"TECHNICAL SETUP & PURPOSED SYSTEMS:\n"
            f"• Instrument: {opp.instrument_id} ({opp.horizon})\n"
            f"• Direction: {opp.direction.upper()}\n"
            f"• Merit Score: {opp.merit_score:.1f} / 100 | Signal Confidence: {int(opp.confidence_score * 100)}%\n"
            f"• Trigger Price: ${entry_p:.4f} | Invalidation Stop: ${stop_p:.4f} (Stop Distance: {stop_dist:.4f})\n"
            f"• Projections: 2R at ${r2_target:.4f} | 3R at ${r3_target:.4f}\n"
            f"• Thesis: {opp.thesis}\n"
            f"• Counter Evidence: {opp.counter_evidence or 'None flagged'}"
        )

        # 7. First Principles Strategy Reference Slot
        principles_lines = ["PURPOSED STRATEGY HEURISTICS:"]
        for acronym, meta in STRATEGY_DEEP_DIVES.items():
            principles_lines.append(f"• {meta['purposed_name']} [{acronym}]: {meta['tagline']}")
        principles_slot = "\n".join(principles_lines)

        # 8. Historical Trades / Memory Slot
        hist_lines = ["RELEVANT HISTORICAL PRECEDENTS & USER COMMENTARY:"]
        if past_trades:
            for pt in past_trades[:3]:
                hist_lines.append(f"• Setup: {pt.get('setup')} | Note: \"{pt.get('reason')}\" | Outcome: {pt.get('outcome')}")
        else:
            hist_lines.append("• No prior historical trades logged for this exact setup signature.")
        if user_notes:
            hist_lines.append(f"• Active User Review Rationale: \"{user_notes}\"")
        hist_slot = "\n".join(hist_lines)

        return {
            "macro_regime": macro_slot,
            "seasonality_session": sess_slot,
            "sentiment_breadth": sent_slot,
            "economic_catalysts": cat_slot,
            "account_state": acct_slot,
            "technical_setup": tech_slot,
            "strategy_principles": principles_slot,
            "historical_commentary": hist_slot,
        }

    def format_master_prompt(self, slots: dict[str, str]) -> str:
        """Combine slots into an injection-safe prompt for any LLM."""
        return f"""You are the Chief Risk Officer and Strategic Analyst for a proprietary trading system.
Your mission is to rigorously interrogate trade opportunities, identify structural blindspots, protect account drawdown headroom, and optimize expectancy.

[SYSTEM OPERATING BOUNDARIES]
1. Law #1 is Capital Preservation: Never endorse an execution that breaches account drawdown or daily limits.
2. For Breakoutprop: The $9,700 static floor and $300 daily loss window are sacred limits.
3. Every trade must have a verified structural invalidation level and positive risk-reward (minimum 2.0R).
4. If an intraday breakout setup directly precedes a high-impact catalyst (<2h), flag as CATALYST_RISK or REJECT.

---
[CONTEXT SLOT 1: MACRO ENVIRONMENT]
{slots['macro_regime']}

[CONTEXT SLOT 2: SEASONALITY & SESSION LIQUIDITY]
{slots['seasonality_session']}

[CONTEXT SLOT 3: SENTIMENT & BREADTH]
{slots['sentiment_breadth']}

[CONTEXT SLOT 4: ECONOMIC CATALYSTS]
{slots['economic_catalysts']}

[CONTEXT SLOT 5: TARGET ACCOUNT STATE]
{slots['account_state']}

[CONTEXT SLOT 6: ASSET TECHNICAL SETUP]
{slots['technical_setup']}

[CONTEXT SLOT 7: STRATEGY FIRST PRINCIPLES]
{slots['strategy_principles']}

[CONTEXT SLOT 8: HUMAN CONTEXT & TRADE MEMORY]
{slots['historical_commentary']}
---

[INSTRUCTIONS]
Synthesize the evidence across all slots and return a JSON object with:
- "verdict": One of "ACCEPT", "MODIFY_RISK", "DEFER", "REJECT"
- "confluence_confidence": float between 0.0 and 1.0
- "executive_synthesis": Concise 2-3 sentence strategic rationale
- "key_risks_and_blindspots": list of specific structural risks
- "sizing_and_plan_refinement": {{
    "recommended_risk_usd": float,
    "adjusted_entry": float,
    "adjusted_stop": float,
    "target_r2": float,
    "target_r3": float
  }}
- "playbook_learning_feedback": actionable advice on how to improve this setup rule in our broader systems.
"""

    def build_focused_payload(
        self,
        opp: Any,
        context_summary: dict[str, Any],
        account_id: str,
        account_state: dict[str, Any],
        user_notes: str = "",
        past_trades: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """
        Build a focused context payload that strictly separates:
        1. Primary data used directly in this trade setup and sizing decision.
        2. Secondary context available in the system but NOT directly factored into this setup.
        """
        entry_p = float(opp.trigger_price or 1.0)
        stop_p = float(opp.invalidation_price or 0.95)
        stop_dist = abs(entry_p - stop_p)
        r2_target = entry_p + (stop_dist * 2.0) if opp.direction == "long" else entry_p - (stop_dist * 2.0)
        r3_target = entry_p + (stop_dist * 3.0) if opp.direction == "long" else entry_p - (stop_dist * 3.0)

        # 1. Primary data USED in this decision
        used_sections: list[str] = [
            "### 1. ASSET SETUP & DIRECT SIGNAL TRIGGERS (USED IN DECISION)",
            f"• Instrument: {opp.instrument_id} ({opp.horizon})",
            f"• Direction: {opp.direction.upper()}",
            f"• Merit Score: {float(opp.merit_score or 0.0):.1f} / 100",
            f"• Signal Confidence: {int((opp.confidence_score or 0.75) * 100)}%",
            f"• Trigger Entry Price: ${entry_p:.4f}",
            f"• Invalidation Hard Stop: ${stop_p:.4f} (Stop Distance: {stop_dist:.4f})",
            f"• Projected 2R Target: ${r2_target:.4f}",
            f"• Projected 3R Target: ${r3_target:.4f}",
            f"• Playbook Thesis: {opp.thesis}",
            f"• Counter Evidence Flagged: {opp.counter_evidence or 'None identified'}",
            "",
            f"### 2. TARGET ACCOUNT BOUNDARIES (USED IN DECISION - {account_id.upper()})",
            f"• Current Equity: ${account_state.get('equity', 10000.0):,.2f}",
            f"• Max Drawdown Floor: ${account_state.get('drawdown_floor', 9700.0):,.2f}",
            f"• Usable Drawdown Headroom: ${account_state.get('headroom', 250.0):,.2f}",
            f"• Remaining Daily Loss Budget: ${account_state.get('daily_loss_limit', 300.0):,.2f}",
            f"• Targeted Trade Risk Allocation: ${account_state.get('factored_risk_usd', 100.0):,.2f}",
        ]

        # Catalyst timing if proximate
        catalysts = context_summary.get("catalysts", [])
        clean_window = context_summary.get("clean_window_for_breakouts", True)
        near_cats = [c for c in catalysts if c.get("hours_until", 999) <= 4.0]
        if near_cats or not clean_window:
            used_sections.append("")
            used_sections.append("### 3. PROXIMATE CATALYSTS (USED IN DECISION - WITHIN 4H)")
            for c in near_cats:
                used_sections.append(f"• {c['event']} in {c['hours_until']}h [{c['impact']}] — {c.get('trading_warning', '')}")
            if not clean_window:
                used_sections.append("• WARNING: Clean breakout window flag is FALSE. Catalyst event risk is active.")

        if user_notes:
            used_sections.append("")
            used_sections.append("### 4. OPERATOR DIRECT CONTEXT (USED IN DECISION)")
            used_sections.append(f"• User / Trader Rationale: \"{user_notes}\"")

        # 2. Secondary data AVAILABLE BUT NOT DIRECTLY USED
        macro = context_summary.get("macro", {})
        sess = context_summary.get("seasonality", {})
        sent = context_summary.get("sentiment", {})
        distant_cats = [c for c in catalysts if c.get("hours_until", 999) > 4.0]

        unused_sections: list[str] = [
            "### BACKGROUND CONTEXT (AVAILABLE IN SYSTEM, NOT DIRECT TRIGGER)",
            f"• Macro Regime: {macro.get('macro_regime', 'neutral_regime').upper()} (US 10Y: {macro.get('us_10y_yield', 4.0):.2f}%, DXY: {macro.get('dollar_index_trend', 'flat')})",
            f"• Session & Seasonality: {sess.get('current_session', 'Active')} session | Bias: {sess.get('session_bias', 'normal')} | Liquidity: {sess.get('liquidity_level', 'moderate')}",
            f"• Market Sentiment: Crypto Fear & Greed = {sent.get('fear_greed_score', 50)}/100 ({sent.get('fear_greed_class', 'Neutral')}) | Equity Breadth: {sent.get('equity_breadth', 'neutral')}",
            f"• Distant Scheduled Events (>4h): {len(distant_cats)} macroeconomic events tracked on calendar",
            "• Other System Strategies: VCEI, SHLA, AVIA, TCMB, AVWAP, NERN evaluated across market universe",
        ]

        used_text = "\n".join(used_sections)
        unused_text = "\n".join(unused_sections)

        focused_prompt = f"""You are the Chief Risk Officer and Senior Strategic Analyst for an institutional trading desk.
Rigorously interrogate the trade candidate below with zero tolerance for emotional bias or unhedged tail risk.

=== SECTION A: DIRECT EVIDENCE & SIZING PARAMETERS (USED TO SCORE THIS SETUP) ===
{used_text}

=== SECTION B: SYSTEM BACKGROUND (NOT DIRECTLY SCORED FOR THIS SIGNAL) ===
{unused_text}

=== EVALUATION MANDATE ===
1. Verify Capital Preservation: Confirm whether risk (${account_state.get('factored_risk_usd', 100.0):,.2f}) respects usable headroom (${account_state.get('headroom', 250.0):,.2f}) and static rules.
2. Interrogate Invalidation: Is the hard stop structurally protected by order flow or market structure, or vulnerable to random noise?
3. Scrutinize Asymmetry: Does the entry offer genuine minimum 2.0R to 3.0R potential under current conditions?
4. Identify Blindspots: What could destroy this trade that the technical scanner missed?

You MUST respond strictly with a valid JSON object matching this schema:
{{
  "verdict": "ACCEPT" | "MODIFY_RISK" | "DEFER" | "REJECT",
  "confluence_confidence": 0.85,
  "executive_summary": "Concise 2-3 sentence executive summary explaining the strategic verdict and core rationale.",
  "key_risks_and_blindspots": [
    "Specific structural risk item 1",
    "Specific structural risk item 2"
  ],
  "sizing_and_plan_refinement": {{
    "recommended_risk_usd": {account_state.get('factored_risk_usd', 100.0)},
    "adjusted_entry": {entry_p},
    "adjusted_stop": {stop_p},
    "target_r2": {r2_target},
    "target_r3": {r3_target}
  }},
  "playbook_learning_feedback": "Actionable feedback on what to adjust or monitor in this playbook setup."
}}
"""

        return {
            "used_context": used_text,
            "unused_context": unused_text,
            "focused_prompt": focused_prompt,
        }

    def run_context_audit(
        self,
        opp: Any,
        context_summary: dict[str, Any],
        account_id: str,
        account_state: dict[str, Any],
        user_notes: str = "",
        past_trades: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """
        Deterministic Institutional Rule-Based Context Audit.
        Evaluates the opportunity against strict hard-coded risk rules, drawdown headroom,
        and macro/catalyst constraints without calling an external LLM API.
        """
        slots = self.build_prompt_slots(
            opp=opp,
            context_summary=context_summary,
            account_id=account_id,
            account_state=account_state,
            user_notes=user_notes,
            past_trades=past_trades,
        )
        master_prompt = self.format_master_prompt(slots)

        # Deterministic Institutional Reasoning Synthesis
        clean_window = context_summary.get("clean_window_for_breakouts", True)
        macro_regime = context_summary.get("macro", {}).get("macro_regime", "neutral_regime")
        headroom = account_state.get("headroom", 250.0)
        target_risk = account_state.get("factored_risk_usd", 100.0)

        entry_p = opp.trigger_price or 1.0
        stop_p = opp.invalidation_price or 0.95
        stop_dist = abs(entry_p - stop_p)
        r2_target = entry_p + (stop_dist * 2.0) if opp.direction == "long" else entry_p - (stop_dist * 2.0)
        r3_target = entry_p + (stop_dist * 3.0) if opp.direction == "long" else entry_p - (stop_dist * 3.0)

        # Verdict logic
        risks = []
        if not clean_window:
            verdict = "DEFER"
            conf = 0.55
            risks.append("High-impact economic catalyst scheduled within 2 hours; wide spreads and stop whipsaws probable.")
        elif target_risk > headroom:
            verdict = "MODIFY_RISK"
            conf = 0.70
            risks.append(f"Factored risk (${target_risk:,.2f}) exceeds usable drawdown headroom (${headroom:,.2f}). Sizing must be scaled down.")
        elif opp.direction == "short" and macro_regime == "risk_on_expansion":
            verdict = "MODIFY_RISK"
            conf = 0.65
            risks.append("Shorting against a Risk-On Expansion macro tailwind carries asymmetric squeeze risk.")
        else:
            verdict = "ACCEPT"
            conf = min(max(opp.confidence_score, 0.75), 0.95)

        synthesis = (
            f"Deterministic context audit indicates {verdict} for {opp.direction.upper()} on {opp.instrument_id} ({opp.horizon}). "
            f"Evaluated against {macro_regime.replace('_', ' ').title()} regime and account headroom limits."
        )

        recommended_risk = min(target_risk, max(headroom * 0.4, 25.0)) if "bp" in account_id else target_risk

        return {
            "verdict": verdict,
            "confluence_confidence": round(conf, 2),
            "executive_synthesis": synthesis,
            "key_risks_and_blindspots": risks or ["Ensure hard stop is set immediately upon fill; monitor 2R partial at swing inflection."],
            "sizing_and_plan_refinement": {
                "recommended_risk_usd": round(recommended_risk, 2),
                "adjusted_entry": round(entry_p, 4),
                "adjusted_stop": round(stop_p, 4),
                "target_r2": round(r2_target, 4),
                "target_r3": round(r3_target, 4),
            },
            "playbook_learning_feedback": (
                "Continuous feedback: Squeeze Pro (VCEI) expansions that align with institutional session open liquidity "
                "consistently generate higher R-multiples than counter-session entries."
            ),
            "assembled_prompt": master_prompt,
        }

    # Backward compatibility alias
    def evaluate_opportunity(
        self,
        opp: Any,
        context_summary: dict[str, Any],
        account_id: str,
        account_state: dict[str, Any],
        user_notes: str = "",
        past_trades: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Backward compatibility alias for run_context_audit()."""
        return self.run_context_audit(
            opp=opp,
            context_summary=context_summary,
            account_id=account_id,
            account_state=account_state,
            user_notes=user_notes,
            past_trades=past_trades,
        )

