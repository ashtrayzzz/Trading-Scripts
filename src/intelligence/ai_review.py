"""Multi-Provider LLM Strategic Review Adapter for Trading Intelligence.

Supports OpenAI, Anthropic, and Google Gemini with unified structured output,
API key persistence in config/llm.yaml, token/cost estimation, and connection diagnostics.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
from typing import Any
import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_PATH = PROJECT_ROOT / "config" / "llm.yaml"

# Supported models and pricing (USD per 1,000,000 input tokens)
PROVIDER_MODELS: dict[str, list[str]] = {
    "openai": ["gpt-4o", "gpt-4o-mini", "o3-mini", "gpt-4-turbo"],
    "anthropic": [
        "claude-3-7-sonnet-20250219",
        "claude-3-5-sonnet-20241022",
        "claude-3-5-haiku-20241022",
        "claude-3-opus-20240229",
    ],
    "gemini": [
        "gemini-2.0-flash",
        "gemini-2.0-flash-lite",
        "gemini-1.5-pro",
        "gemini-1.5-flash",
    ],
}

DEFAULT_MODELS: dict[str, str] = {
    "openai": "gpt-4o",
    "anthropic": "claude-3-7-sonnet-20250219",
    "gemini": "gemini-2.0-flash",
}

INPUT_PRICING_PER_1M: dict[str, float] = {
    "gpt-4o": 2.50,
    "gpt-4o-mini": 0.15,
    "o3-mini": 1.10,
    "gpt-4-turbo": 10.00,
    "claude-3-7-sonnet-20250219": 3.00,
    "claude-3-5-sonnet-20241022": 3.00,
    "claude-3-5-haiku-20241022": 0.80,
    "claude-3-opus-20240229": 15.00,
    "gemini-2.0-flash": 0.10,
    "gemini-2.0-flash-lite": 0.075,
    "gemini-1.5-pro": 1.25,
    "gemini-1.5-flash": 0.075,
}


def load_llm_config() -> dict[str, Any]:
    """Load configuration from config/llm.yaml with fallback to environment variables."""
    cfg: dict[str, Any] = {
        "provider": "",
        "api_key": "",
        "model": "",
        "api_keys": {},
        "models": {},
    }
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r") as f:
                loaded = yaml.safe_load(f) or {}
                if isinstance(loaded, dict):
                    cfg.update(loaded)
        except Exception:
            pass

    if not isinstance(cfg.get("api_keys"), dict):
        cfg["api_keys"] = {}
    if not isinstance(cfg.get("models"), dict):
        cfg["models"] = {}

    # Seed api_keys with provider/api_key if present
    if cfg.get("provider") and cfg.get("api_key"):
        cfg["api_keys"][cfg["provider"]] = cfg["api_key"]
    if cfg.get("provider") and cfg.get("model"):
        cfg["models"][cfg["provider"]] = cfg["model"]

    # Environment variable fallbacks
    if os.getenv("OPENAI_API_KEY") and not cfg["api_keys"].get("openai"):
        cfg["api_keys"]["openai"] = os.getenv("OPENAI_API_KEY", "")
    if os.getenv("ANTHROPIC_API_KEY") and not cfg["api_keys"].get("anthropic"):
        cfg["api_keys"]["anthropic"] = os.getenv("ANTHROPIC_API_KEY", "")
    gem_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if gem_key and not cfg["api_keys"].get("gemini"):
        cfg["api_keys"]["gemini"] = gem_key

    # Ensure active provider and api_key are set if any provider has a key
    if not cfg.get("api_key"):
        provider = cfg.get("provider") or ""
        if provider and cfg["api_keys"].get(provider):
            cfg["api_key"] = cfg["api_keys"][provider]
        else:
            # pick first available
            for p in ["openai", "anthropic", "gemini"]:
                if cfg["api_keys"].get(p):
                    cfg["provider"] = p
                    cfg["api_key"] = cfg["api_keys"][p]
                    break

    provider = cfg.get("provider", "")
    if provider and not cfg.get("model"):
        cfg["model"] = cfg["models"].get(provider, DEFAULT_MODELS.get(provider, ""))

    return cfg


def save_llm_config(provider: str, api_key: str, model: str = "") -> None:
    """Save configuration to config/llm.yaml while preserving keys for other providers."""
    provider = provider.strip().lower()
    existing: dict[str, Any] = {}
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r") as f:
                existing = yaml.safe_load(f) or {}
                if not isinstance(existing, dict):
                    existing = {}
        except Exception:
            existing = {}

    api_keys = existing.get("api_keys", {}) if isinstance(existing.get("api_keys"), dict) else {}
    models = existing.get("models", {}) if isinstance(existing.get("models"), dict) else {}

    if api_key.strip():
        api_keys[provider] = api_key.strip()
    selected_model = model.strip() or DEFAULT_MODELS.get(provider, "")
    if selected_model:
        models[provider] = selected_model

    existing["provider"] = provider
    existing["api_key"] = api_key.strip()
    existing["model"] = selected_model
    existing["api_keys"] = api_keys
    existing["models"] = models

    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        yaml.safe_dump(existing, f, default_flow_style=False)


def get_connected_apis() -> list[dict[str, Any]]:
    """Scan config/llm.yaml and environment variables for all active API credentials.

    Returns a list of active API dictionaries with provider, model, api_key, and display_name.
    """
    cfg = load_llm_config()
    api_keys = cfg.get("api_keys", {})
    models = cfg.get("models", {})

    connected = []

    # Check OpenAI
    openai_key = api_keys.get("openai", "").strip()
    if openai_key:
        model = models.get("openai") or DEFAULT_MODELS["openai"]
        connected.append({
            "provider": "openai",
            "display_name": f"OpenAI ({model})",
            "model": model,
            "api_key": openai_key,
        })

    # Check Anthropic
    anthropic_key = api_keys.get("anthropic", "").strip()
    if anthropic_key:
        model = models.get("anthropic") or DEFAULT_MODELS["anthropic"]
        connected.append({
            "provider": "anthropic",
            "display_name": f"Anthropic ({model})",
            "model": model,
            "api_key": anthropic_key,
        })

    # Check Gemini
    gemini_key = api_keys.get("gemini", "").strip()
    if gemini_key:
        model = models.get("gemini") or DEFAULT_MODELS["gemini"]
        connected.append({
            "provider": "gemini",
            "display_name": f"Google Gemini ({model})",
            "model": model,
            "api_key": gemini_key,
        })

    return connected


def estimate_prompt_cost(prompt: str, provider: str, model: str) -> dict[str, Any]:
    """Calculate estimated token count and USD cost for a prompt."""
    # Standard heuristic: ~4 characters per token
    char_count = len(prompt)
    est_tokens = max(int(char_count / 3.8), 50)
    
    # Lookup pricing
    rate_per_1m = INPUT_PRICING_PER_1M.get(model, 2.50)
    est_cost = (est_tokens / 1_000_000.0) * rate_per_1m

    return {
        "estimated_tokens": est_tokens,
        "estimated_cost_usd": est_cost,
        "rate_per_1m": rate_per_1m,
        "provider": provider,
        "model": model,
    }


def verify_llm_connection(provider: str, api_key: str, model: str = "") -> tuple[bool, str]:
    """Test API credentials with a minimal ping prompt."""
    provider = provider.strip().lower()
    api_key = api_key.strip()
    if not api_key:
        return False, "API key cannot be empty."

    if not model:
        model = DEFAULT_MODELS.get(provider, "")

    try:
        if provider == "openai":
            from openai import OpenAI
            client = OpenAI(api_key=api_key)
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": "Respond with single word: OK"}],
                max_tokens=10,
            )
            reply = resp.choices[0].message.content or ""
            return True, f"Connected to OpenAI ({model}): {reply.strip()}"

        elif provider == "anthropic":
            from anthropic import Anthropic
            client = Anthropic(api_key=api_key)
            resp = client.messages.create(
                model=model,
                max_tokens=10,
                messages=[{"role": "user", "content": "Respond with single word: OK"}],
            )
            reply = resp.content[0].text if resp.content else ""
            return True, f"Connected to Anthropic ({model}): {reply.strip()}"

        elif provider == "gemini":
            import google.generativeai as genai
            genai.configure(api_key=api_key)
            g_model = genai.GenerativeModel(model)
            resp = g_model.generate_content("Respond with single word: OK")
            reply = resp.text or ""
            return True, f"Connected to Google Gemini ({model}): {reply.strip()}"

        else:
            return False, f"Unsupported provider: {provider}"

    except Exception as exc:
        return False, f"Connection error ({provider}): {str(exc)}"


# Backward-compatible alias
test_llm_connection = verify_llm_connection


def _clean_json_string(raw: str) -> str:
    """Extract JSON object from markdown code blocks or surrounding text."""
    # Find ```json ... ```
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if match:
        return match.group(1).strip()
    
    # Or first { to last }
    first_brace = raw.find("{")
    last_brace = raw.rfind("}")
    if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
        return raw[first_brace : last_brace + 1].strip()
    
    return raw.strip()


def generate_ai_review(
    prompt: str,
    provider: str = "",
    api_key: str = "",
    model: str = "",
) -> dict[str, Any]:
    """Dispatch prompt to authenticated LLM and return structured analysis."""
    # Resolve configuration
    cfg = load_llm_config()
    provider = (provider or cfg.get("provider", "")).strip().lower()
    api_key = (api_key or cfg.get("api_key", "")).strip()
    model = (model or cfg.get("model", "")).strip() or DEFAULT_MODELS.get(provider, "")

    if not provider:
        raise ValueError("No LLM provider selected. Configure in Account Cockpit -> AI Engine.")
    if not api_key:
        raise ValueError(f"No API key provided for {provider.title()}. Please enter your API key in Account Cockpit.")

    system_instruction = (
        "You are the Chief Risk Officer and Strategic Portfolio Analyst for a quantitative trading firm. "
        "Interrogate trade setups with strict adherence to capital preservation, structural invalidation, and market context. "
        "You MUST respond ONLY with valid JSON matching the exact schema requested, with no conversational preamble."
    )

    raw_response_text = ""

    if provider == "openai":
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        # Use json_object response format where supported
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"} if "gpt-4" in model or "o3" in model else None,
            temperature=0.2,
        )
        raw_response_text = resp.choices[0].message.content or ""

    elif provider == "anthropic":
        from anthropic import Anthropic
        client = Anthropic(api_key=api_key)
        resp = client.messages.create(
            model=model,
            system=system_instruction,
            max_tokens=2048,
            temperature=0.2,
            messages=[{"role": "user", "content": prompt}],
        )
        raw_response_text = resp.content[0].text if resp.content else ""

    elif provider == "gemini":
        import google.generativeai as genai
        genai.configure(api_key=api_key)
        g_model = genai.GenerativeModel(
            model_name=model,
            system_instruction=system_instruction,
            generation_config={"response_mime_type": "application/json", "temperature": 0.2},
        )
        resp = g_model.generate_content(prompt)
        raw_response_text = resp.text or ""

    else:
        raise ValueError(f"Unknown LLM provider: {provider}")

    # Parse JSON output
    cleaned = _clean_json_string(raw_response_text)
    try:
        data = json.loads(cleaned)
    except Exception:
        # Fallback parsing
        data = {
            "verdict": "MODIFY_RISK",
            "confluence_confidence": 0.70,
            "executive_summary": raw_response_text[:300] + "...",
            "key_risks_and_blindspots": ["Could not parse structured JSON response; raw output preserved."],
            "sizing_and_plan_refinement": {
                "recommended_risk_usd": 100.0,
                "adjusted_entry": 0.0,
                "adjusted_stop": 0.0,
                "target_r2": 0.0,
                "target_r3": 0.0,
            },
            "playbook_learning_feedback": "Review raw response.",
        }

    # Normalize fields
    verdict = str(data.get("verdict", "MODIFY_RISK")).upper()
    if verdict not in ["ACCEPT", "MODIFY_RISK", "DEFER", "REJECT"]:
        verdict = "MODIFY_RISK"

    confluence_confidence = float(data.get("confluence_confidence", 0.75))
    if confluence_confidence > 1.0:
        confluence_confidence = confluence_confidence / 100.0
    confluence_confidence = min(max(confluence_confidence, 0.1), 0.99)

    exec_summary = str(data.get("executive_summary") or data.get("executive_synthesis") or "Strategic analysis complete.")
    risks = data.get("key_risks_and_blindspots") or []
    if isinstance(risks, str):
        risks = [risks]

    sizing = data.get("sizing_and_plan_refinement") or {}
    feedback = str(data.get("playbook_learning_feedback") or "Maintain disciplined risk.")

    cost_info = estimate_prompt_cost(prompt, provider, model)

    return {
        "verdict": verdict,
        "confluence_confidence": round(confluence_confidence, 2),
        "executive_summary": exec_summary,
        "key_risks_and_blindspots": risks,
        "sizing_and_plan_refinement": sizing,
        "playbook_learning_feedback": feedback,
        "raw_response": raw_response_text,
        "provider": provider,
        "model": model,
        "estimated_cost_usd": cost_info["estimated_cost_usd"],
        "estimated_tokens": cost_info["estimated_tokens"],
    }


def ask_ai_strategy_chat(
    opp_context: dict[str, Any],
    user_question: str,
    conversation_history: list[dict[str, str]] | None = None,
    provider: str = "",
    api_key: str = "",
    model: str = "",
) -> str:
    """Send an interactive tactical question to the connected AI agent about a specific trade setup,

    taking into account the original trigger time & price, latest market price, price drift %,
    and updated executable R:R.
    """
    cfg = load_llm_config()
    provider = (provider or cfg.get("provider", "")).strip().lower()
    api_key = (api_key or cfg.get("api_key", "")).strip()
    model = (model or cfg.get("model", "")).strip() or DEFAULT_MODELS.get(provider, "")

    if not provider:
        raise ValueError("No LLM provider selected. Configure in Settings -> AI Engine & API Connections.")
    if not api_key:
        raise ValueError(f"No API key provided for {provider.title()}. Configure in Settings.")

    system_instruction = (
        "You are an elite quantitative trading strategist and Chief Risk Officer. "
        "The trader is interrogating a specific active trade setup on a live market candidate. "
        "Carefully evaluate their question taking into account:\n"
        "1. The original trigger price and trigger timestamp.\n"
        "2. The latest live market price, current price drift percentage, and real-time executable R:R.\n"
        "3. The specific strategy mechanics (e.g. FVG mitigation, SFP liquidity sweep, Volatility Squeeze, Volume Profile POC).\n"
        "4. Risk management: strict adherence to hard stop loss levels, avoiding FOMO chase when price has drifted too far, "
        "and managing trade execution.\n"
        "Provide direct, concise, mathematically grounded, and tactical advice in clean GitHub-flavored Markdown. "
        "Avoid generic disclaimers. Give direct actionable guidance."
    )

    # Format the structured setup context with robust numeric defaults
    trig_p = float(opp_context.get('trigger_price') or 0.0)
    inv_p = float(opp_context.get('invalidation_price') or 0.0)
    stop_p = float(opp_context.get('stop_pct') or 0.0)
    t2r = float(opp_context.get('target_2r') or 0.0)
    t3r = float(opp_context.get('target_3r') or 0.0)
    live_p = float(opp_context.get('latest_price') or 0.0)
    drift = float(opp_context.get('drift_pct') or 0.0)
    exec_rr = float(opp_context.get('current_rr') or 0.0)

    context_text = f"""### Staged Trade Setup Context:
- **Ticker / Instrument**: {opp_context.get('ticker', 'N/A')} (`{opp_context.get('instrument_id', 'N/A')}`)
- **Playbook / Setup Name**: {opp_context.get('playbook_name', 'N/A')}
- **Timeframe Horizon**: {opp_context.get('horizon', 'N/A')}
- **Direction**: {str(opp_context.get('direction', 'N/A')).upper()}
- **Merit Score**: {opp_context.get('merit_score', 'N/A')} / 100 (Confidence: {opp_context.get('confidence_pct', 'N/A')}%)
- **Original Trigger**: ${trig_p:.4f} (Triggered at {opp_context.get('trigger_time', 'N/A')})
- **Hard Invalidation Stop**: ${inv_p:.4f} ({stop_p:.2f}% risk)
- **Original Profit Targets**: 2R at ${t2r:.4f} | 3R at ${t3r:.4f}
- **Current Live Market Price**: ${live_p:.4f}
- **Live Price Drift from Trigger**: {drift:+.2f}%
- **Current Real-Time Executable R:R**: {exec_rr:.2f}R (vs 2.0R initial target)
- **Technical Signals & Confluence**: {opp_context.get('technical_signals', 'N/A')}
- **Strategy Execution Playbook**: {opp_context.get('how_summary', 'N/A')}
- **Catalysts / Macro Context**: {opp_context.get('macro_context', 'Normal trading session')}

### Trader's Tactical Question:
{user_question}
"""

    if provider == "openai":
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        messages = [{"role": "system", "content": system_instruction}]
        if conversation_history:
            for msg in conversation_history[-6:]:
                messages.append({"role": msg["role"], "content": msg["content"]})
        messages.append({"role": "user", "content": context_text})

        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.3,
        )
        return resp.choices[0].message.content or "No response received."

    elif provider == "anthropic":
        from anthropic import Anthropic
        client = Anthropic(api_key=api_key)
        messages = []
        if conversation_history:
            for msg in conversation_history[-6:]:
                messages.append({"role": msg["role"], "content": msg["content"]})
        messages.append({"role": "user", "content": context_text})

        resp = client.messages.create(
            model=model,
            system=system_instruction,
            max_tokens=2048,
            temperature=0.3,
            messages=messages,
        )
        return resp.content[0].text if resp.content else "No response received."

    elif provider == "gemini":
        import google.generativeai as genai
        genai.configure(api_key=api_key)
        g_model = genai.GenerativeModel(model_name=model, system_instruction=system_instruction)
        resp = g_model.generate_content(context_text)
        return resp.text or "No response received."

    else:
        raise ValueError(f"Unknown LLM provider: {provider}")
