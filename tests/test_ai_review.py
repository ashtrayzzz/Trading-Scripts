"""Unit tests for the multi-provider AI Review module and focused payload scoping."""

from unittest.mock import MagicMock, patch
import json
import pytest

from src.intelligence.ai_review import (
    DEFAULT_MODELS,
    PROVIDER_MODELS,
    estimate_prompt_cost,
    generate_ai_review,
    load_llm_config,
    save_llm_config,
    verify_llm_connection as check_llm_connection,
)
from src.intelligence.llm_scaffold import LLMScaffold


def test_token_and_cost_estimation():
    """Verify token counting and cost estimates across providers and models."""
    sample_prompt = "Interrogate this trade opportunity with focus on invalidation and capital preservation." * 20
    
    # OpenAI gpt-4o
    est_openai = estimate_prompt_cost(sample_prompt, "openai", "gpt-4o")
    assert est_openai["estimated_tokens"] > 100
    assert est_openai["estimated_cost_usd"] > 0
    assert est_openai["model"] == "gpt-4o"

    # Anthropic Claude 3.5 Sonnet
    est_anthropic = estimate_prompt_cost(sample_prompt, "anthropic", "claude-3-5-sonnet-20241022")
    assert est_anthropic["estimated_tokens"] > 100
    assert est_anthropic["estimated_cost_usd"] > 0

    # Gemini 2.0 Flash
    est_gemini = estimate_prompt_cost(sample_prompt, "gemini", "gemini-2.0-flash")
    assert est_gemini["estimated_tokens"] > 100
    assert est_gemini["estimated_cost_usd"] > 0
    # Gemini 2.0 flash is cheaper than gpt-4o
    assert est_gemini["estimated_cost_usd"] < est_openai["estimated_cost_usd"]


def test_build_focused_payload_scoping():
    """Verify that build_focused_payload cleanly isolates used evidence from secondary background."""
    scaffold = LLMScaffold()

    mock_opp = MagicMock()
    mock_opp.instrument_id = "bybit:ETH_USDT_USDT:perpetual"
    mock_opp.horizon = "1h"
    mock_opp.direction = "long"
    mock_opp.merit_score = 91.5
    mock_opp.confidence_score = 0.88
    mock_opp.trigger_price = 3500.0
    mock_opp.invalidation_price = 3420.0
    mock_opp.thesis = "Bullish breakout from 1h compression zone"
    mock_opp.counter_evidence = "Minor volume divergence"

    ctx_summary = {
        "macro": {"macro_regime": "risk_on_expansion", "us_10y_yield": 4.15, "dollar_index_trend": "falling"},
        "seasonality": {"current_session": "London Open", "session_bias": "expansion", "liquidity_level": "high"},
        "sentiment": {"fear_greed_score": 62, "fear_greed_class": "Greed", "equity_breadth": "positive"},
        "catalysts": [{"event": "US CPI", "hours_until": 2.5, "impact": "HIGH", "trading_warning": "High volatility"}],
        "clean_window_for_breakouts": False,
    }

    acct_state = {
        "equity": 10000.0,
        "drawdown_floor": 9700.0,
        "headroom": 250.0,
        "daily_loss_limit": 300.0,
        "factored_risk_usd": 75.0,
    }

    payload = scaffold.build_focused_payload(
        opp=mock_opp,
        context_summary=ctx_summary,
        account_id="bp_turbo_10k",
        account_state=acct_state,
        user_notes="Entering at candle close",
    )

    assert "used_context" in payload
    assert "unused_context" in payload
    assert "focused_prompt" in payload

    used = payload["used_context"]
    unused = payload["unused_context"]

    # Direct signal triggers should be in used
    assert "ETH_USDT_USDT" in used
    assert "3500.0000" in used
    assert "3420.0000" in used
    assert "91.5" in used
    assert "Entering at candle close" in used
    assert "US CPI" in used  # Within 4h, so it's in used

    # Macro and Sentiment background should be noted in unused
    assert "BACKGROUND CONTEXT" in unused
    assert "RISK_ON_EXPANSION" in unused
    assert "Fear & Greed = 62/100" in unused


def test_run_context_audit_deterministic():
    """Verify run_context_audit executes deterministic rules without calling external APIs."""
    scaffold = LLMScaffold()

    mock_opp = MagicMock()
    mock_opp.instrument_id = "yahoo:SPY:stock"
    mock_opp.horizon = "1d"
    mock_opp.direction = "short"
    mock_opp.merit_score = 65.0
    mock_opp.confidence_score = 0.70
    mock_opp.trigger_price = 560.0
    mock_opp.invalidation_price = 568.0
    mock_opp.thesis = "Short pullback off resistance"
    mock_opp.counter_evidence = None

    ctx_summary = {
        "macro": {"macro_regime": "risk_on_expansion"},
        "clean_window_for_breakouts": True,
    }

    acct_state = {
        "headroom": 500.0,
        "factored_risk_usd": 100.0,
    }

    audit = scaffold.run_context_audit(
        opp=mock_opp,
        context_summary=ctx_summary,
        account_id="ibkr_personal",
        account_state=acct_state,
    )

    # Shorting against risk_on_expansion should trigger MODIFY_RISK
    assert audit["verdict"] == "MODIFY_RISK"
    assert "Risk-On Expansion" in audit["key_risks_and_blindspots"][0]


@patch("openai.OpenAI")
def test_generate_ai_review_mock_openai(mock_openai_class):
    """Verify OpenAI adapter dispatches prompt and returns normalized dictionary."""
    mock_client = MagicMock()
    mock_openai_class.return_value = mock_client

    mock_resp = MagicMock()
    mock_resp.choices = [
        MagicMock(
            message=MagicMock(
                content=json.dumps({
                    "verdict": "ACCEPT",
                    "confluence_confidence": 0.88,
                    "executive_summary": "High confluence breakout aligned with institutional London liquidity.",
                    "key_risks_and_blindspots": ["Ensure stop is defended at 3420."],
                    "sizing_and_plan_refinement": {
                        "recommended_risk_usd": 75.0,
                        "adjusted_entry": 3500.0,
                        "adjusted_stop": 3420.0,
                        "target_r2": 3660.0,
                        "target_r3": 3740.0,
                    },
                    "playbook_learning_feedback": "Monitor volume confirmation.",
                })
            )
        )
    ]
    mock_client.chat.completions.create.return_value = mock_resp

    result = generate_ai_review(
        prompt="Interrogate trade candidate",
        provider="openai",
        api_key="sk-fake-key",
        model="gpt-4o",
    )

    assert result["verdict"] == "ACCEPT"
    assert result["confluence_confidence"] == 0.88
    assert "High confluence" in result["executive_summary"]
    assert result["provider"] == "openai"
    assert result["model"] == "gpt-4o"
    assert result["estimated_tokens"] > 0


def test_get_connected_apis_detection():
    """Verify that get_connected_apis properly discovers active credentials."""
    from src.intelligence.ai_review import get_connected_apis

    # When no keys configured
    with patch.dict("os.environ", {}, clear=True), patch("src.intelligence.ai_review.load_llm_config", return_value={"api_keys": {}, "models": {}}):
        connected = get_connected_apis()
        assert connected == []

    # When OpenAI and Gemini are active
    mock_cfg = {
        "api_keys": {"openai": "sk-test-key", "gemini": "AIza-test"},
        "models": {"openai": "gpt-4o", "gemini": "gemini-2.0-flash"},
    }
    with patch("src.intelligence.ai_review.load_llm_config", return_value=mock_cfg):
        connected = get_connected_apis()
        assert len(connected) == 2
        providers = [c["provider"] for c in connected]
        assert "openai" in providers
        assert "gemini" in providers
        assert "anthropic" not in providers

