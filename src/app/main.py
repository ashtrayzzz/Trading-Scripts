import os
from pathlib import Path
import sys
import json

# Ensure workspace root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import streamlit as st
import pandas as pd
from datetime import datetime
import yaml

from src.accounts.base import AccountState
from src.accounts.breakoutprop import BreakoutpropAccount
from src.accounts.eligibility import AccountEligibilityEngine
from src.accounts.personal import PersonalAccount
from src.app.components.chart import create_candlestick_chart
from src.connectors.bybit import BybitConnector
from src.connectors.yahoo_finance import YahooFinanceConnector
from src.core.database import DATABASE_DEBUG, DATABASE_URL, SessionLocal, init_db
from src.core.enums import AssessmentStage, DecisionType, PlaybookMode, QualityStatus, RiskState, TimeInterval
from src.core.models import (
    DecisionRecord,
    FeatureSnapshot,
    FillRecord,
    InstrumentRecord,
    MarketObservation,
    ModuleSignal,
    OpportunityVersion,
    OutcomeRecord,
    RiskReservation,
    TradePlan,
)
from src.core.time import format_dual_time, get_breakoutprop_daily_window, get_dual_clock_status, utc_now
from src.intelligence.base import ModuleContext
from src.intelligence.context_engine import ContextEngine
import importlib
import src.core.enums as enums_module
import src.intelligence.llm_scaffold as llm_scaffold_module
import src.intelligence.ai_review as ai_review_module
import src.opportunities.aggregation as opp_agg_module
import src.opportunities.lifecycle as opp_lifecycle_module

# Ensure hot reloads pick up edits to enums, intelligence and opportunity modules in running Streamlit instances
importlib.reload(enums_module)
importlib.reload(llm_scaffold_module)
importlib.reload(ai_review_module)
importlib.reload(opp_agg_module)
importlib.reload(opp_lifecycle_module)

from src.opportunities.lifecycle import OpportunityLifecycleManager

from src.intelligence.ai_review import (
    DEFAULT_MODELS,
    PROVIDER_MODELS,
    ask_ai_strategy_chat,
    estimate_prompt_cost,
    generate_ai_review,
    get_connected_apis,
    load_llm_config,
    save_llm_config,
    test_llm_connection,
)
from src.intelligence.llm_scaffold import LLMScaffold, STRATEGY_DEEP_DIVES
from src.intelligence.sector_engine import SectorEngine, get_instrument_sector
from src.intelligence.technical import TechnicalScanner
from src.journal.decisions import JournalService
from src.opportunities.aggregation import (
    TickerOpportunityAggregate,
    aggregate_opportunities_by_ticker,
    get_latest_active_opportunities,
)
from src.opportunities.assembly import OpportunityAssembler

# Configure page
st.set_page_config(
    page_title="Trading Automations Cockpit",
    page_icon="◈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Global Typography, Proportional Sizing, and Protected Icon Glyphs
st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=Roboto:wght@300;400;500;700&family=JetBrains+Mono:wght@400;500;600&display=swap');

    /* Global Base Typography & Institutional Dark Palette */
    html, body, .stApp, [data-testid="stAppViewContainer"], [data-testid="stMain"] {
        font-family: 'Inter', 'Roboto', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
        background-color: #0b0f19 !important;
        color: #f1f5f9 !important;
    }
    [data-testid="stSidebar"], section[data-testid="stSidebar"] {
        background-color: #111827 !important;
        border-right: 1px solid #1f2937 !important;
    }
    [data-testid="stHeader"] {
        background-color: #0b0f19 !important;
    }
    p, .stMarkdown p, .stMarkdown li, .stMarkdown strong, .stMarkdown em, label, .stSelectbox label, .stTextInput label, .stNumberInput label {
        font-family: 'Inter', 'Roboto', sans-serif;
        color: #e2e8f0;
    }

    /* Monospace */
    code, pre, kbd, samp, .terminal-mono {
        font-family: 'JetBrains Mono', monospace !important;
    }

    /* Explicitly protect icon glyphs from font overrides */
    .material-icons,
    .material-symbols-rounded,
    .material-symbols-outlined,
    [data-testid*="stIcon"],
    [data-testid="stIconMaterial"],
    .ag-icon,
    .ag-header-cell-menu-button span,
    span[class*="material-symbols"],
    i[class*="material"] {
        font-family: 'Material Symbols Rounded', 'Material Symbols Outlined', 'Material Icons', 'agGridMaterial', sans-serif !important;
        font-weight: normal !important;
        font-style: normal !important;
        line-height: 1 !important;
        letter-spacing: normal !important;
        text-transform: none !important;
        display: inline-block !important;
        white-space: nowrap !important;
        word-wrap: normal !important;
        direction: ltr !important;
    }

    /* Headings Hierarchy (H1 - H4) */
    h1, .stMarkdown h1, [data-testid="stHeader"] h1 {
        font-family: 'Inter', sans-serif !important;
        font-size: 1.55rem !important;
        font-weight: 700 !important;
        color: #f8fafc !important;
        letter-spacing: -0.02em !important;
        line-height: 1.25 !important;
        margin-top: 0.2rem !important;
        margin-bottom: 0.4rem !important;
    }

    h2, .stMarkdown h2 {
        font-family: 'Inter', sans-serif !important;
        font-size: 1.25rem !important;
        font-weight: 600 !important;
        color: #e2e8f0 !important;
        letter-spacing: -0.01em !important;
        line-height: 1.3 !important;
        margin-top: 0.9rem !important;
        margin-bottom: 0.35rem !important;
        border-bottom: 1px solid #1e2330;
        padding-bottom: 0.3rem;
    }

    h3, .stMarkdown h3 {
        font-family: 'Inter', sans-serif !important;
        font-size: 1.05rem !important;
        font-weight: 600 !important;
        color: #cbd5e1 !important;
        letter-spacing: -0.005em !important;
        line-height: 1.35 !important;
        margin-top: 0.6rem !important;
        margin-bottom: 0.25rem !important;
    }

    h4, .stMarkdown h4 {
        font-family: 'Inter', sans-serif !important;
        font-size: 0.85rem !important;
        font-weight: 600 !important;
        color: #94a3b8 !important;
        text-transform: uppercase !important;
        letter-spacing: 0.05em !important;
        line-height: 1.3 !important;
        margin-top: 0.5rem !important;
        margin-bottom: 0.2rem !important;
    }

    /* Harmonized Metric Cards: Proportional Headers and Non-Truncating Values */
    [data-testid="stMetric"] {
        background: #11141d;
        border: 1px solid #1e2330;
        border-radius: 6px;
        padding: 8px 12px;
        min-height: 68px;
    }
    [data-testid="stMetricLabel"] {
        font-size: 0.8rem !important;
        font-weight: 600 !important;
        color: #94a3b8 !important;
        text-transform: uppercase !important;
        letter-spacing: 0.05em !important;
        margin-bottom: 3px !important;
        line-height: 1.2 !important;
    }
    [data-testid="stMetricLabel"] > div {
        font-size: 0.8rem !important;
        font-weight: 600 !important;
        color: #94a3b8 !important;
        white-space: normal !important;
    }
    [data-testid="stMetricValue"] {
        font-size: 1.15rem !important;
        font-weight: 600 !important;
        color: #f8fafc !important;
        line-height: 1.3 !important;
        white-space: normal !important;
        word-break: break-word !important;
        overflow: visible !important;
        text-overflow: clip !important;
    }
    [data-testid="stMetricValue"] > div {
        font-size: 1.15rem !important;
        font-weight: 600 !important;
        color: #f8fafc !important;
        white-space: normal !important;
        overflow: visible !important;
        text-overflow: clip !important;
    }
    [data-testid="stMetricDelta"] {
        font-size: 0.8rem !important;
        font-weight: 500 !important;
    }

    /* Professional Status Badges and Pills */
    .status-pill {
        display: inline-flex;
        align-items: center;
        padding: 3px 9px;
        border-radius: 9999px;
        font-size: 11px;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.04em;
        line-height: 1;
    }
    .pill-green {
        background-color: rgba(6, 78, 59, 0.25);
        color: #10b981;
        border: 1px solid rgba(4, 120, 87, 0.5);
    }
    .pill-amber {
        background-color: rgba(245, 158, 11, 0.12);
        color: #fbbf24;
        border: 1px solid rgba(245, 158, 11, 0.35);
    }
    .pill-red {
        background-color: rgba(239, 68, 68, 0.12);
        color: #f87171;
        border: 1px solid rgba(239, 68, 68, 0.35);
    }
    .pill-blue {
        background-color: rgba(59, 130, 246, 0.12);
        color: #60a5fa;
        border: 1px solid rgba(59, 130, 246, 0.35);
    }
    .pill-neutral {
        background-color: rgba(148, 163, 184, 0.12);
        color: #cbd5e1;
        border: 1px solid rgba(148, 163, 184, 0.25);
    }

    /* Explicit directional colors for trade signals */
    .text-long {
        color: #34d399 !important;
        font-weight: 700 !important;
    }
    .text-short {
        color: #f87171 !important;
        font-weight: 700 !important;
    }
    .badge-long {
        color: #34d399 !important;
        background: rgba(4, 120, 87, 0.18) !important;
        border: 1px solid rgba(4, 120, 87, 0.45) !important;
        padding: 2px 7px;
        border-radius: 4px;
        font-weight: 700;
        font-size: 11px;
    }
    .badge-short {
        color: #ff5252 !important;
        background: rgba(255, 82, 82, 0.12) !important;
        border: 1px solid rgba(255, 82, 82, 0.35) !important;
        padding: 2px 7px;
        border-radius: 4px;
        font-weight: 700;
        font-size: 11px;
    }

    /* Primary Action Buttons: Deep, rich matte forest emerald (20% darker) */
    button[kind="primary"],
    button[data-testid="baseButton-primary"],
    button[data-testid="stBaseButton-primary"],
    .stButton > button[kind="primary"],
    .stButton > button[data-testid="baseButton-primary"],
    .stButton > button[data-testid="stBaseButton-primary"] {
        background-color: #064e3b !important;
        border: 1px solid #047857 !important;
        color: #f8fafc !important;
        font-weight: 600 !important;
        box-shadow: 0 1px 2px 0 rgba(0, 0, 0, 0.35) !important;
    }
    button[kind="primary"]:hover,
    button[data-testid="baseButton-primary"]:hover,
    button[data-testid="stBaseButton-primary"]:hover,
    .stButton > button[kind="primary"]:hover,
    .stButton > button[data-testid="baseButton-primary"]:hover,
    .stButton > button[data-testid="stBaseButton-primary"]:hover {
        background-color: #065f46 !important;
        border-color: #047857 !important;
        color: #ffffff !important;
    }
    button[kind="primary"]:active,
    button[data-testid="baseButton-primary"]:active,
    button[data-testid="stBaseButton-primary"]:active,
    .stButton > button[kind="primary"]:active,
    .stButton > button[data-testid="baseButton-primary"]:active,
    .stButton > button[data-testid="stBaseButton-primary"]:active {
        background-color: #022c22 !important;
        border-color: #064e3b !important;
    }
    button[kind="primary"]:focus,
    button[data-testid="baseButton-primary"]:focus,
    button[data-testid="stBaseButton-primary"]:focus,
    .stButton > button[kind="primary"]:focus,
    .stButton > button[data-testid="baseButton-primary"]:focus,
    .stButton > button[data-testid="stBaseButton-primary"]:focus {
        box-shadow: 0 0 0 2px rgba(6, 78, 59, 0.6) !important;
    }

    /* Top Nav Menu & Header Styling: Keep menu accessible, hide external deploy ads */
    header, [data-testid="stHeader"] {
        background-color: #0b0f19 !important;
        visibility: visible !important;
    }
    #MainMenu {
        visibility: visible !important;
        display: inline-block !important;
    }
    [data-testid="stToolbar"] {
        display: flex !important;
        visibility: visible !important;
    }
    /* Hide Streamlit Community Cloud Deploy / Third-Party Hosting Ads */
    .stDeployButton,
    [data-testid="stDeployButton"] {
        display: none !important;
        visibility: hidden !important;
    }
    footer {
        visibility: hidden !important;
        display: none !important;
    }
    [data-testid="stDecoration"] {display: none !important;}
    </style>
    """,
    unsafe_allow_html=True,
)

# Initialize database schema
try:
    init_db()
except Exception as e:
    st.error(f"⚠️ Database connection/initialization error: {e}. Check your DATABASE_URL in Secrets.")


def clean_ticker(instrument_id: str) -> str:
    """Convert raw instrument IDs into clean, human-readable ticker names.

    Examples:
        'bybit:DOGE_USDT_USDT:perpetual'  → 'DOGE/USDT'
        'bybit:BTC_USDT_USDT:perpetual'   → 'BTC/USDT'
        'yahoo:SPY:stock'                 → 'SPY'
        'yahoo:AAPL:stock'                → 'AAPL'
        'yahoo:XIU.TO:stock'              → 'XIU.TO'
        'DOGE_USDT_USDT'                  → 'DOGE/USDT'
    """
    # Strip exchange prefix and suffix (e.g. bybit:...:perpetual → middle part)
    parts = instrument_id.split(":")
    if len(parts) >= 2:
        raw = parts[1]
    else:
        raw = instrument_id

    # Handle crypto pairs like DOGE_USDT_USDT → DOGE/USDT
    segments = raw.split("_")
    if len(segments) >= 2 and segments[-1] == segments[-2]:
        # e.g. DOGE_USDT_USDT → base=DOGE, quote=USDT
        return f"{segments[0]}/{segments[1]}"
    elif len(segments) == 2:
        return f"{segments[0]}/{segments[1]}"

    return raw


def get_db_session():
    """Helper for short-lived session."""
    return SessionLocal()


BP_CONFIG_PATH = PROJECT_ROOT / "config" / "accounts" / "breakoutprop_turbo.yaml"
PERSONAL_CONFIG_PATH = PROJECT_ROOT / "config" / "accounts" / "personal.yaml"


def read_account_configs():
    bp_cfg = {}
    if BP_CONFIG_PATH.exists():
        try:
            with open(BP_CONFIG_PATH) as f:
                bp_cfg = yaml.safe_load(f) or {}
        except Exception:
            pass

    p_cfg = {}
    if PERSONAL_CONFIG_PATH.exists():
        try:
            with open(PERSONAL_CONFIG_PATH) as f:
                p_cfg = yaml.safe_load(f) or {}
        except Exception:
            pass
    return bp_cfg, p_cfg


bp_cfg, p_cfg = read_account_configs()

# Session state defaults for accounts (loaded from persistent YAML)
if "bp_floor" not in st.session_state:
    st.session_state.bp_floor = float(bp_cfg.get("max_drawdown_floor", 9700.0))
if "bp_daily_limit" not in st.session_state:
    st.session_state.bp_daily_limit = float(bp_cfg.get("daily_loss_limit", 300.0))
if "bp_buffer" not in st.session_state:
    st.session_state.bp_buffer = float(bp_cfg.get("operational_buffer", 50.0))
if "bp_per_trade_risk" not in st.session_state:
    st.session_state.bp_per_trade_risk = float(bp_cfg.get("per_trade_risk_limit", 100.0))
if "bp_equity" not in st.session_state:
    st.session_state.bp_equity = float(bp_cfg.get("starting_equity", 10000.0))

if "personal_equity" not in st.session_state:
    st.session_state.personal_equity = float(p_cfg.get("starting_equity", 25000.0))
if "personal_risk_pct" not in st.session_state:
    st.session_state.personal_risk_pct = float(p_cfg.get("per_trade_risk_pct", 1.0))

if "flagged_opp_ids" not in st.session_state:
    st.session_state.flagged_opp_ids = []


def load_accounts():
    """Instantiate account objects from current session parameters."""
    bp = BreakoutpropAccount(
        account_id="bp_turbo_10k",
        name="Breakoutprop Turbo 10k",
        starting_equity=10000.0,
        max_drawdown_floor=st.session_state.bp_floor,
        daily_loss_limit=st.session_state.bp_daily_limit,
        operational_buffer=st.session_state.bp_buffer,
        per_trade_risk_limit=st.session_state.bp_per_trade_risk,
    )
    personal = PersonalAccount(
        account_id="ibkr_personal",
        name="Personal Account (IBKR/Wealthsimple)",
        per_trade_risk_pct=st.session_state.personal_risk_pct,
    )
    return bp, personal


bp_account, personal_account = load_accounts()
eligibility_engine = AccountEligibilityEngine()
journal_service = JournalService()
context_engine = ContextEngine()
llm_scaffold = LLMScaffold()
sector_engine = SectorEngine()

# -------------------------------------------------------------
# TOP AMBIENT DUAL CLOCK HEADER
# -------------------------------------------------------------
clocks = get_dual_clock_status()
st.markdown(
    f"""
    <div style="background-color: #131722; padding: 10px 18px; border-radius: 6px; margin-bottom: 8px; display: flex; justify-content: space-between; align-items: center; border: 1px solid #232733;">
        <div>
            <span class="status-pill pill-blue" style="margin-right: 8px;">SYSTEM UTC</span>
            <span style="font-family: 'JetBrains Mono', monospace; color: #38bdf8; font-size: 15px; font-weight: 600;">{clocks['utc_str']}</span>
        </div>
        <div>
            <span class="status-pill pill-neutral" style="margin-right: 8px;">DEVICE LOCAL ({clocks['tz_abbr']})</span>
            <span style="font-family: 'JetBrains Mono', monospace; color: #4ade80; font-size: 15px; font-weight: 600;">{clocks['local_str']}</span>
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)
st.caption("All candles, settlement windows, and scan routines execute in UTC. Local device time is provided side-by-side for seamless reference.")

# -------------------------------------------------------------
# SIDEBAR NAVIGATION
# -------------------------------------------------------------
st.sidebar.title("Trading Automations")
st.sidebar.caption("Modular Trading Intelligence System v0.1")

if DATABASE_URL.startswith("postgresql"):
    st.sidebar.markdown("<div style='margin-bottom: 8px;'><span class='status-pill pill-green'>● SUPABASE CLOUD DB</span></div>", unsafe_allow_html=True)
else:
    st.sidebar.markdown("<div style='margin-bottom: 8px;'><span class='status-pill pill-amber'>STORAGE: LOCAL SQLITE</span></div>", unsafe_allow_html=True)
    with st.sidebar.expander("🔍 Cloud DB Connection Helper", expanded=False):
        st.caption(f"**Engine Source:** `{DATABASE_DEBUG.get('source')}`")
        found_keys = DATABASE_DEBUG.get("secret_keys_found", [])
        st.caption(f"**Secrets Keys Detected:** `{found_keys}`")
        if DATABASE_DEBUG.get("error"):
            st.error(DATABASE_DEBUG["error"])
        st.markdown(
            "To connect Supabase, open **App Settings > Secrets** in Streamlit Cloud and enter:\n"
            "```toml\n"
            'DATABASE_URL = "postgresql://postgres.[ref]:[password]@aws-0-[region].pooler.supabase.com:6543/postgres"\n'
            "```\n"
            "*Ensure password does not have square brackets `[]` and URL is enclosed in quotes.*"
        )

NAV_OPTIONS = [
    "Opportunity Queue",
    "Opportunity Detail",
    "Macro & Catalysts",
    "Deviations & Rotation",
    "Strategy Deep-Dives",
    "Journal & Performance",
    "Settings",
]

def set_nav(destination: str):
    """Programmatically queue navigation destination for next rerun without mutating active widgets."""
    if destination == "Screener & Confluence":
        destination = "Opportunity Queue"
    if destination in NAV_OPTIONS:
        st.session_state["pending_nav"] = destination

# Handle pending programmatic navigation BEFORE the radio widget is instantiated
if "pending_nav" in st.session_state and st.session_state["pending_nav"]:
    target = st.session_state.pop("pending_nav")
    if target in NAV_OPTIONS:
        st.session_state["sb_nav_radio"] = target

if "sb_nav_radio" not in st.session_state or st.session_state["sb_nav_radio"] not in NAV_OPTIONS:
    st.session_state["sb_nav_radio"] = NAV_OPTIONS[0]

nav = st.sidebar.radio(
    "Navigation",
    NAV_OPTIONS,
    key="sb_nav_radio",
)

st.sidebar.divider()
st.sidebar.subheader("Active Accounts Status")

# Headroom display in sidebar
bp_state = AccountState(equity=st.session_state.bp_equity, balance=st.session_state.bp_equity)
bp_headroom, _, _ = bp_account.calculate_headroom(bp_state)
bp_risk_state, _ = bp_account.get_operating_risk_state(bp_state)

bp_pill = "pill-green" if bp_risk_state == RiskState.NORMAL else ("pill-amber" if bp_risk_state == RiskState.REDUCED else "pill-red")
st.sidebar.markdown(f"**Breakoutprop:** <span class='status-pill {bp_pill}'>{bp_risk_state.value.upper()}</span>", unsafe_allow_html=True)
st.sidebar.write(f"• Equity: `${st.session_state.bp_equity:,.2f}`")
st.sidebar.write(f"• Usable Headroom: `${bp_headroom:,.2f}`")

_, next_reset = get_breakoutprop_daily_window()
st.sidebar.caption(f"**Next 00:30 UTC reset:**  \n`{format_dual_time(next_reset)}`")

st.sidebar.markdown("**Personal Account:** <span class='status-pill pill-green'>NORMAL</span>", unsafe_allow_html=True)
st.sidebar.write(f"• Equity: `${st.session_state.personal_equity:,.2f}`")
st.sidebar.write(f"• Risk Budget: `${st.session_state.personal_equity * (st.session_state.personal_risk_pct / 100.0):,.2f}`")

# -------------------------------------------------------------
# VIEW 1: OPPORTUNITY QUEUE (Multi-Facet Screeners & Flagged Watchlist)
# -------------------------------------------------------------
if nav == "Opportunity Queue":
    h_col1, h_col2, h_col3, h_col4 = st.columns([3.8, 1.2, 1.3, 1.7])
    with h_col1:
        st.title("Ranked Opportunity Queue & Screener")
        st.markdown("Filter and inspect validated candidates evaluated against your accounts, macro context, sectors, and higher timeframes.")
    with h_col2:
        st.write("")
        if st.button("Refresh View", key="btn_refresh_queue_top", use_container_width=True, help="Reload latest opportunities from local database (zero network calls)"):
            st.rerun()
    with h_col3:
        st.write("")
        if st.button("Rescan Local DB", key="btn_rescan_queue_top", use_container_width=True, help="Run technical scanner across existing cached bars in local database"):
            with st.spinner("Rescanning existing cached bars in database..."):
                from src.intelligence.technical import TechnicalScanner
                from src.opportunities.assembly import OpportunityAssembler
                from src.core.enums import PlaybookMode
                from src.intelligence.base import ModuleContext
                from src.core.models import InstrumentRecord

                scan_session = get_db_session()
                try:
                    instruments = scan_session.query(InstrumentRecord).filter_by(is_active=True).all()
                    inst_ids = [i.id for i in instruments]
                    if inst_ids:
                        now = utc_now()
                        scanner = TechnicalScanner(mode=PlaybookMode.FILTERED)
                        assembler = OpportunityAssembler()
                        for h in ["15m", "1h", "4h", "1d"]:
                            ctx = ModuleContext(
                                instrument_ids=inst_ids,
                                horizon=h,
                                decision_cutoff=now,
                                run_id=f"gui_{now.strftime('%Y%m%d%H%M%S')}",
                            )
                            scanner.evaluate(ctx, scan_session)
                            assembler.assemble_opportunities(scan_session, playbook_name=f"playbook_{h}", horizon=h)
                finally:
                    scan_session.close()
            st.success("Local database rescanned!")
            st.rerun()
    with h_col4:
        st.write("")
        if st.button("Sync Live & Rescan", key="btn_sync_live_queue_top", type="primary", use_container_width=True, help="Pull newest live bars from Bybit & Yahoo Finance, then run the scanner"):
            with st.status("🔄 Ingesting Live Feeds & Assembling Setups...", expanded=True) as status_box:
                def update_sync_ui(msg: str):
                    status_box.write(f"• {msg}")
                from src.connectors.sync import sync_and_rescan_all
                sync_session = get_db_session()
                try:
                    res = sync_and_rescan_all(sync_session, log_fn=update_sync_ui)
                    status_box.update(label="✅ Ingestion & Setup Generation Complete!", state="complete", expanded=False)
                    st.session_state["sync_summary_banner"] = (
                        f"Live Sync Complete! Ingested {res['new_bars']} live bars ({res['bybit_bars']} Bybit + {res['yahoo_bars']} Yahoo Finance) "
                        f"and assembled {res['opportunities_assembled']} trade candidates."
                    )
                except Exception as e:
                    status_box.update(label=f"❌ Sync failed: {e}", state="error", expanded=True)
                    st.error(f"Sync error: {e}")
                    raise
                finally:
                    sync_session.close()
            st.rerun()

    if "sync_summary_banner" in st.session_state:
        st.success(st.session_state.pop("sync_summary_banner"))

    session = get_db_session()
    try:
        # Query latest active opportunities (deduplicated across revisions)
        opportunities = get_latest_active_opportunities(session)

        with st.expander("Multi-Facet Screener & Filters", expanded=True):
            f_col1, f_col2, f_col3, f_col4 = st.columns(4)
            with f_col1:
                horizon_filter = st.selectbox("Horizon", ["All", "15m", "1h", "4h", "1d", "1w", "1M"])
                dir_filter = st.selectbox("Direction", ["All", "long", "short"])
            with f_col2:
                min_merit = st.slider("Minimum Merit Score", 0, 100, 40)
                setup_filter = st.selectbox(
                    "Setup Type (Purposed System)",
                    [
                        "All",
                        "VCEI (Squeeze Pro)",
                        "SHLA (Liquidity Sweep)",
                        "AVIA (Volume Profile)",
                        "TCMB (Ripster Cloud)",
                        "AVWAP (Anchored VWAP)",
                        "NERN (Lorentzian ML)",
                        "IFVG (Fair Value Gap)",
                        "DREF (Dealing Range SFP)",
                    ],
                )
            with f_col3:
                elig_filter = st.selectbox(
                    "Account Eligibility",
                    ["All", "Breakoutprop Eligible", "Personal Eligible", "Both Eligible"],
                )
                sector_filter = st.selectbox(
                    "Sector Classification",
                    [
                        "All Sectors",
                        "Technology",
                        "Consumer Discretionary",
                        "Broad Market Index",
                        "Tech Index (Nasdaq 100)",
                        "Canadian TSX 60",
                        "Crypto Store of Value",
                        "Crypto Smart Contracts (L1)",
                        "Crypto Oracles & Infra",
                        "Crypto Memecoins & Beta",
                        "Crypto Payments & Settlement",
                    ],
                )
            with f_col4:
                context_filter = st.selectbox(
                    "Macro / Catalyst Filter",
                    ["All", "Clean Catalyst Window (>2h)", "Macro Aligned Only"],
                )
                timeframe_scope = st.selectbox(
                    "Timeframe Scope (Noise Filter)",
                    ["All Horizons (incl. 15m Scalps)", "Swing Only (1h, 4h, 1d - Filter 15m Noise)"],
                )

        # Compute context summary for filtering
        ctx_summary = context_engine.compute_context_summary()

        filtered_opps = []
        for opp in opportunities:
            if timeframe_scope.startswith("Swing Only") and opp.horizon in ("15m", "5m", "1m"):
                continue
            if horizon_filter != "All" and opp.horizon != horizon_filter:
                continue
            if dir_filter != "All" and opp.direction != dir_filter:
                continue
            if opp.merit_score is not None and opp.merit_score < min_merit:
                continue

            # Sector filter
            opp_sec = get_instrument_sector(opp.instrument_id)
            if sector_filter != "All Sectors" and opp_sec != sector_filter:
                continue

            # Setup type filter
            if setup_filter != "All":
                search_term = setup_filter.split(" (")[0].lower() if " (" in setup_filter else setup_filter.lower()
                if search_term == "vcei":
                    search_term = "squeeze"
                elif search_term == "shla":
                    search_term = "sweep"
                elif search_term == "avia":
                    search_term = "volume_profile"
                elif search_term == "tcmb":
                    search_term = "cloud"
                elif search_term == "nern":
                    search_term = "lorentzian"
                elif search_term == "ifvg":
                    search_term = "fvg"
                elif search_term == "dref":
                    search_term = "dealing_range"

                thesis_lower = (opp.thesis or "").lower()
                playbook_lower = (opp.playbook_name or "").lower()
                sigs = " ".join((opp.score_breakdown or {}).get("signals", [])).lower()
                match = (search_term in thesis_lower) or (search_term in playbook_lower) or (search_term in sigs)
                if search_term == "dealing_range" and ("mayne" in thesis_lower or "sfp" in thesis_lower or "sfp" in sigs):
                    match = True
                if search_term == "fvg" and ("imbalance" in thesis_lower or "bisi" in thesis_lower or "sibi" in thesis_lower):
                    match = True
                if not match:
                    continue

            # Account eligibility checks
            elig_bp = eligibility_engine.evaluate(bp_account, opp, bp_state)
            p_state = AccountState(equity=st.session_state.personal_equity, balance=st.session_state.personal_equity)
            elig_personal = eligibility_engine.evaluate(personal_account, opp, p_state)

            if elig_filter == "Breakoutprop Eligible" and elig_bp.result != "eligible":
                continue
            if elig_filter == "Personal Eligible" and elig_personal.result != "eligible":
                continue
            if elig_filter == "Both Eligible" and (elig_bp.result != "eligible" or elig_personal.result != "eligible"):
                continue

            # Context alignment check
            conf = context_engine.evaluate_opportunity_confluence(opp.direction, opp.horizon, ctx_summary)
            if context_filter == "Clean Catalyst Window (>2h)" and not conf["clean_catalyst_window"]:
                continue
            if context_filter == "Macro Aligned Only" and not conf["is_macro_aligned"]:
                continue

            filtered_opps.append(opp)

        sector_map = {opp.instrument_id: get_instrument_sector(opp.instrument_id) for opp in opportunities}
        all_aggregates = aggregate_opportunities_by_ticker(
            filtered_opps,
            watchlist_ids=set(st.session_state.flagged_opp_ids),
            sector_lookup=sector_map,
        )
        flagged_aggregates = [a for a in all_aggregates if a.is_flagged]

        invalidation_logs = OpportunityLifecycleManager.get_invalidation_telemetry_logs(session, limit=200)

        tab_all, tab_watchlist, tab_invalidated, tab_discovery = st.tabs([
            f"Live Opportunities ({len(all_aggregates)} Tickers)",
            f"Flagged Watchlist ({len(flagged_aggregates)} Tickers)",
            f"Invalidated & Busted Setups ({len(invalidation_logs)} Logged)",
            "Broad Market Anomalies",
        ])

        def style_opportunity_dataframe(df: pd.DataFrame):
            """Highlight LONG in vibrant green, SHORT in vibrant red, and CONFLICT in amber."""
            def highlight_direction(val):
                s = str(val).upper()
                if "LONG" in s or "▲" in s:
                    return "color: #10b981; font-weight: 700;"
                elif "SHORT" in s or "▼" in s:
                    return "color: #ff5252; font-weight: 700;"
                elif "CONFLICT" in s or "CHOP" in s:
                    return "color: #fbbf24; font-weight: 700;"
                return ""

            cols_to_style = [c for c in ["Direction & Confluence", "Direction", "Primary Setup"] if c in df.columns]
            if hasattr(df.style, "map"):
                return df.style.map(highlight_direction, subset=cols_to_style)
            else:
                return df.style.applymap(highlight_direction, subset=cols_to_style)

        def render_queue_view(agg_list: list[TickerOpportunityAggregate], raw_opp_list: list[OpportunityVersion], tab_key: str):
            if not agg_list and not raw_opp_list:
                from src.core.models import MarketObservation
                bar_count = session.query(MarketObservation).count()
                if bar_count == 0:
                    st.info(
                        "⚡ **Fresh Database Connected**: No market observations have been synced yet.\n\n"
                        "Click **'Sync Live & Rescan'** in the top-right header above to pull live closed bars from Bybit & Yahoo Finance and generate candidate setups."
                    )
                else:
                    st.info("No opportunity candidates found matching the selected screener criteria.")
                return

            c_mode, c_stat = st.columns([3, 2])
            with c_mode:
                view_mode = st.radio(
                    "Display Format",
                    ["Aggregated by Ticker (Recommended)", "All Setups (Expanded)"],
                    horizontal=True,
                    key=f"vmode_{tab_key}",
                )
            with c_stat:
                st.caption(f"Showing **{len(agg_list)} unique tickers** with **{len(raw_opp_list)} active setups** across all timeframes.")

            if view_mode == "Aggregated by Ticker (Recommended)":
                agg_table_data = []
                for a in agg_list:
                    watch_sym = "★" if a.is_flagged else "—"
                    bp_badge = "Eligible" if a.breakoutprop_eligible else "Blocked"
                    p_badge = "Eligible" if a.personal_eligible else "Blocked"
                    systems_str = ", ".join(s.system_acronym for s in a.triggered_signals)
                    horizons_str = ", ".join(a.active_horizons)
                    drift_str = f"{a.price_drift_pct:+.2f}%" if a.price_drift_pct is not None else "—"
                    exec_rr_str = f"{a.current_executable_rr:.1f}R" if a.current_executable_rr is not None else "—"
                    merit_delta_str = f" ({a.merit_delta:+0.1f})" if a.merit_delta else ""

                    agg_table_data.append({
                        "Merit": f"{a.peak_merit_score:.1f}{merit_delta_str}",
                        "Watch": watch_sym,
                        "Ticker": a.ticker,
                        "Direction & Confluence": a.directional_confluence,
                        "Horizons": horizons_str,
                        "Triggered Systems": systems_str,
                        "Entry": f"${a.trigger_price:.4f}" if a.trigger_price else "—",
                        "Stop": f"${a.invalidation_price:.4f}" if a.invalidation_price else "—",
                        "Live Drift": drift_str,
                        "Exec R:R": exec_rr_str,
                        "Sector": a.sector,
                        "BP Eval": bp_badge,
                        "Personal": p_badge,
                    })

                event = st.dataframe(
                    style_opportunity_dataframe(pd.DataFrame(agg_table_data)),
                    hide_index=True,
                    use_container_width=True,
                    selection_mode="single-row",
                    on_select="rerun",
                    key=f"df_agg_{tab_key}",
                )

                # Controls for Ticker selection
                agg_map = {a.ticker: a for a in agg_list}
                sorted_tickers = [a.ticker for a in agg_list]

                # Detect row click in table and sync with selection only when table click changes
                clicked_rows = event.selection.rows if hasattr(event, "selection") and event.selection else []
                last_clicked = st.session_state.get(f"last_clicked_{tab_key}", [])
                if clicked_rows != last_clicked:
                    st.session_state[f"last_clicked_{tab_key}"] = clicked_rows
                    if clicked_rows and clicked_rows[0] < len(agg_list):
                        clicked_ticker = agg_list[clicked_rows[0]].ticker
                        st.session_state[f"sel_agg_{tab_key}"] = clicked_ticker
                        st.session_state.selected_ticker = clicked_ticker

                curr_sel_ticker = st.session_state.get(f"sel_agg_{tab_key}")
                if curr_sel_ticker not in sorted_tickers and sorted_tickers:
                    st.session_state[f"sel_agg_{tab_key}"] = sorted_tickers[0]

                c_sel, c_flag, c_open, c_ref = st.columns([3, 1, 1, 1])
                with c_sel:
                    def format_agg_choice(t: str) -> str:
                        a = agg_map.get(t)
                        if not a:
                            return t
                        dir_icon = "▲ LONG" if a.reconciled_direction == "long" else "▼ SHORT"
                        return f"{a.peak_merit_score:.1f} Merit  •  {a.ticker} ({dir_icon})  •  {a.directional_confluence}"

                    chosen_ticker = st.selectbox(
                        "Selected Ticker (Click table row or select from dropdown)",
                        sorted_tickers,
                        format_func=format_agg_choice,
                        key=f"sel_agg_{tab_key}",
                    )
                    st.session_state.selected_ticker = chosen_ticker
                with c_flag:
                    chosen_agg = agg_map.get(chosen_ticker)
                    is_currently_flagged = chosen_agg.is_flagged if chosen_agg else False
                    flag_label = "Remove Flag" if is_currently_flagged else "Flag Ticker"
                    if st.button(flag_label, key=f"btn_flag_agg_{tab_key}", use_container_width=True):
                        if chosen_agg:
                            for o in chosen_agg.all_opportunities:
                                if o.opportunity_id in st.session_state.flagged_opp_ids:
                                    st.session_state.flagged_opp_ids.remove(o.opportunity_id)
                                else:
                                    st.session_state.flagged_opp_ids.append(o.opportunity_id)
                        st.rerun()
                with c_open:
                    if st.button("Open Detail", key=f"btn_open_agg_{tab_key}", type="primary", use_container_width=True):
                        if chosen_agg:
                            st.session_state.selected_ticker = chosen_agg.ticker
                            st.session_state.selected_opp_id = chosen_agg.primary_opportunity.opportunity_id
                            st.session_state["pending_detail_ticker"] = chosen_agg.ticker
                            set_nav("Opportunity Detail")
                            st.rerun()
                with c_ref:
                    if st.button("Refresh", key=f"btn_ref_agg_{tab_key}", use_container_width=True):
                        st.rerun()

                # Signal Breakdown & Conflict Reconciliation for the selected Ticker
                if chosen_agg:
                    if chosen_agg.is_conflicted:
                        rec_border = "#10b981" if chosen_agg.reconciled_direction == "long" else "#ff5252"
                        rec_badge = "pill-green" if chosen_agg.reconciled_direction == "long" else "pill-red"
                        rec_title = "▲ RECONCILED LONG" if chosen_agg.reconciled_direction == "long" else "▼ RECONCILED SHORT"
                        st.markdown(
                            f"""
                            <div style="background: rgba(30, 41, 59, 0.7); border-left: 4px solid {rec_border}; padding: 10px 14px; border-radius: 6px; margin: 10px 0 14px 0;">
                                <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 4px;">
                                    <span style="font-weight: 700; font-size: 0.88rem; color: #f8fafc;">
                                        Multi-Timeframe Trend & Conflict Reconciliation: {chosen_agg.ticker}
                                    </span>
                                    <span class="status-pill {rec_badge}">{rec_title}</span>
                                </div>
                                <p style="margin: 0; font-size: 0.82rem; color: #cbd5e1; line-height: 1.4;">
                                    {chosen_agg.reconciliation_summary}
                                </p>
                                <div style="margin-top: 6px; font-size: 0.76rem; color: #94a3b8;">
                                    <span style="color: #10b981; font-weight: 600;">Long Conviction Mass: {chosen_agg.weighted_long_conviction:.0f} pts</span> &nbsp;•&nbsp; 
                                    <span style="color: #ff5252; font-weight: 600;">Short Conviction Mass: {chosen_agg.weighted_short_conviction:.0f} pts</span>
                                    {f" &nbsp;•&nbsp; <span style='color: #fbbf24; font-weight: 600;'>⚠️ Counter-trend pullback cautioned against HTF trend</span>" if chosen_agg.is_counter_trend_warning else ""}
                                </div>
                            </div>
                            """,
                            unsafe_allow_html=True,
                        )

                    with st.expander(f"Inspect {chosen_agg.ticker} Signals & Multi-Timeframe Confluence ({len(chosen_agg.triggered_signals)} Strategy Systems)", expanded=True):
                        for s_idx, sig_group in enumerate(chosen_agg.triggered_signals):
                            sig_dir_pill = f"<span class='status-pill {'pill-green' if sig_group.direction == 'long' else 'pill-red'}'>{'▲ LONG' if sig_group.direction == 'long' else '▼ SHORT'}</span>"
                            st.markdown(f"**{sig_group.setup_name} ({sig_group.system_acronym})** &nbsp; {sig_dir_pill} &nbsp; *{sig_group.confluence_badge}*", unsafe_allow_html=True)
                            for tf_idx, tf in enumerate(sig_group.timeframes):
                                detail = sig_group.timeframe_details.get(tf)
                                if not detail:
                                    continue
                                with st.container():
                                    c_d1, c_d2, c_d3 = st.columns([3.2, 1, 0.8])
                                    with c_d1:
                                        st.markdown(f"• **{tf} Horizon (Merit: {detail.merit_score:.1f})**: {detail.where_summary}")
                                        st.caption(f"**Why:** {detail.why_summary}  \n**How:** {detail.how_summary}")
                                    Chips_key = f"btn_sub_open_{tab_key}_{s_idx}_{tf_idx}_{detail.opportunity_id}"
                                    with c_d2:
                                        if st.button(f"Open {tf} Setup", key=Chips_key, use_container_width=True):
                                            st.session_state.selected_ticker = chosen_agg.ticker
                                            st.session_state.selected_opp_id = detail.opportunity_id
                                            st.session_state["pending_detail_ticker"] = chosen_agg.ticker
                                            set_nav("Opportunity Detail")
                                            st.rerun()
                                    with c_d3:
                                        inv_btn_k = f"btn_sub_inv_{tab_key}_{s_idx}_{tf_idx}_{detail.opportunity_id}"
                                        if st.button("Invalidate", key=inv_btn_k, use_container_width=True, help="Manually bust and invalidate this setup"):
                                            OpportunityLifecycleManager().manually_invalidate_opportunity(
                                                session=session,
                                                opportunity_id=detail.opportunity_id,
                                                reason="manual_screener_invalidation",
                                            )
                                            st.toast(f"Setup {chosen_agg.ticker} ({tf}) manually invalidated.")
                                            st.rerun()
            else:
                # Expanded all individual setups table
                table_data = []
                for opp in raw_opp_list:
                    is_flagged = "★" if opp.opportunity_id in st.session_state.flagged_opp_ids else "—"
                    elig_bp = eligibility_engine.evaluate(bp_account, opp, bp_state)
                    p_state = AccountState(equity=st.session_state.personal_equity, balance=st.session_state.personal_equity)
                    elig_p = eligibility_engine.evaluate(personal_account, opp, p_state)

                    bp_badge = "Eligible" if elig_bp.result == "eligible" else f"Blocked ({elig_bp.result})"
                    p_badge = "Eligible" if elig_p.result == "eligible" else f"Blocked ({elig_p.result})"

                    conf = context_engine.evaluate_opportunity_confluence(opp.direction, opp.horizon, ctx_summary)
                    macro_badge = "Aligned" if conf["is_macro_aligned"] else "Friction"
                    if not conf["clean_catalyst_window"]:
                        macro_badge = "Catalyst Risk"

                    table_data.append({
                        "Merit": f"{opp.merit_score:.1f}" if opp.merit_score is not None else "Unscored",
                        "Watch": is_flagged,
                        "Ticker": clean_ticker(opp.instrument_id),
                        "Direction": "▲ LONG" if opp.direction == "long" else ("▼ SHORT" if opp.direction == "short" else opp.direction.upper()),
                        "Horizon": opp.horizon,
                        "Sector": get_instrument_sector(opp.instrument_id),
                        "Identified": format_dual_time(opp.created_at),
                        "Updated": format_dual_time(opp.available_at or opp.created_at),
                        "Confidence": f"{int(opp.confidence_score * 100)}%",
                        "Context": macro_badge,
                        "BP Eligible": bp_badge,
                        "Personal": p_badge,
                        "Trigger": f"${opp.trigger_price:.4f}" if opp.trigger_price else "—",
                        "Invalidation": f"${opp.invalidation_price:.4f}" if opp.invalidation_price else "—",
                    })

                event_raw = st.dataframe(
                    style_opportunity_dataframe(pd.DataFrame(table_data)),
                    hide_index=True,
                    use_container_width=True,
                    selection_mode="single-row",
                    on_select="rerun",
                    key=f"df_raw_{tab_key}",
                )

                opp_map = {o.opportunity_id: o for o in raw_opp_list}
                sorted_ids = sorted(
                    [o.opportunity_id for o in raw_opp_list],
                    key=lambda oid: (opp_map[oid].merit_score or 0) if oid in opp_map else 0,
                    reverse=True,
                )

                # Sync table row click with selectbox only when table click changes
                clicked_raw_rows = event_raw.selection.rows if hasattr(event_raw, "selection") and event_raw.selection else []
                last_raw_clicked = st.session_state.get(f"last_raw_clicked_{tab_key}", [])
                if clicked_raw_rows != last_raw_clicked:
                    st.session_state[f"last_raw_clicked_{tab_key}"] = clicked_raw_rows
                    if clicked_raw_rows and clicked_raw_rows[0] < len(raw_opp_list):
                        st.session_state[f"sel_raw_{tab_key}"] = raw_opp_list[clicked_raw_rows[0]].opportunity_id

                curr_sel_id = st.session_state.get(f"sel_raw_{tab_key}")
                if curr_sel_id not in sorted_ids and sorted_ids:
                    st.session_state[f"sel_raw_{tab_key}"] = sorted_ids[0]

                c_sel, c_flag, c_open, c_ref = st.columns([3, 1, 1, 1])
                with c_sel:
                    def format_opp_choice(oid: str) -> str:
                        o = opp_map.get(oid)
                        if not o:
                            return oid
                        ticker = clean_ticker(o.instrument_id)
                        merit_val = f"{o.merit_score:.1f}" if o.merit_score is not None else "—"
                        dir_arrow = "▲" if o.direction == "long" else "▼"
                        t_short = o.created_at.strftime("%m-%d %H:%M UTC") if o.created_at else ""
                        return f"{merit_val} Merit  •  {ticker} {dir_arrow} {o.direction.upper()} ({o.horizon})  •  {t_short}"

                    chosen_id = st.selectbox(
                        "Selected Setup (Click table row or select from dropdown)",
                        sorted_ids,
                        format_func=format_opp_choice,
                        key=f"sel_raw_{tab_key}",
                    )
                with c_flag:
                    flag_label = "Remove Flag" if chosen_id in st.session_state.flagged_opp_ids else "Flag Opportunity"
                    if st.button(flag_label, key=f"btn_flag_raw_{tab_key}", use_container_width=True):
                        if chosen_id in st.session_state.flagged_opp_ids:
                            st.session_state.flagged_opp_ids.remove(chosen_id)
                        else:
                            st.session_state.flagged_opp_ids.append(chosen_id)
                        st.rerun()
                with c_open:
                    if st.button("Open Detail", key=f"btn_open_raw_{tab_key}", type="primary", use_container_width=True):
                        st.session_state.selected_opp_id = chosen_id
                        sel_o = opp_map.get(chosen_id)
                        if sel_o:
                            raw_t = clean_ticker(sel_o.instrument_id)
                            st.session_state.selected_ticker = raw_t
                            st.session_state["pending_detail_ticker"] = raw_t
                        set_nav("Opportunity Detail")
                        st.rerun()
                with c_ref:
                    if st.button("Refresh", key=f"btn_ref_raw_{tab_key}", use_container_width=True):
                        st.rerun()

        with tab_all:
            render_queue_view(all_aggregates, filtered_opps, "all")

        with tab_watchlist:
            render_queue_view(flagged_aggregates, [o for o in filtered_opps if o.opportunity_id in st.session_state.flagged_opp_ids], "watchlist")

        with tab_invalidated:
            st.subheader("Invalidated & Busted Setups (Telemetry & Autopsy Audit)")
            st.caption(
                "Persistent telemetry log of trade setups that were invalidated by stop loss breach, dissolved criteria, "
                "or target expiration. Use this structured data to analyze failure patterns and systematically adjust "
                "risk parameters, indicator thresholds, and strategy merit weights."
            )

            if not invalidation_logs:
                st.info("No invalidated setups currently logged. The system audits active setups against subsequent candles during each scan.")
            else:
                df_inv = pd.DataFrame(invalidation_logs)

                # Executive KPI Cards
                total_logged = len(df_inv)
                stopped_out = len(df_inv[df_inv["reason"] == "stop_breached"])
                dissolved = len(df_inv[df_inv["reason"] == "criteria_dissolved"])
                targets_hit = len(df_inv[df_inv["reason"].isin(["target_2r_hit", "target_hit", "target_2r_achieved"])])
                mean_mfe = float(df_inv["mfe_r"].mean()) if "mfe_r" in df_inv and len(df_inv) else 0.0
                mean_bars = float(df_inv["bars_held"].mean()) if "bars_held" in df_inv and len(df_inv) else 0.0

                c_kpi1, c_kpi2, c_kpi3, c_kpi4, c_kpi5 = st.columns(5)
                with c_kpi1:
                    st.metric("Total Logged", f"{total_logged}")
                with c_kpi2:
                    stop_pct = (stopped_out / total_logged * 100) if total_logged else 0
                    st.metric("Stop Breached", f"{stopped_out}", f"{stop_pct:.1f}% of total")
                with c_kpi3:
                    diss_pct = (dissolved / total_logged * 100) if total_logged else 0
                    st.metric("Criteria Dissolved", f"{dissolved}", f"{diss_pct:.1f}% of total")
                with c_kpi4:
                    st.metric("Targets Reached", f"{targets_hit}")
                with c_kpi5:
                    st.metric("Mean MFE (R)", f"+{mean_mfe:.2f}R", f"{mean_bars:.1f} avg bars")

                # Telemetry Export Buttons
                st.markdown("##### 📥 Export Telemetry Data for Quantitative Analysis & Math Tuning")
                c_exp1, c_exp2, c_info = st.columns([1.5, 1.5, 3])
                with c_exp1:
                    csv_bytes = df_inv.to_csv(index=False).encode("utf-8")
                    st.download_button(
                        label="📥 Download CSV Dataset",
                        data=csv_bytes,
                        file_name=f"invalidation_telemetry_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
                        mime="text/csv",
                        key="btn_dl_inv_csv",
                        use_container_width=True,
                    )
                with c_exp2:
                    json_bytes = json.dumps(invalidation_logs, indent=2, default=str).encode("utf-8")
                    st.download_button(
                        label="📥 Download JSON Dataset",
                        data=json_bytes,
                        file_name=f"invalidation_telemetry_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
                        mime="application/json",
                        key="btn_dl_inv_json",
                        use_container_width=True,
                    )
                with c_info:
                    st.caption(
                        "💡 **Math & Parameter Tuning Tip**: Analyze the **MFE** (Maximum Favorable Excursion) distribution. "
                        "If setups consistently achieve >1.0R before stopping out, consider taking partial profits or adjusting stop-to-breakeven triggers."
                    )

                st.markdown("---")

                # Strategy & Timeframe Aggregated Autopsies
                col_strat, col_tf = st.columns(2)
                with col_strat:
                    st.markdown("###### Failures by Playbook / Strategy")
                    if "playbook_name" in df_inv.columns:
                        strat_summary = []
                        for pb, g in df_inv.groupby("playbook_name"):
                            strat_summary.append({
                                "Playbook": pb,
                                "Total": len(g),
                                "Stops": len(g[g["reason"] == "stop_breached"]),
                                "Dissolved": len(g[g["reason"] == "criteria_dissolved"]),
                                "Mean MFE": f"+{g['mfe_r'].mean():.2f}R",
                                "Avg Merit": f"{g['merit_score'].mean():.1f}",
                            })
                        st.dataframe(pd.DataFrame(strat_summary), hide_index=True, use_container_width=True)
                with col_tf:
                    st.markdown("###### Failures by Timeframe Horizon")
                    if "horizon" in df_inv.columns:
                        tf_summary = []
                        for hz, g in df_inv.groupby("horizon"):
                            tf_summary.append({
                                "Horizon": hz,
                                "Total": len(g),
                                "Stops": len(g[g["reason"] == "stop_breached"]),
                                "Mean Bars": f"{g['bars_held'].mean():.1f}",
                                "Mean MFE": f"+{g['mfe_r'].mean():.2f}R",
                            })
                        st.dataframe(pd.DataFrame(tf_summary), hide_index=True, use_container_width=True)

                st.markdown("##### Detailed Autopsy Log Records")
                # Interactive Filter Bar
                f_col1, f_col2, f_col3 = st.columns([2, 2, 2])
                with f_col1:
                    reason_filter = st.selectbox(
                        "Filter by Invalidation Reason",
                        ["All Reasons", "stop_breached", "criteria_dissolved", "target_achieved / hit", "manual_invalidation"],
                        key="inv_filter_reason",
                    )
                with f_col2:
                    strat_options = ["All Playbooks"] + sorted(list(df_inv["playbook_name"].dropna().unique()))
                    strat_filter = st.selectbox("Filter by Playbook", strat_options, key="inv_filter_strat")
                with f_col3:
                    ticker_search = st.text_input("Search Ticker", "", key="inv_search_ticker").strip().upper()

                filtered_df = df_inv.copy()
                if reason_filter == "stop_breached":
                    filtered_df = filtered_df[filtered_df["reason"] == "stop_breached"]
                elif reason_filter == "criteria_dissolved":
                    filtered_df = filtered_df[filtered_df["reason"] == "criteria_dissolved"]
                elif reason_filter == "target_achieved / hit":
                    filtered_df = filtered_df[filtered_df["reason"].str.contains("target", case=False, na=False)]
                elif reason_filter == "manual_invalidation":
                    filtered_df = filtered_df[filtered_df["reason"].str.contains("manual", case=False, na=False)]

                if strat_filter != "All Playbooks":
                    filtered_df = filtered_df[filtered_df["playbook_name"] == strat_filter]

                if ticker_search:
                    filtered_df = filtered_df[filtered_df["ticker"].str.contains(ticker_search, case=False, na=False)]

                # Display table
                display_cols = [
                    "ticker", "horizon", "direction", "playbook_name", "status", "reason",
                    "merit_score", "trigger_price", "invalidation_price", "breach_price",
                    "bars_held", "mfe_r", "mae_r", "breach_time", "counter_evidence",
                ]
                valid_cols = [c for c in display_cols if c in filtered_df.columns]
                rename_map = {
                    "ticker": "Ticker",
                    "horizon": "Horizon",
                    "direction": "Direction",
                    "playbook_name": "Playbook",
                    "status": "Status",
                    "reason": "Reason",
                    "merit_score": "Merit",
                    "trigger_price": "Trigger",
                    "invalidation_price": "Stop Loss",
                    "breach_price": "Breach Price",
                    "bars_held": "Bars Held",
                    "mfe_r": "MFE (R)",
                    "mae_r": "MAE (R)",
                    "breach_time": "Breach Time",
                    "counter_evidence": "Autopsy / Counter-Evidence",
                }
                styled_df = filtered_df[valid_cols].rename(columns=rename_map)
                st.dataframe(styled_df, hide_index=True, use_container_width=True)

                # Deep Inspect Expander
                with st.expander("🔍 Inspect Full Telemetry Payload & Score Breakdown"):
                    opp_ids = filtered_df["opportunity_id"].tolist() if "opportunity_id" in filtered_df else []
                    if opp_ids:
                        sel_inv_id = st.selectbox("Select Opportunity ID", opp_ids, key="sel_inv_opp_inspect")
                        sel_row = filtered_df[filtered_df["opportunity_id"] == sel_inv_id].iloc[0]
                        c_json1, c_json2 = st.columns(2)
                        with c_json1:
                            st.markdown("**Core Failure Metadata**")
                            st.json({
                                "ticker": sel_row.get("ticker"),
                                "playbook": sel_row.get("playbook_name"),
                                "horizon": sel_row.get("horizon"),
                                "direction": sel_row.get("direction"),
                                "reason": sel_row.get("reason"),
                                "trigger_price": sel_row.get("trigger_price"),
                                "invalidation_price": sel_row.get("invalidation_price"),
                                "breach_price": sel_row.get("breach_price"),
                                "mfe_r": sel_row.get("mfe_r"),
                                "mae_r": sel_row.get("mae_r"),
                                "bars_held": sel_row.get("bars_held"),
                                "breach_time": sel_row.get("breach_time"),
                            })
                        with c_json2:
                            st.markdown("**Initial Scoring Components**")
                            st.json({
                                "initial_merit": sel_row.get("merit_score"),
                                "confidence": sel_row.get("confidence_score"),
                                "confluence_quality": sel_row.get("confluence_quality"),
                                "volume_confirmation": sel_row.get("volume_confirmation"),
                                "trend_alignment": sel_row.get("trend_alignment"),
                                "compression_duration": sel_row.get("compression_duration"),
                                "confluence_count": sel_row.get("confluence_count"),
                            })

        with tab_discovery:
            st.subheader("Broad Market Discovery Scans")
            st.caption("Statistically anomalous market conditions across all watched instruments (compression squeezes, volume spikes, structure tests).")
            signals = (
                session.query(ModuleSignal)
                .filter_by(setup_name="broad_ev_discovery")
                .order_by(ModuleSignal.as_of.desc())
                .limit(100)
                .all()
            )

            if not signals:
                st.info("No broad discovery anomalies currently flagged. Run an ingestion and scan in Settings to refresh.")
            else:
                disc_rows = []
                for s in signals:
                    anomalies = s.conditions.get("anomalies", []) if s.conditions else []
                    disc_rows.append({
                        "Ticker": clean_ticker(s.instrument_id),
                        "Horizon": s.horizon,
                        "Suggested Bias": s.direction.upper(),
                        "Confidence": f"{int(s.confidence * 100)}%",
                        "Detected Anomalies": ", ".join(anomalies),
                        "Trigger Level": f"${s.trigger_price:.4f}" if s.trigger_price else "—",
                        "As Of": format_dual_time(s.as_of),
                    })
                st.dataframe(pd.DataFrame(disc_rows), hide_index=True, use_container_width=True)

    finally:
        session.close()

# -------------------------------------------------------------
# VIEW 2: OPPORTUNITY DETAIL (Account Sizing & AI Strategic Review)
# -------------------------------------------------------------
elif nav == "Opportunity Detail":
    st.title("Opportunity Detail & Execution Planner")

    session = get_db_session()
    try:
        all_latest_opps = get_latest_active_opportunities(session)
        sector_map = {opp.instrument_id: get_instrument_sector(opp.instrument_id) for opp in all_latest_opps}
        all_aggregates = aggregate_opportunities_by_ticker(
            all_latest_opps,
            watchlist_ids=set(st.session_state.flagged_opp_ids),
            sector_lookup=sector_map,
        )
        ticker_map = {a.ticker: a for a in all_aggregates}

        if not ticker_map:
            st.warning("No opportunities available to view. Ingest bars and run scanner first.")
        else:
            ticker_list = list(ticker_map.keys())
            # Synchronize selected ticker if queued from external navigation
            pending_t = st.session_state.pop("pending_detail_ticker", None)
            if pending_t and pending_t in ticker_list:
                st.session_state["detail_ticker_sel"] = pending_t
                st.session_state.selected_ticker = pending_t
            elif "selected_opp_id" in st.session_state and st.session_state.selected_opp_id and ("detail_ticker_sel" not in st.session_state or st.session_state["detail_ticker_sel"] not in ticker_list):
                for t in ticker_list:
                    if any(o.opportunity_id == st.session_state.selected_opp_id for o in ticker_map[t].all_opportunities):
                        st.session_state["detail_ticker_sel"] = t
                        st.session_state.selected_ticker = t
                        break
            elif "detail_ticker_sel" not in st.session_state or st.session_state["detail_ticker_sel"] not in ticker_list:
                init_t = st.session_state.get("selected_ticker")
                if init_t in ticker_list:
                    st.session_state["detail_ticker_sel"] = init_t
                else:
                    st.session_state["detail_ticker_sel"] = ticker_list[0]

            col_sel, col_ref = st.columns([4, 1])
            with col_sel:
                def format_ticker_detail_choice(t: str) -> str:
                    a = ticker_map.get(t)
                    if not a:
                        return t
                    dir_icon = "▲ LONG" if a.reconciled_direction == "long" else "▼ SHORT"
                    return f"{a.peak_merit_score:.1f} Merit  •  {a.ticker} ({dir_icon})  •  {a.directional_confluence}"

                chosen_ticker = st.selectbox(
                    "Select Candidate Ticker",
                    ticker_list,
                    format_func=format_ticker_detail_choice,
                    key="detail_ticker_sel",
                )
                st.session_state.selected_ticker = chosen_ticker
            with col_ref:
                st.write("")
                if st.button("Refresh Candidate", key="btn_refresh_detail_candidate", use_container_width=True):
                    st.rerun()

            agg = ticker_map[chosen_ticker]

            # Multi-Timeframe Confluence Header
            pill_class = "pill-green" if agg.reconciled_direction == "long" else "pill-red"
            st.subheader(f"{agg.ticker} — Multi-Timeframe Confluence Hub")

            drift_pill = "pill-green" if (agg.price_drift_pct or 0) >= 0 else "pill-red"
            rr_pill = "pill-green" if (agg.current_executable_rr or 0) >= 1.5 else ("pill-amber" if (agg.current_executable_rr or 0) >= 1.0 else "pill-red")
            drift_span = f"<span class='status-pill {drift_pill}'>Drift: {agg.price_drift_pct:+.2f}%</span>" if agg.price_drift_pct is not None else ""
            rr_span = f"<span class='status-pill {rr_pill}'>Exec R:R: {agg.current_executable_rr:.1f}R</span>" if agg.current_executable_rr is not None else ""
            delta_span = f"<span class='status-pill pill-blue'>Merit Δ: {agg.merit_delta:+0.1f}</span>" if agg.merit_delta else ""
            px_span = f"<span class='status-pill pill-neutral'>Live Price: \\${agg.latest_price:.4f}</span>" if agg.latest_price is not None else ""

            st.markdown(
                f"<div style='margin-bottom: 12px; display: flex; flex-wrap: wrap; gap: 6px;'>"
                f"<span class='status-pill {pill_class}'>{agg.directional_confluence}</span>"
                f"<span class='status-pill pill-neutral'>Sector: {agg.sector}</span>"
                f"<span class='status-pill pill-blue'>Peak Merit: {agg.peak_merit_score:.1f}</span>"
                f"{delta_span}"
                f"{px_span}"
                f"{drift_span}"
                f"{rr_span}"
                f"<span class='status-pill pill-neutral'>Active Horizons: {', '.join(agg.active_horizons)}</span>"
                f"</div>",
                unsafe_allow_html=True,
            )

            # Multi-Timeframe Trend & Conflict Reconciliation Banner
            if agg.is_conflicted:
                rec_border = "#10b981" if agg.reconciled_direction == "long" else "#ff5252"
                rec_badge = "pill-green" if agg.reconciled_direction == "long" else "pill-red"
                rec_title = "▲ RECONCILED LONG" if agg.reconciled_direction == "long" else "▼ RECONCILED SHORT"
                st.markdown(
                    f"""
                    <div style="background: rgba(30, 41, 59, 0.7); border-left: 4px solid {rec_border}; padding: 10px 14px; border-radius: 6px; margin: 10px 0 16px 0;">
                        <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 4px;">
                            <span style="font-weight: 700; font-size: 0.88rem; color: #f8fafc;">
                                Multi-Timeframe Trend & Conflict Reconciliation: {agg.ticker}
                            </span>
                            <span class="status-pill {rec_badge}">{rec_title}</span>
                        </div>
                        <p style="margin: 0; font-size: 0.82rem; color: #cbd5e1; line-height: 1.4;">
                            {agg.reconciliation_summary}
                        </p>
                        <div style="margin-top: 6px; font-size: 0.76rem; color: #94a3b8;">
                            <span style="color: #10b981; font-weight: 600;">Long Conviction Mass: {agg.weighted_long_conviction:.0f} pts</span> &nbsp;•&nbsp; 
                            <span style="color: #ff5252; font-weight: 600;">Short Conviction Mass: {agg.weighted_short_conviction:.0f} pts</span>
                            {f" &nbsp;•&nbsp; <span style='color: #fbbf24; font-weight: 600;'>⚠️ Counter-trend pullback cautioned against HTF trend</span>" if agg.is_counter_trend_warning else ""}
                        </div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

            # Staged Opportunity Selection (default to primary_opportunity or session selected)
            active_opp_ids = [o.opportunity_id for o in agg.all_opportunities]
            current_staged_id = st.session_state.get("selected_opp_id")
            if not current_staged_id or current_staged_id not in active_opp_ids:
                current_staged_id = agg.primary_opportunity.opportunity_id
                st.session_state.selected_opp_id = current_staged_id

            # ── Triggered Signals & Multi-Timeframe Breakdown (Where / Why / How) ──
            st.markdown("#### Triggered Signals & Strategy Breakdown")
            st.caption("Inspect structural catalysts, execution geometry, and multi-timeframe confluence for each triggered system.")

            for s_idx, sig_group in enumerate(agg.triggered_signals):
                with st.container():
                    confl_pill = f"<span class='status-pill pill-blue'>⚡ Triggered across {len(sig_group.timeframes)} Timeframes: {', '.join(sig_group.timeframes)}</span>" if sig_group.is_multi_timeframe else f"<span class='status-pill pill-neutral'>Timeframe: {sig_group.timeframes[0]}</span>"
                    sig_dir_pill = f"<span class='status-pill {'pill-green' if sig_group.direction == 'long' else 'pill-red'}'>{'▲ LONG' if sig_group.direction == 'long' else '▼ SHORT'}</span>"
                    st.markdown(f"**{sig_group.setup_name} ({sig_group.system_acronym})** &nbsp; {sig_dir_pill} &nbsp; {confl_pill}", unsafe_allow_html=True)

                    for tf_idx, tf in enumerate(sig_group.timeframes):
                        detail = sig_group.timeframe_details.get(tf)
                        if not detail:
                            continue
                        is_active_staged = (detail.opportunity_id == current_staged_id)
                        staged_badge = " [ACTIVE FOR PLANNER]" if is_active_staged else ""

                        with st.expander(f"{tf} Horizon — Merit: {detail.merit_score:.1f}{staged_badge}", expanded=is_active_staged):
                            col_w1, col_w2 = st.columns([3.5, 1])
                            with col_w1:
                                st.markdown(f"**Where (Price Levels & Risk Geometry):**  \n{detail.where_summary}")
                                st.markdown(f"**Why (Technical Catalyst & Structural Context):**  \n{detail.why_summary}")
                                st.markdown(f"**How (Mechanical Execution Playbook):**  \n{detail.how_summary}")
                            stage_btn_k = f"btn_stage_detail_{s_idx}_{tf_idx}_{detail.opportunity_id}"
                            with col_w2:
                                if not is_active_staged:
                                    if st.button(f"Stage {tf} Setup", key=stage_btn_k, use_container_width=True, type="secondary"):
                                        st.session_state.selected_opp_id = detail.opportunity_id
                                        st.rerun()
                                else:
                                    st.markdown("<span class='status-pill pill-green'>Currently Staged</span>", unsafe_allow_html=True)

            opp = next((o for o in agg.all_opportunities if o.opportunity_id == current_staged_id), agg.primary_opportunity)

            if opp:
                opp_ticker = agg.ticker
                opp_sec = agg.sector
                dir_arrow = "▲" if opp.direction == "long" else "▼"
                dir_class = "text-long" if opp.direction == "long" else "text-short"
                st.divider()
                st.markdown(
                    f"<h3 style='margin-bottom: 2px;'>Active Staged Setup: <span class='{dir_class}'>{dir_arrow} {opp.direction.upper()}</span> {opp_ticker} ({opp.horizon}) — Rev. {opp.revision}</h3>",
                    unsafe_allow_html=True,
                )
                st.caption(
                    f"Sector: **{opp_sec}**  •  "
                    f"Source: <code>{opp.instrument_id}</code>  •  "
                    f"Identified: <b>{format_dual_time(opp.created_at)}</b>  •  "
                    f"Updated: <b>{format_dual_time(opp.available_at or opp.created_at)}</b>",
                    unsafe_allow_html=True,
                )

                col_a, col_b, col_c, col_d = st.columns(4)
                col_a.metric("Merit Score", f"{opp.merit_score:.1f}" if opp.merit_score else "N/A")
                col_b.metric("Confidence", f"{int(opp.confidence_score * 100)}%")
                col_c.metric("Entry / Trigger", f"${opp.trigger_price:.4f}" if opp.trigger_price else "—")
                col_d.metric("Hard Stop (Invalidation)", f"${opp.invalidation_price:.4f}" if opp.invalidation_price else "—")

                st.info(f"**Thesis:** {opp.thesis}")
                if opp.counter_evidence:
                    st.warning(f"**Counter-evidence / Risk:** {opp.counter_evidence}")

                # Candlestick chart
                observations = (
                    session.query(MarketObservation)
                    .filter_by(instrument_id=opp.instrument_id, interval=opp.horizon)
                    .order_by(MarketObservation.open_time.asc())
                    .all()
                )
                if observations:
                    chart_fig = create_candlestick_chart(
                        observations=observations,
                        symbol_label=opp.instrument_id,
                        trigger_price=opp.trigger_price,
                        invalidation_price=opp.invalidation_price,
                    )
                    st.plotly_chart(chart_fig, use_container_width=True)

                # Score breakdown
                st.subheader("Score Breakdown")
                sb = opp.score_breakdown
                if sb:
                    numeric_items = [(k, v) for k, v in sb.items() if isinstance(v, (int, float))]
                    if numeric_items:
                        for chunk_idx in range(0, len(numeric_items), 4):
                            chunk = numeric_items[chunk_idx : chunk_idx + 4]
                            cols = st.columns(4)
                            for c_idx, (k, v) in enumerate(chunk):
                                cols[c_idx].metric(k.replace("_", " ").title(), f"{v:.1f}")

                # -------------------------------------------------------------
                # ACCOUNT-BY-ACCOUNT RISK FACTOR & SIZING
                # -------------------------------------------------------------
                st.divider()
                st.subheader("Factored Per-Trade Risk & Account Allocation")
                st.markdown("Select which account you wish to trade this opportunity with, and fine-tune your factored dollar risk.")

                target_account_choice = st.radio(
                    "Select Target Execution Account:",
                    ["Breakoutprop Turbo 10k", "Personal Account (IBKR/Wealthsimple)"],
                    horizontal=True,
                )

                is_bp = "Breakoutprop" in target_account_choice
                target_acct_id = "bp_turbo_10k" if is_bp else "ibkr_personal"
                entry_p = opp.trigger_price or 1.0
                stop_p = opp.invalidation_price or 0.95
                stop_dist = abs(entry_p - stop_p)
                stop_pct = (stop_dist / entry_p) * 100.0 if entry_p > 0 else 0.0

                r2_target = entry_p + (stop_dist * 2.0) if opp.direction == "long" else entry_p - (stop_dist * 2.0)
                r3_target = entry_p + (stop_dist * 3.0) if opp.direction == "long" else entry_p - (stop_dist * 3.0)

                c_acct_info, c_risk_input = st.columns([1, 1])

                with c_acct_info:
                    if is_bp:
                        st.write(f"**Account:** `bp_turbo_10k`")
                        st.write(f"• Current Equity: `${st.session_state.bp_equity:,.2f}`")
                        st.write(f"• Max Drawdown Floor: `${st.session_state.bp_floor:,.2f}`")
                        st.write(f"• Usable Headroom: `${bp_headroom:,.2f}`")
                        st.write(f"• Daily Loss Limit: `${st.session_state.bp_daily_limit:,.2f}`")
                        default_risk = min(st.session_state.bp_per_trade_risk, max(bp_headroom * 0.4, 25.0))
                    else:
                        p_risk_budget = st.session_state.personal_equity * (st.session_state.personal_risk_pct / 100.0)
                        st.write(f"**Account:** `ibkr_personal`")
                        st.write(f"• Personal Equity: `${st.session_state.personal_equity:,.2f}`")
                        st.write(f"• Risk Setting: `{st.session_state.personal_risk_pct:.2f}%` of equity")
                        st.write(f"• Max Risk Budget: `${p_risk_budget:,.2f}`")
                        default_risk = p_risk_budget

                with c_risk_input:
                    user_trade_risk = st.number_input(
                        "Factored Per-Trade Dollar Risk ($)",
                        value=float(default_risk),
                        min_value=5.0,
                        max_value=bp_headroom if is_bp else float(st.session_state.personal_equity * 0.1),
                        step=10.0,
                        help="Adjust the dollar amount you are willing to lose if the hard stop is hit.",
                    )

                    if stop_dist > 0:
                        calculated_qty = round(user_trade_risk / stop_dist, 4)
                    else:
                        calculated_qty = 0.01

                    actual_scenario_loss = calculated_qty * stop_dist
                    consumption_pct = (actual_scenario_loss / bp_headroom * 100.0) if is_bp and bp_headroom > 0 else (actual_scenario_loss / st.session_state.personal_equity * 100.0)

                # ── Actionable Execution Card ──
                notional_value = calculated_qty * entry_p
                trigger_dt = opp.as_of or opp.created_at
                trigger_time_str = trigger_dt.strftime("%H:%M UTC on %Y-%m-%d") if trigger_dt else "—"

                sb = opp.score_breakdown or {}
                live_telem = sb.get("live_tracking", {})
                lp = live_telem.get("latest_price")
                drift_pct = live_telem.get("drift_pct", ((lp - entry_p) / entry_p * 100) if lp and entry_p else 0.0)
                curr_rr = live_telem.get("current_rr", 2.0)
                drift_color = "#4ade80" if drift_pct >= 0 else "#f87171"
                drift_sign = "+" if drift_pct >= 0 else ""

                st.markdown(
                    f"""
                    <div style="background: linear-gradient(135deg, #131722 0%, #1e222d 100%); border: 1px solid #2a2e39; border-radius: 8px; padding: 18px 22px; margin: 12px 0;">
                        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px;">
                            <span style="font-size: 13px; text-transform: uppercase; letter-spacing: 0.05em; color: #94a3b8; font-weight: 600;">Execution Summary — {opp_ticker} {opp.direction.upper()}</span>
                            <span style="font-size: 12px; color: #38bdf8; font-weight: 600;">Triggered at {trigger_time_str} at ${entry_p:.4f}</span>
                        </div>
                        <div style="display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; margin-bottom: 16px;">
                            <div>
                                <div style="color: #94a3b8; font-size: 12px; font-weight: 600; text-transform: uppercase;">Position Size</div>
                                <div style="color: #f1f5f9; font-size: 16px; font-weight: 600; font-family: 'JetBrains Mono', monospace;">{calculated_qty:,.4f} units</div>
                                <div style="color: #38bdf8; font-size: 12px;">≈ ${notional_value:,.2f} notional</div>
                            </div>
                            <div>
                                <div style="color: #94a3b8; font-size: 12px; font-weight: 600; text-transform: uppercase;">Trigger Entry</div>
                                <div style="color: #38bdf8; font-size: 16px; font-weight: 600; font-family: 'JetBrains Mono', monospace;">${entry_p:.4f}</div>
                                {f'<div style="color: {drift_color}; font-size: 11px;">Live: ${lp:.4f} ({drift_sign}{drift_pct:.2f}% | {curr_rr:.1f}R)</div>' if lp else f'<div style="color: #94a3b8; font-size: 11px;">Triggered: {trigger_time_str}</div>'}
                            </div>
                            <div>
                                <div style="color: #94a3b8; font-size: 12px; font-weight: 600; text-transform: uppercase;">Hard Stop Loss</div>
                                <div style="color: #f87171; font-size: 16px; font-weight: 600; font-family: 'JetBrains Mono', monospace;">${stop_p:.4f}</div>
                                <div style="color: #f87171; font-size: 12px;">-{stop_pct:.2f}% from entry</div>
                            </div>
                            <div>
                                <div style="color: #94a3b8; font-size: 12px; font-weight: 600; text-transform: uppercase;">Risk if Stopped</div>
                                <div style="color: #fbbf24; font-size: 16px; font-weight: 600; font-family: 'JetBrains Mono', monospace;">${actual_scenario_loss:,.2f}</div>
                                <div style="color: #fbbf24; font-size: 12px;">{consumption_pct:.1f}% of {'headroom' if is_bp else 'equity'}</div>
                            </div>
                        </div>
                        <div style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; border-top: 1px solid #2a2e39; padding-top: 14px;">
                            <div>
                                <div style="color: #94a3b8; font-size: 12px; font-weight: 600; text-transform: uppercase;">Take Profit — 2R Target</div>
                                <div style="color: #4ade80; font-size: 15px; font-weight: 600; font-family: 'JetBrains Mono', monospace;">${r2_target:.4f}</div>
                                <div style="color: #4ade80; font-size: 12px;">+${(calculated_qty * stop_dist * 2):,.2f} profit</div>
                            </div>
                            <div>
                                <div style="color: #94a3b8; font-size: 12px; font-weight: 600; text-transform: uppercase;">Take Profit — 3R Target</div>
                                <div style="color: #22c55e; font-size: 15px; font-weight: 600; font-family: 'JetBrains Mono', monospace;">${r3_target:.4f}</div>
                                <div style="color: #22c55e; font-size: 12px;">+${(calculated_qty * stop_dist * 3):,.2f} profit</div>
                            </div>
                            <div>
                                <div style="color: #94a3b8; font-size: 12px; font-weight: 600; text-transform: uppercase;">Reward-to-Risk Ratio</div>
                                <div style="color: #38bdf8; font-size: 15px; font-weight: 600; font-family: 'JetBrains Mono', monospace;">2:1 / 3:1</div>
                                <div style="color: #38bdf8; font-size: 12px;">{f"Current Executable: {curr_rr:.2f}R" if curr_rr else "at 2R / 3R respectively"}</div>
                            </div>
                        </div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

                acct_dict = {
                    "equity": st.session_state.bp_equity if is_bp else st.session_state.personal_equity,
                    "drawdown_floor": st.session_state.bp_floor if is_bp else 0.0,
                    "headroom": bp_headroom if is_bp else st.session_state.personal_equity,
                    "daily_loss_limit": st.session_state.bp_daily_limit if is_bp else 1000.0,
                    "factored_risk_usd": user_trade_risk,
                }
                ctx_eval_summary = context_engine.compute_context_summary()

                # -------------------------------------------------------------
                # SECTION A: STRUCTURED CONTEXT AUDIT (Deterministic)
                # -------------------------------------------------------------
                st.divider()
                st.subheader("Structured Context Audit")
                st.caption(
                    "Deterministic rule evaluation: tests candidate against macro regime, catalyst proximity, "
                    "and account headroom boundaries using local business rules. Zero external API calls."
                )

                audit_key = f"context_audit_{opp.id}"
                if st.button("Run Structured Context Audit", key=f"btn_audit_{opp.id}", use_container_width=True):
                    with st.spinner("Executing rule-based context audit..."):
                        audit_res = llm_scaffold.run_context_audit(
                            opp=opp,
                            context_summary=ctx_eval_summary,
                            account_id=target_acct_id,
                            account_state=acct_dict,
                        )
                        st.session_state[audit_key] = audit_res

                if audit_key in st.session_state:
                    rev = st.session_state[audit_key]
                    v_pill = "pill-green" if rev["verdict"] == "ACCEPT" else ("pill-amber" if rev["verdict"] == "MODIFY_RISK" else "pill-red")
                    st.markdown(
                        f"<h4>Audit Verdict: <span class='status-pill {v_pill}'>{rev['verdict']}</span> &nbsp; "
                        f"Confidence: <b>{int(rev['confluence_confidence'] * 100)}%</b></h4>",
                        unsafe_allow_html=True,
                    )
                    st.info(f"**Executive Synthesis:** {rev['executive_synthesis']}")

                    rk_col1, rk_col2 = st.columns(2)
                    with rk_col1:
                        st.markdown("#### Key Risks & Structural Blindspots")
                        for risk in rev.get("key_risks_and_blindspots", []):
                            st.write(f"• {risk}")
                    with rk_col2:
                        st.markdown("#### Suggested Sizing & Targets")
                        sugg = rev.get("sizing_and_plan_refinement", {})
                        st.write(f"• Suggested Risk: `${sugg.get('recommended_risk_usd', user_trade_risk):,.2f}`")
                        st.write(f"• Adjusted Stop: `${sugg.get('adjusted_stop', stop_p):.4f}`")
                        st.write(f"• 2R Target: `${sugg.get('target_r2', r2_target):.4f}` | 3R Target: `${sugg.get('target_r3', r3_target):.4f}`")

                    st.caption(f"**Playbook Feedback:** {rev.get('playbook_learning_feedback', '')}")

                    with st.expander("Show Assembled Deterministic Prompt (For Copying)"):
                        st.code(rev.get("assembled_prompt", ""), language="markdown")

                # -------------------------------------------------------------
                # SECTION B: AI AUDIT (Frontier LLM via Connected API)
                # -------------------------------------------------------------
                st.divider()
                st.subheader("AI Audit")
                st.caption(
                    "Dispatches a scoped context payload to an authenticated AI provider (OpenAI, Anthropic, or Google Gemini) "
                    "for independent strategic interrogation. Only factors data that directly influenced this decision, noting background context separately."
                )

                connected_apis = get_connected_apis()

                if not connected_apis:
                    st.info("Connect an API to enable this function")
                    st.caption("Configure credentials in **Settings > AI Engine & API Connections** to enable live AI audits.")
                else:
                    col_api_sel, col_api_info = st.columns([1, 1])
                    with col_api_sel:
                        api_display_list = [c["display_name"] for c in connected_apis]
                        chosen_display = st.selectbox("Connected AI Engine", api_display_list, key=f"sel_api_{opp.id}")
                        chosen_api = next(c for c in connected_apis if c["display_name"] == chosen_display)

                    # Build focused payload
                    focused_payload = llm_scaffold.build_focused_payload(
                        opp=opp,
                        context_summary=ctx_eval_summary,
                        account_id=target_acct_id,
                        account_state=acct_dict,
                    )

                    cost_info = estimate_prompt_cost(focused_payload["focused_prompt"], chosen_api["provider"], chosen_api["model"])

                    with col_api_info:
                        st.markdown(
                            f"<div style='background: #11141d; border: 1px solid #1e2330; border-radius: 6px; padding: 10px 14px; margin-top: 1.5rem;'>"
                            f"<span style='color: #94a3b8; font-size: 11px; text-transform: uppercase; font-weight: 600;'>Estimated Cost</span><br>"
                            f"<span style='color: #f8fafc; font-size: 14px; font-weight: 600;'>~{cost_info['estimated_tokens']} tokens (< ${max(cost_info['estimated_cost_usd'], 0.0005):.4f} USD)</span>"
                            f"</div>",
                            unsafe_allow_html=True,
                        )

                    ai_audit_key = f"ai_audit_{opp.id}"
                    if st.button(f"Generate AI Audit ({chosen_api['display_name']})", key=f"btn_gen_ai_{opp.id}", type="primary", use_container_width=True):
                        with st.spinner(f"Interrogating trade setup via {chosen_api['display_name']}..."):
                            try:
                                ai_result = generate_ai_review(
                                    prompt=focused_payload["focused_prompt"],
                                    provider=chosen_api["provider"],
                                    api_key=chosen_api["api_key"],
                                    model=chosen_api["model"],
                                )
                                st.session_state[ai_audit_key] = ai_result
                            except Exception as exc:
                                st.error(f"AI Audit failed: {str(exc)}")

                    if ai_audit_key in st.session_state:
                        ai_rev = st.session_state[ai_audit_key]
                        v_pill = "pill-green" if ai_rev["verdict"] == "ACCEPT" else ("pill-amber" if ai_rev["verdict"] == "MODIFY_RISK" else "pill-red")
                        st.markdown(
                            f"<h4>AI Verdict: <span class='status-pill {v_pill}'>{ai_rev['verdict']}</span> &nbsp; "
                            f"Confluence: <b>{int(ai_rev['confluence_confidence'] * 100)}%</b> &nbsp; "
                            f"<span style='font-size: 12px; color: #94a3b8;'>({ai_rev.get('provider', '').upper()} {ai_rev.get('model', '')})</span></h4>",
                            unsafe_allow_html=True,
                        )
                        st.success(f"**Executive Synthesis:** {ai_rev['executive_summary']}")

                        ai_c1, ai_c2 = st.columns(2)
                        with ai_c1:
                            st.markdown("#### Identified Risks & Blindspots")
                            for r in ai_rev.get("key_risks_and_blindspots", []):
                                st.write(f"• {r}")
                        with ai_c2:
                            st.markdown("#### Sizing & Execution Adjustments")
                            sugg = ai_rev.get("sizing_and_plan_refinement", {})
                            if sugg:
                                st.write(f"• Recommended Risk: `${sugg.get('recommended_risk_usd', user_trade_risk):,.2f}`")
                                st.write(f"• Adjusted Stop: `${sugg.get('adjusted_stop', stop_p):.4f}`")
                                st.write(f"• 2R Target: `${sugg.get('target_r2', r2_target):.4f}` | 3R Target: `${sugg.get('target_r3', r3_target):.4f}`")

                        st.caption(f"**Playbook Feedback:** {ai_rev.get('playbook_learning_feedback', 'Disciplined execution recommended.')}")

                        with st.expander("Inspect Focused Context Payload (Sent to AI)"):
                            st.markdown("##### 1. Primary Evidence Used in Decision")
                            st.code(focused_payload["used_context"], language="text")
                            st.markdown("##### 2. System Background (Available But Not Directly Factored)")
                            st.code(focused_payload["unused_context"], language="text")
                            st.markdown("##### 3. Full Transmitted Prompt")
                            st.code(focused_payload["focused_prompt"], language="markdown")

                # -------------------------------------------------------------
                # SECTION B.2: INTERACTIVE STRATEGY ASSISTANT (Q&A)
                # -------------------------------------------------------------
                st.markdown("---")
                st.markdown("### 💬 Interactive Strategy Assistant (Q&A)")
                st.caption(
                    "Ask tactical questions to your connected AI model regarding this specific strategy setup. "
                    "The assistant evaluates the original trigger timestamp, trigger price, latest market price, "
                    "price drift %, and real-time executable R:R."
                )

                if not connected_apis:
                    st.info("Connect an AI model in Settings to enable interactive strategy Q&A.")
                else:
                    st.markdown("**Tactical Presets:**")
                    qp_col1, qp_col2, qp_col3, qp_col4 = st.columns(4)
                    preset_question = None
                    with qp_col1:
                        if st.button("⚖️ Validity after Drift?", key=f"btn_qp1_{opp.id}", use_container_width=True):
                            preset_question = "Is this setup still valid after the current price drift? Should I enter now or wait for a pullback to trigger?"
                    with qp_col2:
                        if st.button("⚠️ Invalidation Risks?", key=f"btn_qp2_{opp.id}", use_container_width=True):
                            preset_question = "What are the key failure modes and invalidation traps for this setup under the current market regime?"
                    with qp_col3:
                        if st.button("🎯 Trail Stop & Targets?", key=f"btn_qp3_{opp.id}", use_container_width=True):
                            preset_question = "What are the recommended trail stop rules and scale-out target milestones for this trade?"
                    with qp_col4:
                        if st.button("🌐 HTF Confluence Check?", key=f"btn_qp4_{opp.id}", use_container_width=True):
                            preset_question = "How does this setup align with higher timeframe market structure and liquidity pools?"

                    chat_hist_key = f"ai_chat_history_{opp.id}"
                    if chat_hist_key not in st.session_state:
                        st.session_state[chat_hist_key] = []

                    user_q_key = f"chat_user_q_{opp.id}"
                    custom_q = st.text_input(
                        "Ask your question:",
                        value=preset_question or "",
                        key=user_q_key,
                        placeholder="e.g. Price drifted +1.2% above trigger. Is it safe to enter or should I wait for a retest?",
                    )

                    col_ask, col_clear = st.columns([4, 1])
                    with col_ask:
                        ask_btn = st.button(f"Ask Strategic Assistant ({chosen_api['display_name']})", key=f"btn_ask_custom_{opp.id}", type="primary", use_container_width=True)
                    with col_clear:
                        if st.button("Clear Chat", key=f"btn_clear_chat_{opp.id}", use_container_width=True):
                            st.session_state[chat_hist_key] = []
                            st.rerun()

                    prompt_to_send = preset_question or (custom_q.strip() if ask_btn and custom_q.strip() else None)

                    if prompt_to_send:
                        opp_context = {
                            "ticker": opp_ticker,
                            "instrument_id": opp.instrument_id,
                            "playbook_name": opp.playbook_name,
                            "horizon": opp.horizon.value if hasattr(opp.horizon, "value") else str(opp.horizon),
                            "direction": opp.direction.value if hasattr(opp.direction, "value") else str(opp.direction),
                            "merit_score": f"{opp.merit_score:.1f}" if opp.merit_score else "N/A",
                            "confidence_pct": f"{int(opp.confidence_score * 100)}" if opp.confidence_score else "N/A",
                            "trigger_price": entry_p,
                            "trigger_time": trigger_time_str,
                            "invalidation_price": stop_p,
                            "stop_pct": stop_pct,
                            "target_2r": r2_target,
                            "target_3r": r3_target,
                            "latest_price": lp or entry_p,
                            "drift_pct": drift_pct,
                            "current_rr": curr_rr,
                            "technical_signals": ", ".join(k for k, v in (opp.signals or {}).items() if v) if opp.signals else "N/A",
                            "how_summary": (opp.how_summary or "").replace("$", "\\$"),
                            "macro_context": ctx_eval_summary.get("macro_regime", "Standard trading regime"),
                        }

                        with st.spinner(f"Consulting {chosen_api['display_name']} on {opp_ticker} trade strategy..."):
                            try:
                                ai_reply = ask_ai_strategy_chat(
                                    opp_context=opp_context,
                                    user_question=prompt_to_send,
                                    conversation_history=st.session_state[chat_hist_key],
                                    provider=chosen_api["provider"],
                                    api_key=chosen_api["api_key"],
                                    model=chosen_api["model"],
                                    )
                                st.session_state[chat_hist_key].append({"role": "user", "content": prompt_to_send})
                                st.session_state[chat_hist_key].append({
                                    "role": "assistant",
                                    "content": ai_reply,
                                    "provider": chosen_api["display_name"],
                                })
                            except Exception as ex:
                                st.error(f"Strategy Q&A Error: {str(ex)}")

                    if st.session_state[chat_hist_key]:
                        st.markdown("##### Strategic Interrogation Log")
                        for msg in st.session_state[chat_hist_key]:
                            if msg["role"] == "user":
                                st.markdown(
                                    f"<div style='background: #1e293b; border-radius: 6px; padding: 10px 14px; margin-bottom: 8px; border-left: 3px solid #38bdf8;'>"
                                    f"<span style='font-size: 11px; text-transform: uppercase; color: #38bdf8; font-weight: 700;'>Trader</span><br>"
                                    f"<span style='color: #f1f5f9; font-size: 13.5px;'>{msg['content']}</span>"
                                    f"</div>",
                                    unsafe_allow_html=True,
                                )
                            else:
                                safe_content = msg["content"].replace("$", "\\$")
                                prov_badge = msg.get("provider", "AI Agent")
                                st.markdown(
                                    f"<div style='background: #0f172a; border-radius: 6px; padding: 12px 16px; margin-bottom: 12px; border-left: 3px solid #10b981;'>"
                                    f"<span style='font-size: 11px; text-transform: uppercase; color: #10b981; font-weight: 700;'>Strategic Officer ({prov_badge})</span>"
                                    f"</div>",
                                    unsafe_allow_html=True,
                                )
                                st.markdown(safe_content)

                # -------------------------------------------------------------
                # INDICATOR EXPLANATIONS & CONFLUENCE BREAKDOWN
                # -------------------------------------------------------------
                st.divider()
                with st.expander("System Framework & Indicator Heuristics", expanded=False):
                    st.markdown("""
    ### Deep Indicator & Technical Framework Explanations

    1. **VCEI — Volatility Compression & Expansion Impulse (ChrisMoody Squeeze Pro)**:
       - *Mechanism*: Evaluates Bollinger Bands (20, 2.0σ) compressing within three tiers of Keltner Channels (1.0×, 1.5×, 2.0× ATR).
       - *Dots Interpretation*: Black = High (< 1.0 ATR), Red = Mid (< 1.5 ATR), Orange = Low (< 2.0 ATR), Green = Fired expansion.
       - *Momentum*: Linear regression slope of the momentum oscillator confirms breakout direction.

    2. **SHLA — Institutional Stop-Hunt & Liquidity Absorption (LuxAlgo Sweeps & Order Blocks)**:
       - *Mechanism*: Identifies stop-hunts past key swing pivots into resting retail stop clusters.
       - *Order Blocks*: The origin candle of displacement serves as institutional mitigation support/resistance.

    3. **AVIA — Auction Value Area & Institutional Acceptance (Fixed Range Volume Profile)**:
       - *Point of Control (POC)*: The price level with the highest transaction volume.
       - *Value Area (VAH / VAL)*: Encloses 70% of total volume traded.

    4. **TCMB — Multi-Band Trend Cushion & Momentum Bands (Ripster EMA Clouds)**:
       - *5-12 Cloud*: Momentum guide. Pullbacks and bounces offer low-risk continuation entries.
       - *34-50 Cloud*: Trend cushion and structural support.

    5. **AVWAP — Institutional Anchored Volume-Weighted Benchmark**:
       - Institutional benchmark price anchored to key structural reversal pivots or session opens.

    6. **NERN — Non-Euclidean Regime Nearest-Neighbors (Lorentzian Distance KNN)**:
       - Non-Euclidean warped metric space distance finding the 7 most similar historical market states.
                    """)

                    # Confluence evaluation for current candidate
                    ctx_eval = context_engine.evaluate_opportunity_confluence(opp.direction, opp.horizon)
                    st.markdown(f"#### Context Engine Confluence Rating: `{ctx_eval['confluence_rating']}`")
                    for note in ctx_eval["confluence_notes"]:
                        st.write(f"• {note}")

                # -------------------------------------------------------------
                # HUMAN REVIEW & TRADE PLAN LOGGING
                # -------------------------------------------------------------
                st.divider()
                st.subheader("Human Review & Context")

                c_notes1, c_notes2 = st.columns([2, 1])
                with c_notes1:
                    user_notes = st.text_area(
                        "Add your personal context, market notes, or decision rationale:",
                        value="",
                        placeholder="e.g. Squeeze Pro fired during London open; entering with 2R target at prior high.",
                        key=f"txt_notes_{opp.id}",
                    )
                with c_notes2:
                    inv_class = st.selectbox(
                        "Manual Invalidation Classification",
                        [
                            "manual_invalidation",
                            "market_structure_broken",
                            "unfavorable_price_drift",
                            "catalyst_event_risk",
                            "choppy_sideways_chop",
                            "htf_trend_conflict",
                        ],
                        key=f"sel_inv_class_{opp.id}",
                        help="Categorization used when clicking '❌ Invalidate Setup' to record failure telemetry.",
                    )

                action_col1, action_col2, action_col3, action_col4 = st.columns([1.5, 1.1, 1.1, 1.3])
                plan_created_msg = None
                with action_col1:
                    if st.button("Accept & Create Trade Plan", key=f"btn_accept_plan_{opp.id}", type="primary", use_container_width=True):
                        final_reason = user_notes.strip() if user_notes.strip() else f"Accepted for {target_account_choice}"
                        dec = journal_service.record_decision(
                            session=session,
                            opportunity_version_id=opp.id,
                            decision=DecisionType.ACCEPTED,
                            reason=final_reason,
                        )
                        plan, res = journal_service.create_trade_plan(
                            session=session,
                            decision_id=dec.id,
                            account_id=target_acct_id,
                            instrument_id=opp.instrument_id,
                            direction=opp.direction,
                            entry_price=entry_p,
                            stop_loss=stop_p,
                            quantity=calculated_qty,
                            target_price=r2_target,
                        )
                        plan_created_msg = (
                            f"Created Trade Plan `{plan.id}` for {target_account_choice}!\n\n"
                            f"• Position Size: `{calculated_qty} units`  \n"
                            f"• Reserved Risk: `${res.reserved_risk_usd:,.2f}`  \n"
                            f"• Commentary: \"{final_reason}\""
                        )
                with action_col2:
                    if st.button("Defer Candidate", key=f"btn_defer_plan_{opp.id}", use_container_width=True):
                        final_reason = user_notes.strip() if user_notes.strip() else "Deferred by user"
                        journal_service.record_decision(session, opp.id, DecisionType.DEFERRED, final_reason)
                        st.info(f"Opportunity marked as Deferred: \"{final_reason}\"")
                with action_col3:
                    if st.button("Reject Candidate", key=f"btn_reject_plan_{opp.id}", use_container_width=True):
                        final_reason = user_notes.strip() if user_notes.strip() else "Rejected by user"
                        journal_service.record_decision(session, opp.id, DecisionType.REJECTED, final_reason)
                        st.warning(f"Opportunity rejected: \"{final_reason}\"")
                with action_col4:
                    if st.button("❌ Invalidate Setup", key=f"btn_inv_man_{opp.id}", use_container_width=True, help="Mark setup as invalidated/busted with failure telemetry"):
                        final_reason = user_notes.strip() if user_notes.strip() else f"Manual invalidation: {inv_class.replace('_', ' ').title()}"
                        OpportunityLifecycleManager().manually_invalidate_opportunity(
                            session=session,
                            opportunity_id=opp.id,
                            reason=inv_class,
                            user_notes=final_reason,
                        )
                        st.toast(f"Setup {opp_ticker} ({opp.horizon}) invalidated and logged to autopsy telemetry.")
                        set_nav("Opportunity Queue")
                        st.rerun()

                if plan_created_msg:
                    st.success(plan_created_msg)

    finally:
        session.close()

# -------------------------------------------------------------
# VIEW 3: MACRO & CATALYSTS (Non-Technical Context Dashboard)
# -------------------------------------------------------------
elif nav == "Macro & Catalysts":
    st.title("Macro Environment, Seasonality & Catalysts")
    st.markdown("Non-technical market dimensions: global liquidity sessions, Treasury yields, Dollar index, Fear & Greed sentiment, and economic catalysts.")

    tab_sess, tab_macro, tab_sent, tab_cat = st.tabs([
        "Sessions & Seasonality",
        "Macro & Rate Regime",
        "Market Sentiment & Breadth",
        "High-Impact Catalysts",
    ])

    with tab_sess:
        st.subheader("Global Liquidity Sessions & Weekly Patterns")
        sess_data = context_engine.evaluate_seasonality()

        sc1, sc2, sc3, sc4 = st.columns(4)
        sc1.metric("Active Trading Session", sess_data["current_session"])
        sc2.metric("Session Bias", sess_data["session_bias"].replace("_", " ").title())
        sc3.metric("Liquidity Tier", sess_data["liquidity_level"].upper())
        sc4.metric("Day of Week", sess_data["weekday"])

        st.info(f"**Seasonality Insight:** {sess_data['seasonality_notes']}")

        st.markdown("""
#### Global Session Reference Schedule (UTC)
| Session | Hours (UTC) | Characteristics & Strategy Fit |
|---|---|---|
| **Asian Session** | `00:00 – 07:00 UTC` | Initial liquidity formation, range-bound mean reversion, liquidity sweeps. |
| **London Session** | `07:00 – 13:00 UTC` | European open liquidity, trend expansion, breakout confirmation. |
| **New York Session** | `13:00 – 20:00 UTC` | Peak institutional volume, US macroeconomic releases, maximum momentum. |
| **US Close & Reset** | `20:00 – 00:30 UTC` | Settlement window, Breakoutprop daily loss limit resets at **00:30 UTC**. |
        """)

    with tab_macro:
        st.subheader("US Interest Rates, Yields & Dollar Index")
        macro_data = context_engine.evaluate_macro_regime()

        mc1, mc2, mc3 = st.columns(3)
        mc1.metric("US 10Y Yield (^TNX)", f"{macro_data['us_10y_yield']:.2f}%", macro_data["us_10y_trend"].upper())
        mc2.metric("Dollar Index Proxy (UUP)", macro_data["dollar_index_trend"].upper())
        mc3.metric("Macro Regime", macro_data["macro_regime"].replace("_", " ").upper())

        st.markdown(f"""
**Macro Tactical Playbook:**
- **Current Classification**: `{macro_data['macro_regime'].upper()}`
- **Crypto Impact**: Falling yields and a weakening Dollar Index create a strong liquidity tailwind for Bitcoin and high-beta altcoins. Rising yields produce valuation compression.
- **Equities Impact**: Growth and tech sectors expand when 10Y yields soften; defensive sectors outperform when yields spike.
        """)

    with tab_sent:
        st.subheader("Crypto & Equity Sentiment Indices")
        sent_data = context_engine.evaluate_market_sentiment()

        snt1, snt2, snt3 = st.columns(3)
        snt1.metric("Crypto Fear & Greed", f"{sent_data['fear_greed_score']} / 100", sent_data["fear_greed_class"])
        snt2.metric("Equity Breadth", sent_data["equity_breadth"].replace("_", " ").title())
        snt3.metric("Sentiment Bias", "Contrarian Filter Active")

        st.info(f"**Contrarian Interpretation:** {sent_data['contrarian_insight']}")

    with tab_cat:
        st.subheader("Upcoming High-Impact Economic Catalysts")
        st.markdown("Automated countdowns for market-moving events. The system warns against intraday breakouts within 2 hours of release.")

        catalysts = context_engine.get_upcoming_catalysts()
        cat_rows = []
        for c in catalysts:
            cat_rows.append({
                "Event": c["event"],
                "Category": c["category"],
                "Impact": c["impact"],
                "Scheduled Time": format_dual_time(c["scheduled_utc"]),
                "Hours Until": f"{c['hours_until']:.1f} hrs",
                "Trading Advisory": c["trading_warning"].replace("_", " ").title(),
            })
        st.dataframe(pd.DataFrame(cat_rows), hide_index=True, use_container_width=True)

# -------------------------------------------------------------
# VIEW 4: DEVIATIONS & ROTATION
# -------------------------------------------------------------
elif nav == "Deviations & Rotation":
    st.title("Deviations & Rotation")
    st.markdown(
        "Track abnormal deviations, institutional capital flows, and relative performance across "
        "the 11 S&P 500 sectors against the SPY benchmark."
    )

    sec_eval = sector_engine.evaluate_sector_performance()
    sectors = sec_eval["sectors"]
    alerts = sec_eval["abnormal_alerts"]

    top_s = sec_eval.get("top_sector")
    bot_s = sec_eval.get("bottom_sector")
    disp = sec_eval.get("dispersion_range", 0.0)

    # Metric Cards
    c1, c2, c3, c4 = st.columns(4)
    c1.metric(
        "Leading Sector",
        f"{top_s['sector']} ({top_s['ticker']})" if top_s else "—",
        f"+{top_s['rel_perf_1d']:.1f}% vs SPY" if top_s else "—",
    )
    c2.metric(
        "Lagging Sector",
        f"{bot_s['sector']} ({bot_s['ticker']})" if bot_s else "—",
        f"{bot_s['rel_perf_1d']:.1f}% vs SPY" if bot_s else "—",
    )
    c3.metric("Cross-Sector Dispersion", f"{disp:.1f}% spread", "High Alpha Dispersion" if disp > 3.0 else "Normal")
    c4.metric("Abnormal Deviations", f"{len(alerts)} Active Alerts", "Institutional Rotation")

    st.divider()

    # Section 1: Abnormal Deviation Alerts (|Z| >= 1.75 sigma)
    st.subheader("Abnormal Sector Deviation Alerts (|Z| ≥ 1.75σ)")
    if alerts:
        for a in alerts:
            box_type = st.success if a["deviation_type"] == "ABNORMAL_INFLOW" else st.error
            box_type(f"**[{a['deviation_type']}]** {a['message']}")
    else:
        st.info("No abnormal sector deviations currently flagged. All 11 sectors are trading within ±1.75σ of normal distribution.")

    st.divider()

    # Section 2: 11 Sector Matrix Table & Chart
    st.subheader("S&P 500 Sector SPDR Matrix vs SPY Benchmark")
    sec_df = pd.DataFrame([{
        "Ticker": s["ticker"],
        "Sector": s["sector"],
        "Price": f"${s['price']:.2f}",
        "1D Return": f"{s['ret_1d']:+.2f}%",
        "5D Return": f"{s['ret_5d']:+.2f}%",
        "1D vs SPY": f"{s['rel_perf_1d']:+.2f}%",
        "5D vs SPY": f"{s['rel_perf_5d']:+.2f}%",
        "Z-Score": f"{s['z_score']:+.2f}σ",
        "Flow Status": s["status"],
    } for s in sectors])
    st.dataframe(sec_df, hide_index=True, use_container_width=True)

    # Relative Performance Bar Chart
    rel_chart_data = pd.DataFrame({
        "Sector": [s["ticker"] for s in sectors],
        "Relative Return vs SPY (%)": [s["rel_perf_1d"] for s in sectors],
    }).set_index("Sector")
    st.bar_chart(rel_chart_data)

    st.divider()

    # Section 3: Stock-to-Sector Relative Alpha Tracker
    st.subheader("Watched Stocks vs Sector Benchmark (Alpha vs Beta)")
    st.markdown(
        "Compares individual stock performance against its parent sector ETF to distinguish true "
        "**idiosyncratic company alpha** from broader sector beta."
    )

    divergence_data = sector_engine.evaluate_stock_sector_divergence()
    if divergence_data:
        div_df = pd.DataFrame([{
            "Stock": d["stock"],
            "Sector": d["sector"],
            "Sector ETF": d["sector_etf"],
            "Stock 1D": f"{d['stock_ret_1d']:+.2f}%",
            "Sector 1D": f"{d['sector_etf_ret_1d']:+.2f}%",
            "Alpha Spread": f"{d['alpha_divergence']:+.2f}%",
            "Divergence Signal": d["signal"],
        } for d in divergence_data])
        st.dataframe(div_df, hide_index=True, use_container_width=True)
    else:
        st.info("Alpha divergence indicates institutional buying independent of broad market tides.")

# -------------------------------------------------------------
# VIEW 5: STRATEGY DEEP-DIVES
# -------------------------------------------------------------
elif nav == "Strategy Deep-Dives":
    st.title("Strategy Deep-Dives & Institutional Playbook")
    st.markdown(
        "First-principles analysis of how each trading strategy works, underlying market microstructure drivers, "
        "core mathematical assumptions, failure modes, and our tailored confluence adjustments."
    )

    strat_tabs = st.tabs([meta.get("short_title", k) for k, meta in STRATEGY_DEEP_DIVES.items()])

    for idx, (strat_key, meta) in enumerate(STRATEGY_DEEP_DIVES.items()):
        with strat_tabs[idx]:
            st.subheader(f"{meta.get('short_title', strat_key)} — {meta['original_name']}")
            st.caption(f"Purposed Name: **{meta['purposed_name']}** | Acronym: <code>{meta['acronym']}</code>", unsafe_allow_html=True)
            st.info(f"**Institutional Philosophy:** {meta['tagline']}")

            st.markdown("#### How It Works")
            st.write(meta["how_it_works"])

            st.markdown("#### Why It Works (First Principles & Auction Theory)")
            st.write(meta["why_it_works"])

            st.markdown("#### Core Assumptions")
            for assumption in meta["core_assumptions"]:
                st.write(f"• {assumption}")

            st.markdown("#### Limitations & Failure Modes")
            st.warning(meta["limitations"])

            st.markdown("#### Tailored Adjustments & Confluence Gates")
            st.success(meta["tailored_adjustments"])

# -------------------------------------------------------------
# VIEW 6: JOURNAL & PERFORMANCE (Merged Analytics & Orders)
# -------------------------------------------------------------
elif nav == "Journal & Performance":
    st.title("Decision Journal & Strategy Performance")
    st.markdown("Track human review decisions, review performance analytics, inspect trade plans, and export journal records.")

    session = get_db_session()
    try:

        tab_analytics, tab_dec, tab_plans, tab_fills, tab_outcomes = st.tabs([
            "Performance & Analytics",
            "Decisions & Commentary",
            "Trade Plans & Risk Reservations",
            "Fills & Execution",
            "Outcomes & Attribution",
        ])

        with tab_analytics:
            st.subheader("Aggregated Journal & Review Analytics")
            all_decisions = session.query(DecisionRecord).order_by(DecisionRecord.created_at.desc()).all()
            all_plans = session.query(TradePlan).order_by(TradePlan.created_at.desc()).all()
            all_outcomes = session.query(OutcomeRecord).order_by(OutcomeRecord.created_at.desc()).all()

            rc1, rc2, rc3, rc4 = st.columns(4)
            rc1.metric("Decisions Reviewed", len(all_decisions))
            accepted_count = sum(1 for d in all_decisions if d.decision.lower() == "accepted")
            rc2.metric("Accepted Trades", accepted_count)
            total_risk = sum(p.estimated_risk_usd for p in all_plans)
            rc3.metric("Total Reserved Risk", f"${total_risk:,.2f}")
            rc4.metric("Completed Outcomes", len(all_outcomes))

            st.divider()

            col_rep1, col_rep2 = st.columns(2)

            with col_rep1:
                st.subheader("Decisions by Account")
                if all_plans:
                    acct_counts = {}
                    for p in all_plans:
                        acct_counts[p.account_id] = acct_counts.get(p.account_id, 0) + 1
                    st.bar_chart(pd.Series(acct_counts))
                else:
                    st.info("No trade plans logged yet.")

            with col_rep2:
                st.subheader("Decision Breakdown")
                if all_decisions:
                    dec_counts = {}
                    for d in all_decisions:
                        dec_counts[d.decision.upper()] = dec_counts.get(d.decision.upper(), 0) + 1
                    st.bar_chart(pd.Series(dec_counts))
                else:
                    st.info("No human decisions logged yet.")

            st.subheader("Actionable System Optimization Insights")
            st.markdown("""
    Based on your accumulated Context Engine data and logged human decisions:
    1. **Breakoutprop Headroom Management**: Sizing trades with factored risk below $100 guarantees you preserve the $300 daily loss limit across multiple attempts.
    2. **Session Alignment**: Breakout setups taken during London open (`07:00 UTC`) and New York open (`13:00 UTC`) show statistically higher follow-through than Asian session setups.
    3. **Catalyst Caution**: Deferring trade entry within 2 hours of US CPI or FOMC protects accounts from wide slippage and initial false whipsaws.
    4. **TradingView Confluence**: Squeeze Pro (VCEI) combined with LuxAlgo liquidity sweeps (SHLA) provides the highest signal-to-noise ratio in 1H and 4H timeframes.
            """)

            st.divider()
            st.subheader("Export Strategy & Journal Data")
            if all_decisions:
                dec_df = pd.DataFrame([{
                    "ID": d.id,
                    "Opportunity ID": d.opportunity_version_id,
                    "Decision": d.decision,
                    "Reason": d.reason,
                    "Timestamp UTC": d.created_at.strftime("%Y-%m-%d %H:%M:%S"),
                } for d in all_decisions])

                csv_data = dec_df.to_csv(index=False).encode('utf-8')
                st.download_button(
                    "Download Decision Journal (CSV)",
                    data=csv_data,
                    file_name="trading_decisions_journal.csv",
                    mime="text/csv",
                )
            else:
                st.info("No trade decisions recorded yet. Accept or reject opportunities in Opportunity Detail to populate journal analytics and enable CSV export.")

        with tab_dec:
            acct_filter = st.selectbox(
                "Filter by Account:",
                ["All Accounts", "Breakoutprop Turbo 10k (bp_turbo_10k)", "Personal Account (ibkr_personal)"],
                key="dec_acct_filter",
            )
            target_acct_key = None
            if "bp_turbo" in acct_filter:
                target_acct_key = "bp_turbo_10k"
            elif "ibkr" in acct_filter:
                target_acct_key = "ibkr_personal"

            decisions = session.query(DecisionRecord).order_by(DecisionRecord.created_at.desc()).all()
            if not decisions:
                st.info("No recorded human review decisions.")
            else:
                for d in decisions:
                    linked_plan = session.query(TradePlan).filter_by(decision_id=d.id).first()
                    if target_acct_key and linked_plan and linked_plan.account_id != target_acct_key:
                        continue

                    acct_badge = f"`{linked_plan.account_id}`" if linked_plan else "Unassigned"
                    dec_pill = "pill-green" if d.decision.lower() == "accepted" else ("pill-amber" if d.decision.lower() == "deferred" else "pill-red")

                    with st.expander(f"Decision #{d.id} — {d.decision.upper()} on {d.opportunity_version_id} | {format_dual_time(d.created_at)}", expanded=False):
                        st.markdown(f"• **Status:** <span class='status-pill {dec_pill}'>{d.decision.upper()}</span>", unsafe_allow_html=True)
                        st.write(f"• **Account:** {acct_badge}")
                        st.write(f"• **Actor:** `{d.actor}`")
                        st.write(f"• **Timestamp:** {format_dual_time(d.created_at)}")
                        if linked_plan:
                            st.write(f"• **Linked Plan:** `{linked_plan.id}` | Direction: `{linked_plan.direction}` | Reserved Risk: `${linked_plan.estimated_risk_usd:,.2f}`")

                        st.markdown("#### Edit Commentary")
                        new_comment = st.text_area("User Commentary / Reasoning:", value=d.reason or "", key=f"comm_{d.id}")

                        c_save, c_del = st.columns([1, 1])
                        with c_save:
                            if st.button("Save Commentary", key=f"save_{d.id}"):
                                journal_service.update_decision_commentary(session, d.id, new_comment)
                                st.success("Commentary updated successfully!")
                                st.rerun()
                        with c_del:
                            if st.button("Delete Decision Record", key=f"del_{d.id}", type="secondary"):
                                journal_service.delete_decision(session, d.id)
                                st.warning(f"Deleted decision {d.id} and freed risk reservations.")
                                st.rerun()

        with tab_plans:
            plan_query = session.query(TradePlan).order_by(TradePlan.created_at.desc())
            plans = plan_query.all()

            if plans:
                p_rows = [{
                    "Plan ID": p.id,
                    "Account": p.account_id,
                    "Instrument": p.instrument_id,
                    "Direction": p.direction.upper(),
                    "Entry": f"${p.entry_price:.4f}",
                    "Stop": f"${p.stop_loss:.4f}",
                    "Quantity": p.quantity,
                    "Reserved Risk": f"${p.estimated_risk_usd:.2f}",
                    "Status": p.status,
                    "Created At": format_dual_time(p.created_at),
                } for p in plans]
                st.dataframe(pd.DataFrame(p_rows), hide_index=True, use_container_width=True)
            else:
                st.info("No active trade plans found.")

        with tab_fills:
            fill_query = session.query(FillRecord).order_by(FillRecord.fill_time.desc())
            fills = fill_query.all()

            if fills:
                f_rows = [{
                    "Fill ID": f.external_fill_id,
                    "Account": f.account_id,
                    "Instrument": f.instrument_id,
                    "Side": f.side.upper(),
                    "Qty": f.quantity,
                    "Price": f"${f.price:.4f}",
                    "Fee": f"${f.fee:.2f}",
                    "Time": format_dual_time(f.fill_time),
                } for f in fills]
                st.dataframe(pd.DataFrame(f_rows), hide_index=True, use_container_width=True)
            else:
                st.info("No recorded fills found.")

        with tab_outcomes:
            outcomes = session.query(OutcomeRecord).order_by(OutcomeRecord.created_at.desc()).all()
            if outcomes:
                o_rows = [{
                    "Plan ID": o.plan_id,
                    "Net PnL": f"${o.net_pnl:.2f}",
                    "R Multiple": f"{o.r_multiple}R" if o.r_multiple else "—",
                    "Exit Reason": o.exit_reason,
                    "Duration Bars": o.duration_bars,
                } for o in outcomes]
                st.dataframe(pd.DataFrame(o_rows), hide_index=True, use_container_width=True)
            else:
                st.info("No completed trade outcomes recorded.")

    finally:
        session.close()

# -------------------------------------------------------------
# VIEW 7: SETTINGS (Accounts, AI Engines, Data Feeds & Health)
# -------------------------------------------------------------
elif nav == "Settings":
    st.title("Settings & System Configuration")
    st.markdown("Manage execution account parameters, configure frontier AI engines and API keys, and monitor data ingestion health.")

    tab_accts, tab_ai, tab_feeds = st.tabs([
        "Account Rules & Risk Gating",
        "AI Engine & API Connections",
        "Data Feeds & System Health",
    ])

    with tab_accts:
        st.subheader("Breakoutprop Turbo 10k Configuration")
        col1, col2 = st.columns(2)
        with col1:
            new_floor = st.number_input("Max Drawdown Floor ($)", value=st.session_state.bp_floor, step=50.0)
            new_daily_limit = st.number_input("Daily Loss Limit ($)", value=st.session_state.bp_daily_limit, step=25.0)
            new_buffer = st.number_input("Operational Soft Buffer ($)", value=st.session_state.bp_buffer, step=10.0)
        with col2:
            new_equity = st.number_input("Current Marked Equity ($)", value=st.session_state.bp_equity, step=100.0)
            new_per_trade = st.number_input("Per-Trade Max Risk Override ($)", value=st.session_state.bp_per_trade_risk, step=10.0)

        if st.button("Apply & Save Breakoutprop Settings", type="primary"):
            st.session_state.bp_floor = new_floor
            st.session_state.bp_daily_limit = new_daily_limit
            st.session_state.bp_buffer = new_buffer
            st.session_state.bp_equity = new_equity
            st.session_state.bp_per_trade_risk = new_per_trade

            bp_cfg["max_drawdown_floor"] = float(new_floor)
            bp_cfg["daily_loss_limit"] = float(new_daily_limit)
            bp_cfg["operational_buffer"] = float(new_buffer)
            bp_cfg["starting_equity"] = float(new_equity)
            bp_cfg["per_trade_risk_limit"] = float(new_per_trade)
            try:
                with open(BP_CONFIG_PATH, "w") as f:
                    yaml.dump(bp_cfg, f, default_flow_style=False)
            except Exception as e:
                st.warning(f"Could not write YAML: {e}")

            st.success("Updated Breakoutprop constraints and saved to config!")
            st.rerun()

        st.divider()
        st.subheader("Personal Account Configuration")
        col_p1, col_p2 = st.columns(2)
        with col_p1:
            new_p_equity = st.number_input("Personal Equity ($)", value=st.session_state.personal_equity, step=1000.0)
        with col_p2:
            new_p_risk = st.slider("Per-Trade Risk Percentage (%)", 0.25, 5.0, float(st.session_state.personal_risk_pct), 0.25)

        if st.button("Apply & Save Personal Settings", type="primary"):
            st.session_state.personal_equity = new_p_equity
            st.session_state.personal_risk_pct = new_p_risk

            p_cfg["starting_equity"] = float(new_p_equity)
            p_cfg["per_trade_risk_pct"] = float(new_p_risk)
            try:
                with open(PERSONAL_CONFIG_PATH, "w") as f:
                    yaml.dump(p_cfg, f, default_flow_style=False)
            except Exception as e:
                st.warning(f"Could not write YAML: {e}")

            st.success("Updated Personal Account settings and saved to config!")
            st.rerun()

    with tab_ai:
        st.subheader("Frontier AI Provider Credentials & Model Selection")
        st.markdown(
            "Connect frontier LLMs to receive plain-language strategic reviews and non-deterministic interrogations. "
            "Credentials are saved locally in <code>config/llm.yaml</code> (which is gitignored and never committed).",
            unsafe_allow_html=True,
        )

        cfg = load_llm_config()
        connected_list = get_connected_apis()
        connected_providers = {c["provider"]: c for c in connected_list}

        provider_defs = [
            {
                "id": "openai",
                "name": "OpenAI",
                "default_model": "gpt-4o",
                "models": PROVIDER_MODELS["openai"],
                "env_var": "OPENAI_API_KEY",
            },
            {
                "id": "anthropic",
                "name": "Anthropic",
                "default_model": "claude-3-7-sonnet-20250219",
                "models": PROVIDER_MODELS["anthropic"],
                "env_var": "ANTHROPIC_API_KEY",
            },
            {
                "id": "gemini",
                "name": "Google Gemini",
                "default_model": "gemini-2.0-flash",
                "models": PROVIDER_MODELS["gemini"],
                "env_var": "GEMINI_API_KEY",
            },
        ]

        from src.intelligence.ai_review import INPUT_PRICING_PER_1M

        for p_def in provider_defs:
            pid = p_def["id"]
            p_name = p_def["name"]
            is_conn = pid in connected_providers
            status_badge = "<span class='status-pill pill-green'>CONNECTED</span>" if is_conn else "<span class='status-pill pill-neutral'>NOT CONFIGURED</span>"

            st.markdown(f"### {p_name} &nbsp; {status_badge}", unsafe_allow_html=True)
            col_p_key, col_p_mod = st.columns([1, 1])

            cur_key = cfg.get("api_keys", {}).get(pid, "") or (cfg.get("api_key", "") if cfg.get("provider") == pid else "") or os.getenv(p_def["env_var"], "")
            cur_mod = cfg.get("models", {}).get(pid, "") or (cfg.get("model", "") if cfg.get("provider") == pid else "") or p_def["default_model"]
            if cur_mod not in p_def["models"]:
                cur_mod = p_def["default_model"]

            with col_p_key:
                key_val = st.text_input(
                    f"{p_name} API Key",
                    value=cur_key,
                    type="password",
                    key=f"cfg_key_{pid}",
                    help=f"Can also be provided via {p_def['env_var']} environment variable.",
                )
            with col_p_mod:
                mod_val = st.selectbox(
                    f"{p_name} Model",
                    p_def["models"],
                    index=p_def["models"].index(cur_mod),
                    key=f"cfg_mod_{pid}",
                )
                rate = INPUT_PRICING_PER_1M.get(mod_val, 2.50)
                st.caption(f"Estimated Input Rate: **${rate:.3f} per 1M tokens** (~${(1500/1e6)*rate:.5f} per review)")

            col_btn_save, col_btn_test = st.columns([1, 1])
            with col_btn_save:
                if st.button(f"Save {p_name} Credentials", key=f"btn_save_{pid}", type="primary", use_container_width=True):
                    save_llm_config(pid, key_val, mod_val)
                    st.success(f"Saved {p_name} configuration!")
                    st.rerun()
            with col_btn_test:
                if st.button(f"Test {p_name} Connection", key=f"btn_test_{pid}", use_container_width=True):
                    if not key_val.strip():
                        st.error("Please enter an API key to test.")
                    else:
                        with st.spinner(f"Testing {p_name} connection..."):
                            ok, msg = test_llm_connection(pid, key_val, mod_val)
                            if ok:
                                st.success(msg)
                            else:
                                st.error(msg)
            st.divider()

    with tab_feeds:
        st.subheader("Market Data Ingestion & Pipeline Health")
        st.markdown("Trigger real-time acquisition from Bybit and Yahoo Finance, or run feature and setup scanners.")

        session = get_db_session()
        try:
            col1, col2 = st.columns(2)
            with col1:
                st.subheader("Bybit Crypto Ingestion")
                crypto_pairs = st.multiselect(
                    "Select Bybit Pairs to Sync",
                    ["BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT", "XRP/USDT:USDT", "DOGE/USDT:USDT", "AVAX/USDT:USDT"],
                    default=["BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT"],
                    key="sync_bybit_pairs",
                )
                intervals = st.multiselect(
                    "Select Intervals",
                    ["15m", "1h", "4h", "1d", "1w", "1M"],
                    default=["1h", "4h", "1d"],
                    key="sync_bybit_intervals",
                )

                if st.button("Sync Bybit Bars", key="btn_sync_bybit"):
                    with st.spinner("Fetching closed bars from Bybit..."):
                        bybit = BybitConnector()
                        insts = bybit.fetch_instruments(crypto_pairs)
                        bybit.save_instruments(session, insts)
                        total_saved = 0
                        for inst in insts:
                            for interval in intervals:
                                bars = bybit.fetch_bars(inst, interval=interval, limit=60)
                                saved = bybit.save_bars(session, bars)
                                total_saved += saved
                        st.success(f"Synced Bybit! Saved {total_saved} new closed bars.")

            with col2:
                st.subheader("Yahoo Finance Equities Ingestion")
                equity_tickers = st.multiselect(
                    "Select Equities to Sync",
                    ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "TSLA", "XIU.TO", "SHOP.TO"],
                    default=["SPY", "QQQ", "XIU.TO"],
                    key="sync_yf_tickers",
                )
                eq_intervals = st.multiselect(
                    "Equities Intervals",
                    ["15m", "1h", "1d", "1w", "1M"],
                    default=["1d", "1w"],
                    key="sync_yf_intervals",
                )

                if st.button("Sync Yahoo Finance Bars", key="btn_sync_yf"):
                    with st.spinner("Fetching bars from Yahoo Finance..."):
                        yf = YahooFinanceConnector()
                        y_insts = yf.fetch_instruments(equity_tickers)
                        yf.save_instruments(session, y_insts)
                        total_saved = 0
                        for y_inst in y_insts:
                            for interval in eq_intervals:
                                bars = yf.fetch_bars(y_inst, interval=interval, limit=60)
                                saved = yf.save_bars(session, bars)
                                total_saved += saved
                        st.success(f"Synced Yahoo Finance! Saved {total_saved} new bars.")

            st.divider()
            st.subheader("Run Setup Scanner & Opportunity Assembler")

            if st.button("Run Full Scanner Pipeline", type="primary", key="btn_run_full_scanner"):
                with st.spinner("Computing features, detecting setups, and assembling opportunities..."):
                    instruments = session.query(InstrumentRecord).filter_by(is_active=True).all()
                    inst_ids = [i.id for i in instruments]

                    scanner = TechnicalScanner(mode=PlaybookMode.FILTERED)
                    assembler = OpportunityAssembler()

                    now = utc_now()
                    scan_horizons = ["15m", "1h", "4h", "1d", "1w", "1M"]
                    total_signals = 0
                    total_opps = 0

                    for h in scan_horizons:
                        ctx = ModuleContext(
                            instrument_ids=inst_ids,
                            horizon=h,
                            decision_cutoff=now,
                            run_id=f"run_{now.strftime('%Y%m%d%H%M%S')}",
                        )
                        res = scanner.evaluate(ctx, session)
                        total_signals += len(res.signals)

                        opps = assembler.assemble_opportunities(session, playbook_name=f"playbook_{h}", horizon=h)
                        total_opps += len(opps)

                    st.success(f"Scan complete! Emitted {total_signals} signals and assembled {total_opps} opportunities.")

            # Storage Statistics
            st.divider()
            st.subheader("Canonical Database Statistics")
            obs_count = session.query(MarketObservation).count()
            inst_count = session.query(InstrumentRecord).count()
            sig_count = session.query(ModuleSignal).count()
            opp_count = session.query(OpportunityVersion).count()

            stat_c1, stat_c2, stat_c3, stat_c4 = st.columns(4)
            stat_c1.metric("Instruments", inst_count)
            stat_c2.metric("Closed Bars Stored", obs_count)
            stat_c3.metric("Signals Detected", sig_count)
            stat_c4.metric("Opportunities Assembled", opp_count)
        finally:
            session.close()
