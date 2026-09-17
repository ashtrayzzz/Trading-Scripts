# Walkthrough: Trading Intelligence System (v4 — Context Engine, Dynamic Sizing & Indicator Explanations)

We have completed the implementation of non-technical context tracking (Seasonality, Macro, Sentiment, Catalysts), interactive account-by-account risk sizing, editable and deletable commentaries in the Journal, in-depth technical explanations, and a Strategy Performance Analytics Report.

---

## 1. Key Accomplishments & Features

### 1. Ambient Dual-Clock Header (No Timezone Selector Needed)
- **Strict UTC Standard**: All internal models, candle timestamps, bar close checks, and Breakoutprop daily loss windows operate strictly in UTC.
- **Side-by-Side Exposure**: Rendered at the top of the interface:
  $$\text{🌐 System Standard: } \mathbf{2026\text{-}09\text{-}17\ 03\text{:}55\text{ UTC}} \quad \vert \quad \text{📍 Your Device Local Time: } \mathbf{20\text{:}55\text{ PDT}}$$
- **Automatic Local Time in Brackets**: All historical timestamps, bar open times, and catalyst countdowns display as `YYYY-MM-DD HH:MM UTC (HH:MM Local)` without requiring a manual timezone selector.

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│ 🌐 System Standard: 2026-09-17 03:55 UTC    📍 Your Device Time (PDT): 20:55 PDT      │
└────────────────────────────────────────────────────────────────────────────────────────┘
ℹ️ All candles, settlement windows, and scans execute in UTC. Local device time is displayed side-by-side to assist in direct mental translation.
```

---

### 2. Account-by-Account Risk Factoring & Live Dynamic Sizing
- Located in **`🔍 Opportunity Detail`**:
  - **Target Execution Account**: Toggle between `🏦 Breakoutprop Turbo 10k` and `👤 Personal Account (IBKR/Wealthsimple)`.
  - **Live Account Parameters**: Shows current equity, max drawdown floor ($9,700), usable headroom, and daily loss remaining ($300).
  - **Factored Per-Trade Dollar Risk Input ($)**: Allows custom dollar risk override (e.g. `$50`, `$75`, `$100`, `$250`).
  - **Real-Time Position Calculator**:
    $$\text{Position Quantity} = \frac{\text{Factored Dollar Risk}}{\vert \text{Entry Trigger} - \text{Invalidation Stop} \vert}$$
    $$\text{Headroom Consumption} = \frac{\text{Scenario Dollar Loss}}{\text{Usable Headroom}} \times 100\%$$
  - **Atomic Trade Plan Logging**: Accepting a candidate creates the immutable `TradePlan` and `RiskReservation` assigned **specifically to that chosen account**.

---

### 3. Non-Technical Context Engine ([`src/intelligence/context_engine.py`](file:///Users/awri09111/conjectural-technologies/Trading%20Scripts/src/intelligence/context_engine.py))
Located in the new **`🌐 Macro, Seasonality & Catalysts`** dashboard:
1. **🕒 Sessions & Seasonality**:
   - Tracks real-time liquidity sessions: Asian Accumulation (`00:00-07:00 UTC`), London Trend Expansion (`07:00-13:00 UTC`), New York Peak Volatility (`13:00-20:00 UTC`), and US Close/Reset (`20:00-00:30 UTC`).
   - Day-of-week liquidity patterns and weekly range notes.
2. **🏛️ Macro & Rate Regime**:
   - Real-time US 10-Year Treasury Yield (`^TNX`) and Dollar Index proxy (`UUP`/`DXY`).
   - Classifies macro into `risk_on_expansion`, `risk_off_contraction`, or `cautious_defensive`, detailing tactical implications for crypto and equity holdings.
3. **🎭 Market Sentiment**:
   - Real-time Crypto Fear & Greed Index score (0 to 100) with contrarian warnings against crowded long positions.
   - Equity market breadth (SPY price relative to 20-day and 50-day EMAs).
4. **📅 High-Impact Catalysts**:
   - Live automated countdowns for CPI, FOMC, NFP, and PPI.
   - Volatility advisories: `clean_trading_window` (>2h), `APPROACHING_CATALYST_TIGHTEN_STOPS` (2h–24h), and `IMMINENT_RELEASE_PAUSE_INTRADAY` (<2h).

---

### 4. Multi-Facet Screener & Flagged Watchlist ([`src/app/main.py`](file:///Users/awri09111/conjectural-technologies/Trading%20Scripts/src/app/main.py))
- In **`🎯 Opportunity Queue`**:
  - **Screener Controls**: Filter by Horizon, Direction, Minimum Merit Score, Setup Type (e.g. Squeeze Pro, Liquidity Sweep, Ripster Cloud), Account Eligibility (Breakoutprop vs Personal), and Macro/Catalyst alignment.
  - **⭐ Flagged Watchlist**: Bookmark priority setups using the "Flag ⭐" action to view them in a dedicated Watchlist tab.

---

### 5. Editable & Deletable Human Commentaries ([`src/journal/decisions.py`](file:///Users/awri09111/conjectural-technologies/Trading%20Scripts/src/journal/decisions.py))
- In **`📓 Journal & Orders`**:
  - **Account Filter**: Filter all decisions, plans, fills, and outcomes by account (`bp_turbo_10k` vs `ibkr_personal`).
  - **✏️ Edit Commentary**: Inline text editor allowing you to update your review rationale and notes at any time via `update_decision_commentary()`.
  - **🗑️ Delete Decision**: Safely removes a decision record and automatically cancels/releases any linked unexecuted `TradePlan` and `RiskReservation`.

---

### 6. In-Depth Technical & Framework Explanations
- In **`🔍 Opportunity Detail`**, an expandable **"💡 Complete System Explanations & Indicator Confluence"** section provides plain-English and mathematical explanations for:
  - **ChrisMoody Squeeze Pro**: Black (High), Red (Mid), and Orange (Low) compression dots with momentum slope.
  - **LuxAlgo Liquidity Sweeps & Order Blocks**: Institutional liquidity grabs and demand/supply mitigation blocks.
  - **Fixed Range Volume Profile (FRVP)**: Point of Control (POC), Value Area High (VAH), and Value Area Low (VAL).
  - **Ripster EMA Clouds**: 5-12 short-term momentum vs 34-50 trend cushion.
  - **Anchored VWAP**: Volume-weighted institutional benchmarks from inflection highs/lows.
  - **Lorentzian Distance KNN Classification**: 7-neighbor non-Euclidean space pattern matching.
  - **Context Confluence**: Session liquidity, rate headwinds, and catalyst risk assessment for that specific asset.

---

### 7. Strategy Performance Analytics Report
- Located in **`📊 Context & Strategy Performance Report`**:
  - Aggregates all human review decisions, accepted trade plans, reserved risk, and completed outcomes.
  - Visual charts of decisions by Account and by Decision Type.
  - **Actionable Optimization Insights**: System suggestions for improving expectancy based on session alignment and catalyst windows.
  - **📥 Export Data**: One-click download of the complete Decision Journal as CSV for offline analysis.

---

## 2. Verification & Test Results

### Automated Test Suite
Ran `uv run pytest tests/ -v`:
```
============================== test session starts ===============================
collected 21 items

tests/test_connector.py::test_closed_bar_filter PASSED                     [  4%]
tests/test_connector.py::test_ingestion_idempotency PASSED                 [  9%]
tests/test_context.py::test_context_engine_seasonality PASSED              [ 14%]
tests/test_context.py::test_context_engine_catalysts_and_warnings PASSED   [ 19%]
tests/test_context.py::test_context_engine_sentiment_and_confluence PASSED [ 23%]
tests/test_contracts.py::test_shared_envelope_fields PASSED                [ 28%]
tests/test_contracts.py::test_feature_computation_determinism PASSED       [ 33%]
tests/test_eligibility.py::test_high_score_cannot_bypass_risk_limit PASSED   [ 38%]
tests/test_eligibility.py::test_headroom_near_floor PASSED                 [ 42%]
tests/test_eligibility.py::test_breakoutprop_daily_reset_window PASSED     [ 47%]
tests/test_eligibility.py::test_sizing_adapts_to_wide_swing_stops PASSED   [ 52%]
tests/test_journal.py::test_fill_import_idempotency PASSED                 [ 57%]
tests/test_journal.py::test_trade_plan_and_atomic_risk_reservation PASSED   [ 61%]
tests/test_journal.py::test_outcome_evaluation_r_multiple PASSED           [ 66%]
tests/test_journal.py::test_update_decision_commentary PASSED              [ 71%]
tests/test_journal.py::test_delete_decision_cascades_safely PASSED         [ 76%]
tests/test_scanner.py::test_scanner_breakout_detection PASSED              [ 80%]
tests/test_scanner.py::test_broad_discovery_mode PASSED                    [ 85%]
tests/test_scoring.py::test_missing_features_yields_unscored PASSED        [ 90%]
tests/test_scoring.py::test_missing_optional_input_marked_incomplete PASSED [ 95%]
tests/test_scoring.py::test_valid_scoring_calculation PASSED               [100%]

=============================== 21 passed in 2.78s ===============================
```

### Full Pipeline Run
```bash
uv run python src/cli.py scan --horizon all
# Output: Scan complete: 115 signals emitted, 95 opportunities assembled.
```

---

## 3. How to Launch & Explore

Launch the Streamlit Cockpit GUI:
```bash
uv run streamlit run src/app/main.py
```
or via the CLI:
```bash
uv run python src/cli.py gui
```

Open your browser at `http://localhost:8501`:
1. Check the top ambient header for UTC vs your local device time.
2. In **🎯 Opportunity Queue**, use the Screener to filter by Setup type (e.g. Squeeze Pro, Liquidity Sweep) and click "Flag ⭐" to test the Watchlist tab.
3. In **🔍 Opportunity Detail**, select your target account (Breakoutprop vs Personal), adjust your factored dollar risk ($), inspect the live position sizing, and expand the Indicator Explanations.
4. In **🌐 Macro, Seasonality & Catalysts**, explore the 4 tabs: Global Sessions, Yield/DXY Regime, Fear & Greed sentiment, and Catalyst countdowns.
5. In **📊 Context & Strategy Performance Report**, view decision metrics and download the journal CSV.
6. In **📓 Journal & Orders**, edit your past commentary inline and test the deletion action.
