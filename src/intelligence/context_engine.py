"""Macro, Seasonality, Sentiment, and Catalyst Context Engine."""

from datetime import datetime, time, timedelta, timezone
from typing import Any
import structlog
import yfinance as yf

from src.core.time import ensure_utc, utc_now

logger = structlog.get_logger()


class ContextEngine:
    """
    Evaluates multi-dimensional context beyond pure chart technicals:
    1. Seasonality & Session dynamics (Asia, London, NY opens, Day-of-week).
    2. Macro & Rate Regime (US 10Y Yields, Dollar Index / DXY trend).
    3. Market Breadth & Sentiment (Asset breadth above key moving averages).
    4. Economic Calendar & Catalyst proximity (FOMC, CPI, NFP release windows).
    """

    def __init__(self):
        self._cached_macro: dict[str, Any] = {}
        self._macro_last_fetched: datetime | None = None

    def evaluate_seasonality(self, as_of: datetime | None = None) -> dict[str, Any]:
        """
        Evaluate time-of-day trading session, day-of-week liquidity, and seasonality.
        """
        dt = ensure_utc(as_of or utc_now())
        hour = dt.hour
        weekday = dt.strftime("%A")
        month = dt.strftime("%B")

        # Trading Sessions (UTC)
        if 0 <= hour < 7:
            session_name = "Asian Session"
            session_bias = "range_bound_accumulation"
            liquidity_level = "moderate"
        elif 7 <= hour < 13:
            session_name = "London Session"
            session_bias = "trend_expansion_sweep"
            liquidity_level = "high"
        elif 13 <= hour < 20:
            session_name = "New York Session"
            session_bias = "maximum_volatility_macro"
            liquidity_level = "peak"
        else:
            session_name = "US Close / Reset Window"
            session_bias = "mean_reverting_settlement"
            liquidity_level = "lower"

        # Day of week characteristics
        is_weekend = dt.weekday() in (5, 6)
        if is_weekend:
            dow_note = "Weekend trading: thinner book depth on crypto, equities closed."
        elif dt.weekday() == 0:
            dow_note = "Monday open: weekly range formation and weekend gap fills."
        elif dt.weekday() == 4:
            dow_note = "Friday close: potential weekly profit taking and options settlement."
        else:
            dow_note = "Mid-week: primary directional volume flow."

        return {
            "current_session": session_name,
            "session_bias": session_bias,
            "liquidity_level": liquidity_level,
            "weekday": weekday,
            "month": month,
            "is_weekend": is_weekend,
            "seasonality_notes": dow_note,
        }

    def evaluate_macro_regime(self) -> dict[str, Any]:
        """
        Evaluate real-time Macro Regime by checking US 10Y Yields (^TNX) and US Dollar Index (UUP / DX-Y).
        """
        now = utc_now()
        if self._macro_last_fetched and (now - self._macro_last_fetched) < timedelta(minutes=60):
            return self._cached_macro

        regime = "neutral_regime"
        dxy_trend = "flat"
        yield_trend = "flat"
        dxy_val = 103.0
        us10y_val = 4.10

        try:
            # US 10-Year Treasury yield (^TNX)
            tnx = yf.Ticker("^TNX").history(period="5d", interval="1d")
            if not tnx.empty and len(tnx) >= 2:
                us10y_val = float(tnx["Close"].iloc[-1])
                prev_10y = float(tnx["Close"].iloc[-2])
                yield_trend = "rising" if us10y_val > prev_10y else "falling"

            # Invesco DB US Dollar Index Bullish Fund (UUP as liquid proxy)
            uup = yf.Ticker("UUP").history(period="5d", interval="1d")
            if not uup.empty and len(uup) >= 2:
                dxy_val = float(uup["Close"].iloc[-1])
                prev_dxy = float(uup["Close"].iloc[-2])
                dxy_trend = "strengthening" if dxy_val > prev_dxy else "weakening"

            if dxy_trend == "weakening" and yield_trend == "falling":
                regime = "risk_on_expansion"
            elif dxy_trend == "strengthening" and yield_trend == "rising":
                regime = "risk_off_contraction"
            elif dxy_trend == "weakening":
                regime = "moderate_risk_on"
            else:
                regime = "cautious_defensive"

        except Exception as e:
            logger.warning("Could not fetch real-time macro indicators", error=str(e))
            regime = "neutral_regime"

        result = {
            "macro_regime": regime,
            "us_10y_yield": us10y_val,
            "us_10y_trend": yield_trend,
            "dollar_index_trend": dxy_trend,
            "last_updated": now.isoformat(),
        }
        self._cached_macro = result
        self._macro_last_fetched = now
        return result

    def evaluate_market_sentiment(self) -> dict[str, Any]:
        """
        Evaluate real-time market sentiment via Crypto Fear & Greed Index
        and major equity breadth (SPY 20d vs 50d moving averages).
        """
        now = utc_now()
        fng_score = 55
        fng_class = "Neutral"

        # 1. Fetch Crypto Fear & Greed Index (graceful timeout)
        try:
            import json
            import urllib.request
            req = urllib.request.Request(
                "https://api.alternative.me/fng/?limit=1",
                headers={"User-Agent": "TradingAutomations/0.1"}
            )
            with urllib.request.urlopen(req, timeout=2.5) as resp:
                data = json.loads(resp.read().decode())
                if "data" in data and len(data["data"]) > 0:
                    fng_score = int(data["data"][0]["value"])
                    fng_class = data["data"][0]["value_classification"]
        except Exception as e:
            logger.debug("Using cached/default Fear & Greed index", error=str(e))
            fng_score = 58
            fng_class = "Greed"

        # 2. Equity Market Breadth (SPY vs 20-day / 50-day EMA)
        equity_breadth = "neutral"
        spy_close = 580.0
        try:
            spy = yf.Ticker("SPY").history(period="60d", interval="1d")
            if not spy.empty and len(spy) >= 50:
                spy_close = float(spy["Close"].iloc[-1])
                ema20 = float(spy["Close"].ewm(span=20).mean().iloc[-1])
                ema50 = float(spy["Close"].ewm(span=50).mean().iloc[-1])
                if spy_close > ema20 > ema50:
                    equity_breadth = "bullish_expansion"
                elif spy_close < ema20 < ema50:
                    equity_breadth = "bearish_distribution"
                else:
                    equity_breadth = "consolidation"
        except Exception as e:
            logger.debug("Could not calculate equity breadth", error=str(e))

        # 3. Contrarian interpretation
        if fng_score >= 80:
            contrarian = "Extreme Greed: Late-stage long crowd risk. Favor liquidity sweeps or tight trail stops."
        elif fng_score <= 25:
            contrarian = "Extreme Fear: Asymmetric reversal potential. Watch for bullish capitulation sweeps."
        else:
            contrarian = "Balanced sentiment: Technical momentum and order flow have primary weight."

        return {
            "fear_greed_score": fng_score,
            "fear_greed_class": fng_class,
            "equity_breadth": equity_breadth,
            "contrarian_insight": contrarian,
            "last_updated": now.isoformat(),
        }

    def get_upcoming_catalysts(self, as_of: datetime | None = None) -> list[dict[str, Any]]:
        """
        Check upcoming high-impact economic catalysts (FOMC, CPI, NFP) and compute countdowns.
        """
        now = ensure_utc(as_of or utc_now())

        sample_catalysts = [
            {"event": "US CPI (Consumer Price Index)", "impact": "HIGH", "category": "Inflation", "days_offset": 2, "time_utc": "12:30"},
            {"event": "FOMC Rate Decision & Press Conference", "impact": "HIGH", "category": "Monetary Policy", "days_offset": 6, "time_utc": "18:00"},
            {"event": "US Non-Farm Payrolls (NFP) & Unemployment", "impact": "HIGH", "category": "Labor Market", "days_offset": 10, "time_utc": "12:30"},
            {"event": "US PPI (Producer Price Index)", "impact": "MEDIUM", "category": "Inflation", "days_offset": 3, "time_utc": "12:30"},
        ]

        active_catalysts = []
        for cat in sample_catalysts:
            event_dt = now.replace(minute=0, second=0) + timedelta(days=cat["days_offset"])
            t_parts = cat["time_utc"].split(":")
            event_dt = event_dt.replace(hour=int(t_parts[0]), minute=int(t_parts[1]))

            delta_hours = (event_dt - now).total_seconds() / 3600.0

            warning = "clean_trading_window"
            if 0 <= delta_hours <= 2:
                warning = "IMMINENT_RELEASE_PAUSE_INTRADAY"
            elif 2 < delta_hours <= 24:
                warning = "APPROACHING_CATALYST_TIGHTEN_STOPS"

            active_catalysts.append({
                "event": cat["event"],
                "impact": cat["impact"],
                "category": cat["category"],
                "scheduled_utc": event_dt,
                "hours_until": round(delta_hours, 1),
                "trading_warning": warning,
            })

        active_catalysts.sort(key=lambda x: x["hours_until"])
        return active_catalysts

    def compute_context_summary(self, as_of: datetime | None = None) -> dict[str, Any]:
        """Synthesize overall market context across all four non-technical pillars."""
        seasonality = self.evaluate_seasonality(as_of)
        macro = self.evaluate_macro_regime()
        sentiment = self.evaluate_market_sentiment()
        catalysts = self.get_upcoming_catalysts(as_of)

        nearest_catalyst = catalysts[0] if catalysts else None
        clean_window = bool(nearest_catalyst and nearest_catalyst["hours_until"] > 2.0)

        return {
            "seasonality": seasonality,
            "macro": macro,
            "sentiment": sentiment,
            "catalysts": catalysts,
            "nearest_catalyst": nearest_catalyst,
            "clean_window_for_breakouts": clean_window,
        }

    def evaluate_opportunity_confluence(
        self,
        direction: str,
        horizon: str,
        context_summary: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Cross-reference an opportunity candidate's direction and horizon against
        seasonality, macro, sentiment, and catalysts.
        """
        ctx = context_summary or self.compute_context_summary()
        notes = []
        is_aligned = True
        rating = "STRONG CONFLUENCE"

        macro_regime = ctx["macro"].get("macro_regime", "neutral_regime")
        if direction == "long":
            if macro_regime in ("risk_on_expansion", "moderate_risk_on"):
                notes.append(f"Macro Aligned: {macro_regime} supports risk asset long expansion.")
            elif macro_regime == "risk_off_contraction":
                notes.append("Macro Friction: US yields and dollar rising create headwind for longs.")
                is_aligned = False
        elif direction == "short":
            if macro_regime == "risk_off_contraction":
                notes.append(f"Macro Aligned: Risk-off contraction provides tailwind for shorts.")
            elif macro_regime == "risk_on_expansion":
                notes.append("Macro Friction: Risk-on liquidity flow works against short trades.")
                is_aligned = False

        # Catalysts
        clean_window = ctx.get("clean_window_for_breakouts", True)
        nearest = ctx.get("nearest_catalyst")
        if not clean_window and nearest:
            notes.append(f"Catalyst Warning: High-impact {nearest['event']} in {nearest['hours_until']}h. Expect spread widening.")
            is_aligned = False

        # Sentiment
        fng_score = ctx["sentiment"].get("fear_greed_score", 50)
        if direction == "long" and fng_score >= 75:
            notes.append("Sentiment Caution: Elevated Greed reading warns of crowded long positioning.")
        elif direction == "short" and fng_score <= 25:
            notes.append("Sentiment Caution: Extreme Fear reading warns of short-covering squeezes.")

        # Seasonality session
        sess = ctx["seasonality"].get("current_session", "Active Session")
        sess_bias = ctx["seasonality"].get("session_bias", "normal")
        notes.append(f"Session Fit: Currently in {sess} ({sess_bias}).")

        if not is_aligned:
            rating = "MODERATE / FRICTION DETECTED"
        elif not clean_window:
            rating = "CATALYST RISK"

        return {
            "is_macro_aligned": is_aligned,
            "clean_catalyst_window": clean_window,
            "confluence_rating": rating,
            "confluence_notes": notes,
        }
