# Trading Automations: Institutional Trading Intelligence Platform

A modular, multi-asset trading intelligence and decision-support system built in Python for **Breakoutprop Turbo Evaluation accounts** ($10,000 equity, $9,700 static drawdown floor, $300 daily loss limit) and **Personal Portfolio accounts** (Wealthsimple, Interactive Brokers).

The platform continuously collects closed-bar market data, computes deterministic technical features, identifies high-conviction setups across multiple timeframes (`15m` to `1M`), integrates non-technical macro/seasonality/sentiment/catalyst context, tracks sector rotation and abnormal deviations, enforces strict account risk gating, and scaffolds qualitative reviews through deterministic audits and direct frontier LLM integrations (OpenAI, Anthropic, Google Gemini) via an interactive Streamlit cockpit and headless CLI.

---

## Current Status: Production-Ready Alpha (v1.1)

- **Storage**: High-concurrency SQLite WAL mode (`data/trading_automations.db`) with 15 versioned data tables tracking immutable market observations, features, signals, opportunities, accounts, trade plans, decisions, orders, and outcomes.
- **Connectors Active**: Bybit linear perpetuals (crypto) and Yahoo Finance (US/Canadian equities, ETFs, indices) across `15m`, `1h`, `4h`, `1d`, `1w`, and `1M`.
- **Testing**: **41/41 automated unit tests passing** (`uv run pytest tests/`).
- **Opportunity Aggregation Engine**: Groups multiple horizon setups into unique ticker profiles with multi-timeframe directional confluence and Where/Why/How signal decomposition.
- **AI Strategic Review**: Multi-provider frontier LLM integration supporting OpenAI, Anthropic Claude, and Google Gemini with live token & cost estimation.
- **Design & Typography**: Custom institutional theme with Google Fonts Inter (headings & UI structure), Roboto (body copy), JetBrains Mono (monetary metrics, tickers, UTC timestamps), `.status-pill` micro-badges, and zero external branding.
- **Interfaces**: Streamlit Cockpit (`uv run python src/cli.py gui`) and Typer/Rich headless CLI (`src/cli.py`).

---

## Key System Capabilities

### 1. Multi-Asset Data Ingestion & Canonical Storage
- **Feeds**: Ingestion via Bybit (`ccxt`) and Yahoo Finance (`yfinance`).
- **Idempotency & Integrity**: SHA-256 bar hashing prevents duplicate records; strict closed-bar filtering discards unfinished candles.
- **Time Horizons**: Full support for intraday, swing, and macro horizons: `15m`, `1h`, `4h`, `1d`, `1w`, and `1M`.

### 2. Feature Computation & Technical Setups
- **Baseline Features**: ATR(14), Bollinger Bands(20, 2), RSI(14), EMA ribbons (9, 21, 55, 200), On-Balance Volume (OBV), and Session VWAP.
- **Institutional & Community Strategy Models**:
  - **VCEI** (*Volatility Compression & Expansion Indicator* / Squeeze Pro): Multi-tier squeeze detection (low/mid/high compression via BB vs Keltner Channels with momentum histogram).
  - **SHLA** (*Stop-Hunt Liquidity Analyzer* / LuxAlgo Sweep & Order Blocks): Identifies institutional liquidity sweeps and swing order block invalidations.
  - **AVIA** (*Auction Value & Inventory Analyzer* / Volume Profile POC/VAH/VAL): Computes Value Area High (VAH), Value Area Low (VAL), and Point of Control (POC) to detect value-area re-entries.
  - **TCMB** (*Trend Cloud Momentum Bands* / Ripster Clouds): Dual EMA clouds (8/21 short-term, 34/50 intermediate) with directional trend scoring.
  - **AVWAP** (*Event-Anchored Volume Weighted Average Price*): Measures price extension and reclaim against key event reference bars.
  - **NERN** (*Non-Parametric Evaluator & Regime Neighbor* / Lorentzian KNN): Distance-weighted k-nearest-neighbors classification across Lorentzian-metric feature spaces.
  - **IFVG** (*Institutional Fair Value Gaps* / ICT Imbalance BISI & SIBI): Detects 3-candle displacement voids and 50% Consequent Encroachment (CE) defense for retest continuation entries.
  - **DREF** (*Dealing Range Equilibrium & SFP Reclaim* / Trader Mayne): Identifies Range High ($R_H$) and Range Low ($R_L$) boundaries, executes on liquidity stop sweeps (SFPs) with candle body reclaims, scaling out 50% at the 50% Equilibrium (EQ) and targeting opposite range extremes.
- **Named Playbook Setups**: Includes compression breakouts, range breakouts, VWAP reclaims, trend pullbacks, daily compression expansions, momentum divergences, FVG retest continuations, and dealing range SFP reclaims.

### 3. Non-Technical Context Engine
Deterministically incorporates four qualitative market pillars into opportunity scoring:
1. **Seasonality & Session Regimes**: Tracks Asia, London, and New York sessions with an automated **00:30 UTC intraday volatility reset window**.
2. **Macro Yields & Dollar Regimes**: Real-time monitoring of 10-Year Treasury Yields (`^TNX`) and US Dollar Index (`UUP`) to classify environments into *Risk-On Growth*, *Tightening Shock*, or *Neutral*.
3. **Sentiment & Breadth**: Ingestion of the Crypto Fear & Greed Index and equity market breadth with contrarian sentiment filtering.
4. **High-Impact Economic Catalysts**: Real-time countdowns for CPI, FOMC rate decisions, Non-Farm Payrolls (NFP), and PPI, issuing warnings against entering intraday breakouts within 2 hours of major economic releases.

### 4. Sector Rotation & Abnormal Deviations
- **Taxonomy**: Resolves equities and crypto perpetuals to their functional sectors (Technology, Financials, Energy, Crypto L1s, Crypto Store of Value, etc.).
- **Relative Strength vs `SPY`**: Calculates 1-day and 5-day performance spreads against the market benchmark.
- **Abnormal Deviation Z-Scores ($|Z| \ge 1.75\sigma$)**: Identifies anomalous institutional capital rotation:
  - **Abnormal Inflow / Accumulation ($Z \ge +1.75\sigma$)**: Extreme positive deviation outperforming the market.
  - **Abnormal Outflow / Distribution ($Z \le -1.75\sigma$)**: Extreme negative deviation lagging the market.
- **Cross-Sector Dispersion**: Tracks the return spread between the leading and lagging sectors.
- **Stock-to-Sector Alpha Divergence**: Measures whether individual stocks (`NVDA`, `AAPL`, `TSLA`, `SHOP.TO`) are generating company-specific alpha or merely drifting with broad sector beta.

### 5. Multi-Account Risk Gating & Execution Planner
- **Account Profiles**:
  - **Breakoutprop Turbo Eval $10k**: Hard static equity floor at **$9,700**, daily loss limit of **$300** with 00:30 UTC reset, maximum position cap, and crypto-focused universe.
  - **Personal Accounts** (Wealthsimple / IBKR): Configurable equity and per-trade risk percentages (e.g. 1.0% to 2.0% risk per trade).
- **Dynamic Position Sizing**:
  $$\text{Units} = \frac{\text{Allowed Risk USD}}{|\text{Trigger Price} - \text{Stop Loss Price}|}$$
  Automatically scales unit sizes down when wide swing stops (e.g. daily/weekly ATR) are required, ensuring zero account floor violations.
- **Risk Headroom Protection**: Prevents trade generation if available buffer to the $9,700 floor is less than the intended risk reservation.

### 6. Trade Journal, Commentary & Outcome Attribution
- **Human Review Checkpoint**: Opportunities require human confirmation before generating a trade plan.
- **Editable & Deletable Commentary**: Allows logging, modifying, and reviewing trade rationales and emotional state.
- **Cascading Cancellations**: Deleting or rejecting a trade automatically cancels unexecuted trade plans and releases reserved risk headroom.
- **Outcome Attribution**: Tracks realized R-multiples and post-trade performance against context engine readings.

### 7. Two-Tier Decision Review & Frontier LLM Engine

The platform implements a clear, transparent two-tier review architecture:

1. **Tier 1: Structured Context Audit (Deterministic)**:
   - Evaluates setups against an institutional rule checklist locally (zero API latency, zero token cost).
   - Checks trend alignment, compression/expansion cycle, catalyst blackout windows, session regime, risk-to-reward ratio, and account drawdown headroom.
   - Emits an algorithmic verdict (`ACCEPT`, `MODIFY_RISK`, `DEFER`, `REJECT`) with risk checklist passes/fails.

2. **Tier 2: AI Strategic Review (Authenticated Frontier LLMs)**:
   - Direct, authenticated in-app API connections to **OpenAI** (`gpt-4o`, `gpt-4o-mini`, `o1-mini`), **Anthropic** (`claude-3-7-sonnet`, `claude-3-5-sonnet`, `claude-3-5-haiku`), and **Google Gemini** (`gemini-2.0-flash`, `gemini-1.5-pro`).
   - Dynamically discovers all currently connected APIs and displays them in a provider selector. If no APIs are configured, guides the user with `"Connect an API to enable this function"`.
   - **Focused Scoped Context Injection**: Strictly separates **Primary Setup Data** (direct inputs used for sizing, entry, stop, target, and risk gating) from **Secondary Background Context** (noted environmental factors like macro, sentiment, session) to prevent prompt bloat and keep reasoning tightly grounded.
   - **Pre-Send Token & Cost Estimator**: Calculates exact input tokens and projected output costs in USD prior to running API calls.
   - **Structured JSON Schema**: Standardizes model output across all providers with fields for verdict, confluence confidence (0–100%), summary rationale, blindspot warnings, stop adjustments, and system learning recommendations.
   - **API Key Management**: Supports configuration via environment variables (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`) or local gitignored YAML configuration (`config/llm.yaml`).

### 8. Institutional UI Design System
- **Typography**: Clean Google Fonts stack with **Inter** for structural headers and navigation, **Roboto** for body copy and analysis, and **JetBrains Mono** for all prices, quantities, R-multiples, and UTC timestamps.
- **Micro-Badges**: Replaced informal emojis with high-contrast `.status-pill` micro-badges across all tables and view cards.
- **Font & Sizing Hierarchy**: Clean proportionality where headers stay crisp and data values never truncate with ellipses.
- **De-Branded Experience**: Streamlit default chrome, deploy buttons, and hamburger menus cleanly suppressed for a focused, institutional trading terminal look.

---

## Streamlit Cockpit Navigation Map

Launch with `uv run python src/cli.py gui` to access the 7 consolidated sidebar navigation views:

1. **`Opportunity Queue`**: Dual-mode screener with **Aggregated by Ticker (Recommended)** consolidating setups by unique instrument, and **All Setups (Expanded)**. Displays Peak Merit, Ticker, Confluence Direction (e.g. `▲ LONG (3 Timeframes Aligned)`), Active Horizons badges, Triggering Systems, Sector, and inline Where/Why/How signal expanders.
2. **`Opportunity Detail`**: Candidate evaluation organized by Ticker with a **Multi-Timeframe Confluence Hub**. Features expandable signal cards for each triggered system (Where / Why / How), one-click setup staging into execution planner, factored per-trade dollar risk, dynamic position sizing, deterministic **Structured Context Audit**, and separated **AI Strategic Review**.
3. **`Macro & Catalysts`**: Non-technical environmental context tracking global liquidity sessions, 10-year Treasury yields, Dollar Index (DXY), Fear & Greed sentiment, and high-impact catalyst blackout countdowns.
4. **`Deviations & Rotation`**: 11 S&P Sector SPDR Matrix vs SPY, abnormal deviation alerts ($|Z| \ge 1.75\sigma$), relative return charts, and stock-to-sector alpha divergence.
5. **`Strategy Deep-Dives`**: Succinct, plain-word strategy guides (*Volatility Squeeze*, *Liquidity Sweeps*, *Volume Profile*, *Trend Clouds*, *Anchored VWAP*, and *Machine Learning (KNN)*) breaking down calculation logic, setups, and failure modes.
6. **`Journal & Performance`**: Unified execution cockpit housing Active Orders & Fills, Trade Plans, Decision Commentary, Trade Outcome Attribution, Strategy Performance Analytics, System Optimization Insights, and CSV Export.
7. **`Settings`**: Consolidated central configuration hub housing Account Rules & Risk Parameters, Multi-Provider AI Engine & API Connections (OpenAI, Claude, Gemini), and Data Ingestion & System Health.

---

## Quickstart Guide

### Prerequisites
- Python 3.12+
- `uv` package manager installed (`curl -LsSf https://astral.sh/uv/install.sh | sh`)

### Installation & Initialization
```bash
# Clone and enter directory
cd "Trading Scripts"

# Install dependencies
uv sync

# Initialize database schemas
uv run python src/cli.py init
```

### (Optional) Configure Frontier AI API Keys
You can set your API keys as environment variables:
```bash
export OPENAI_API_KEY="sk-..."
export ANTHROPIC_API_KEY="sk-ant-..."
export GEMINI_API_KEY="AIza..."
```
Or manage them directly in the Streamlit Cockpit under **Account Cockpit & Rules > AI Engine Configuration**, which saves them securely to `config/llm.yaml` (gitignored).

### Ingest Data & Run Scans
```bash
# Ingest recent closed bars from Bybit and Yahoo Finance
uv run python src/cli.py ingest --source all --limit 60

# Run technical scan across all horizons (15m, 1h, 4h, 1d, 1w, 1M)
uv run python src/cli.py scan --horizon all

# View top ranked opportunities in terminal
uv run python src/cli.py opps

# Check system health and storage counts
uv run python src/cli.py health
```

### Launch Streamlit Cockpit
```bash
uv run python src/cli.py gui
# or directly:
uv run streamlit run src/app/main.py
```

### Run Test Suite
```bash
uv run pytest tests/ -v
```

---

## Core System Principles & Invariants

1. **Universal UTC Standard**: All internal timestamps, bar data, catalyst schedules, and journal entries are strictly stored and computed in UTC. Local device time is displayed side-by-side in the UI header and in brackets for seamless exposure and translation.
2. **Human Checkpoint Mandatory**: The platform functions as an intelligence and decision-support engine. No trade plan may execute without explicit human approval.
3. **Account Risk Takes Precedence**: Hard account constraints (such as the Breakoutprop $9,700 floor and $300 daily loss limit) strictly override any technical score or LLM recommendation.
4. **Idempotent Ingestion**: Re-running ingestion never creates duplicate bars or corrupted metrics due to SHA-256 candle hashing and unique constraint enforcement.
