"""Technical scanner implementing broad positive-EV discovery and 6 named setups."""

from datetime import datetime
from typing import Any
import structlog
from sqlalchemy.orm import Session

from src.core.enums import PlaybookMode, QualityStatus, SignalDirection
from src.core.models import FeatureSnapshot, MarketObservation, ModuleSignal
from src.core.time import interval_to_timedelta, utc_now
from src.intelligence.base import BaseModule, ModuleContext, ModuleResult
from src.intelligence.features import compute_features_for_bars

logger = structlog.get_logger()


class TechnicalScanner(BaseModule):
    """
    Technical analysis scanner running on closed bars only.
    
    Operates in two modes:
    1. Discovery mode: Broad scanning for anomalous positive-EV conditions (compression, volume surge, structure testing).
    2. Playbook mode: Evaluates 6 specific high-conviction setups.
    """

    def __init__(self, mode: PlaybookMode = PlaybookMode.FILTERED):
        super().__init__(name="technical_scanner", version="0.1.0")
        self.mode = mode

    def evaluate(
        self,
        context: ModuleContext,
        session: Session,
    ) -> ModuleResult:
        """Evaluate technical setups across instruments."""
        signals: list[ModuleSignal] = []
        feature_ids: list[str] = []
        now = utc_now()

        for inst_id in context.instrument_ids:
            # Query recent closed observations
            observations = (
                session.query(MarketObservation)
                .filter_by(instrument_id=inst_id, interval=context.horizon, is_closed=True)
                .order_by(MarketObservation.open_time.asc())
                .all()
            )

            if len(observations) < 20:
                continue

            # Compute features deterministically
            snapshot = compute_features_for_bars(
                instrument_id=inst_id,
                horizon=context.horizon,
                observations=observations,
                as_of=context.decision_cutoff,
            )
            if not snapshot:
                continue

            session.add(snapshot)
            session.flush()
            feature_ids.append(snapshot.id)

            f = snapshot.feature_values

            # 1. Broad Discovery Check (Positive EV conditions)
            discovery_hits = self._check_discovery_anomalies(f)
            if discovery_hits:
                close_px = f["latest_close"]
                atr_val = f.get("atr_14", 1.0)
                safe_buffer = max(0.5 * atr_val, 0.002 * close_px)
                s_dir = discovery_hits["suggested_direction"]
                if s_dir == SignalDirection.LONG.value:
                    inv_px = min(f.get("latest_low", close_px - safe_buffer), close_px - safe_buffer)
                elif s_dir == SignalDirection.SHORT.value:
                    inv_px = max(f.get("latest_high", close_px + safe_buffer), close_px + safe_buffer)
                else:
                    # Neutral context anomaly: symmetric lower structural boundary (non-zero risk distance)
                    inv_px = close_px - safe_buffer

                disc_sig = ModuleSignal(
                    module_name=self.name,
                    setup_name="broad_ev_discovery",
                    instrument_id=inst_id,
                    direction=s_dir,
                    horizon=context.horizon,
                    trigger_price=close_px,
                    invalidation_price=inv_px,
                    confidence=discovery_hits["confidence"],
                    conditions=discovery_hits,
                    feature_snapshot_ids=[snapshot.id],
                    producer=self.name,
                    producer_version=self.version,
                    as_of=snapshot.as_of,
                    available_at=now,
                    quality_status=QualityStatus.VALID.value,
                    expires_at=now + interval_to_timedelta(context.horizon) * 4,
                )
                signals.append(disc_sig)

            # 2. Playbook Named Setups
            detected_setups = self._detect_named_setups(f, context.horizon)
            for setup in detected_setups:
                sig = ModuleSignal(
                    module_name=self.name,
                    setup_name=setup["setup_name"],
                    instrument_id=inst_id,
                    direction=setup["direction"],
                    horizon=context.horizon,
                    trigger_price=setup["trigger_price"],
                    invalidation_price=setup["invalidation_price"],
                    confidence=setup["confidence"],
                    conditions=setup["conditions"],
                    feature_snapshot_ids=[snapshot.id],
                    producer=self.name,
                    producer_version=self.version,
                    as_of=snapshot.as_of,
                    available_at=now,
                    quality_status=QualityStatus.VALID.value,
                    expires_at=now + interval_to_timedelta(context.horizon) * setup.get("ttl_bars", 6),
                )
                signals.append(sig)

        for s in signals:
            session.add(s)
        session.commit()

        return ModuleResult(
            status="success",
            module_name=self.name,
            signals=signals,
            feature_snapshot_ids=feature_ids,
            quality_status=QualityStatus.VALID,
            diagnostics={"instruments_evaluated": len(context.instrument_ids), "signals_count": len(signals)},
        )

    def _check_discovery_anomalies(self, f: dict[str, Any]) -> dict[str, Any] | None:
        """Broad discovery filter looking for statistically anomalous volatility, volume, or momentum."""
        anomalies = []
        confidence = 0.5
        suggested_dir = SignalDirection.NEUTRAL_CONTEXT.value

        # A. Volatility Compression
        if f.get("bb_width_pct_rank", 50) <= 20 or f.get("in_squeeze", False):
            anomalies.append("extreme_volatility_compression")
            confidence += 0.15

        # B. Volume Surge
        if f.get("volume_ratio", 1.0) >= 1.5:
            anomalies.append(f"volume_surge_{f['volume_ratio']:.1f}x")
            confidence += 0.15

        # C. RSI Extremes
        rsi = f.get("rsi_14", 50)
        if rsi <= 30:
            anomalies.append("rsi_oversold")
            suggested_dir = SignalDirection.LONG.value
            confidence += 0.1
        elif rsi >= 70:
            anomalies.append("rsi_overbought")
            suggested_dir = SignalDirection.SHORT.value
            confidence += 0.1

        # D. Structure Testing
        close = f["latest_close"]
        sh = f.get("recent_swing_high")
        sl = f.get("recent_swing_low")
        atr = f.get("atr_14", 1.0)
        if sh and abs(close - sh) <= atr * 0.5:
            anomalies.append("testing_swing_high_resistance")
        elif sl and abs(close - sl) <= atr * 0.5:
            anomalies.append("testing_swing_low_support")

        if anomalies:
            return {
                "anomalies": anomalies,
                "confidence": min(confidence, 0.95),
                "suggested_direction": suggested_dir,
            }
        return None

    def _detect_named_setups(self, f: dict[str, Any], horizon: str) -> list[dict[str, Any]]:
        """Evaluate the 6 specific high-conviction setups."""
        setups: list[dict[str, Any]] = []
        close = f["latest_close"]
        open_ = f["latest_open"]
        high = f["latest_high"]
        low = f["latest_low"]
        atr = f.get("atr_14", 1.0)

        # -------------------------------------------------------------
        # SHORT-TERM BREAKOUT PLAYBOOK SETUPS (15m, 1h)
        # -------------------------------------------------------------

        # Setup 1: Compression -> Breakout
        is_compressed = f.get("bb_width_pct_rank", 50) <= 25 or f.get("in_squeeze", False)
        vol_surge = f.get("volume_ratio", 1.0) >= 1.25

        if is_compressed and vol_surge:
            if close > f.get("bb_upper", close) and close > open_:
                setups.append({
                    "setup_name": "compression_breakout",
                    "direction": SignalDirection.LONG.value,
                    "trigger_price": close,
                    "invalidation_price": f.get("bb_middle", low),
                    "confidence": 0.85,
                    "ttl_bars": 6,
                    "conditions": {"bb_width_pct": f.get("bb_width_pct_rank"), "vol_ratio": f.get("volume_ratio")},
                })
            elif close < f.get("bb_lower", close) and close < open_:
                setups.append({
                    "setup_name": "compression_breakout",
                    "direction": SignalDirection.SHORT.value,
                    "trigger_price": close,
                    "invalidation_price": f.get("bb_middle", high),
                    "confidence": 0.85,
                    "ttl_bars": 6,
                    "conditions": {"bb_width_pct": f.get("bb_width_pct_rank"), "vol_ratio": f.get("volume_ratio")},
                })

        # Setup 2: Range Break
        range_high = f.get("range_10_high")
        range_low = f.get("range_10_low")
        if range_high and range_low and vol_surge:
            if close > range_high:
                setups.append({
                    "setup_name": "range_break",
                    "direction": SignalDirection.LONG.value,
                    "trigger_price": close,
                    "invalidation_price": range_high - (0.5 * atr),
                    "confidence": 0.80,
                    "ttl_bars": 6,
                    "conditions": {"range_high": range_high, "vol_ratio": f.get("volume_ratio")},
                })
            elif close < range_low:
                setups.append({
                    "setup_name": "range_break",
                    "direction": SignalDirection.SHORT.value,
                    "trigger_price": close,
                    "invalidation_price": range_low + (0.5 * atr),
                    "confidence": 0.80,
                    "ttl_bars": 6,
                    "conditions": {"range_low": range_low, "vol_ratio": f.get("volume_ratio")},
                })

        # Setup 3: VWAP Reclaim / Reject
        vwap = f.get("vwap")
        if vwap and open_ and close:
            # Bullish VWAP reclaim
            if open_ <= vwap and close > vwap and f.get("rsi_14", 50) >= 48:
                setups.append({
                    "setup_name": "vwap_reclaim",
                    "direction": SignalDirection.LONG.value,
                    "trigger_price": close,
                    "invalidation_price": vwap - (0.5 * atr),
                    "confidence": 0.75,
                    "ttl_bars": 4,
                    "conditions": {"vwap": vwap, "rsi": f.get("rsi_14")},
                })
            # Bearish VWAP reject
            elif open_ >= vwap and close < vwap and f.get("rsi_14", 50) <= 52:
                setups.append({
                    "setup_name": "vwap_reject",
                    "direction": SignalDirection.SHORT.value,
                    "trigger_price": close,
                    "invalidation_price": vwap + (0.5 * atr),
                    "confidence": 0.75,
                    "ttl_bars": 4,
                    "conditions": {"vwap": vwap, "rsi": f.get("rsi_14")},
                })

        # -------------------------------------------------------------
        # SWING PLAYBOOK SETUPS (4h, 1d)
        # -------------------------------------------------------------

        # Setup 4: Trend Pullback (Structure + EMA zone)
        trend = f.get("structure_trend", "neutral")
        ema21 = f.get("ema_21")
        ema50 = f.get("ema_50")

        if trend == "uptrend_hh_hl" and ema21:
            # Pullback to EMA zone
            if low <= ema21 * 1.01 and close >= ema21 and close > open_:
                setups.append({
                    "setup_name": "trend_pullback",
                    "direction": SignalDirection.LONG.value,
                    "trigger_price": close,
                    "invalidation_price": f.get("recent_swing_low", low - atr),
                    "confidence": 0.85,
                    "ttl_bars": 4,
                    "conditions": {"trend": trend, "ema21": ema21, "swing_low": f.get("recent_swing_low")},
                })
        elif trend == "downtrend_lh_ll" and ema21:
            if high >= ema21 * 0.99 and close <= ema21 and close < open_:
                setups.append({
                    "setup_name": "trend_pullback",
                    "direction": SignalDirection.SHORT.value,
                    "trigger_price": close,
                    "invalidation_price": f.get("recent_swing_high", high + atr),
                    "confidence": 0.85,
                    "ttl_bars": 4,
                    "conditions": {"trend": trend, "ema21": ema21, "swing_high": f.get("recent_swing_high")},
                })

        # Setup 5: Daily Compression Expansion
        if f.get("atr_pct_rank", 50) <= 25 and f.get("in_squeeze", False):
            if close > f.get("bb_upper", close) and close > open_:
                setups.append({
                    "setup_name": "daily_compression_expansion",
                    "direction": SignalDirection.LONG.value,
                    "trigger_price": close,
                    "invalidation_price": f.get("bb_middle", low),
                    "confidence": 0.90,
                    "ttl_bars": 3,
                    "conditions": {"atr_pct": f.get("atr_pct_rank"), "in_squeeze": True},
                })

        # Setup 6: Momentum Divergence
        rsi = f.get("rsi_14", 50)
        swing_low = f.get("recent_swing_low")
        swing_high = f.get("recent_swing_high")

        if swing_low and low < swing_low and rsi > 35 and close > open_:
            setups.append({
                "setup_name": "momentum_divergence",
                "direction": SignalDirection.LONG.value,
                "trigger_price": close,
                "invalidation_price": low - (0.2 * atr),
                "confidence": 0.80,
                "ttl_bars": 3,
                "conditions": {"divergence_type": "bullish", "rsi": rsi, "swing_low": swing_low},
            })
        elif swing_high and high > swing_high and rsi < 65 and close < open_:
            setups.append({
                "setup_name": "momentum_divergence",
                "direction": SignalDirection.SHORT.value,
                "trigger_price": close,
                "invalidation_price": high + (0.2 * atr),
                "confidence": 0.80,
                "ttl_bars": 3,
                "conditions": {"divergence_type": "bearish", "rsi": rsi, "swing_high": swing_high},
            })

        # -------------------------------------------------------------
        # TRADINGVIEW PRO EXTENSION SETUPS
        # -------------------------------------------------------------

        # Setup 7: LuxAlgo Liquidity Sweep (Bullish & Bearish Trap)
        if f.get("liquidity_sweep_bullish"):
            setups.append({
                "setup_name": "luxalgo_liquidity_sweep",
                "direction": SignalDirection.LONG.value,
                "trigger_price": close,
                "invalidation_price": low - (0.15 * atr),
                "confidence": 0.88,
                "ttl_bars": 4,
                "conditions": {"sweep_type": "bullish_liquidity_grab", "recent_low": swing_low, "vol_ratio": f.get("volume_ratio")},
            })
        elif f.get("liquidity_sweep_bearish"):
            setups.append({
                "setup_name": "luxalgo_liquidity_sweep",
                "direction": SignalDirection.SHORT.value,
                "trigger_price": close,
                "invalidation_price": high + (0.15 * atr),
                "confidence": 0.88,
                "ttl_bars": 4,
                "conditions": {"sweep_type": "bearish_liquidity_grab", "recent_high": swing_high, "vol_ratio": f.get("volume_ratio")},
            })

        # Setup 8: ChrisMoody Squeeze Pro Expansion
        sq_pro = f.get("squeeze_pro_level", "no_squeeze")
        sq_slope = f.get("squeeze_momentum_slope", 0.0)
        if sq_pro in ("high_squeeze", "mid_squeeze") and sq_slope > 0 and close > open_:
            setups.append({
                "setup_name": "squeeze_pro_expansion",
                "direction": SignalDirection.LONG.value,
                "trigger_price": close,
                "invalidation_price": f.get("bb_middle", low),
                "confidence": 0.87,
                "ttl_bars": 4,
                "conditions": {"squeeze_level": sq_pro, "momentum_slope": sq_slope},
            })

        # Setup 9: Ripster EMA Cloud Pullback
        if f.get("cloud_pullback_active") and close > open_:
            setups.append({
                "setup_name": "ripster_cloud_pullback",
                "direction": SignalDirection.LONG.value,
                "trigger_price": close,
                "invalidation_price": (f.get("ema_50") or low) - (0.3 * atr),
                "confidence": 0.84,
                "ttl_bars": 4,
                "conditions": {"short_cloud": f.get("short_cloud_bullish"), "long_cloud": f.get("long_cloud_bullish")},
            })

        # Setup 10: Volume Profile Value Area Breakout
        vp_pos = f.get("volume_profile_position")
        if vp_pos == "above_vah" and f.get("volume_ratio", 1.0) >= 1.3:
            setups.append({
                "setup_name": "volume_profile_breakout",
                "direction": SignalDirection.LONG.value,
                "trigger_price": close,
                "invalidation_price": f.get("poc_price", low),
                "confidence": 0.82,
                "ttl_bars": 5,
                "conditions": {"position": "above_vah", "poc": f.get("poc_price"), "vah": f.get("vah_price")},
            })

        # Setup 11: Lorentzian Machine Learning Classification
        ml_bias = f.get("lorentzian_bias")
        ml_conf = f.get("lorentzian_confidence", 0.5)
        if ml_bias == "bullish" and ml_conf >= 0.70:
            setups.append({
                "setup_name": "lorentzian_ml_prediction",
                "direction": SignalDirection.LONG.value,
                "trigger_price": close,
                "invalidation_price": low - (0.25 * atr),
                "confidence": ml_conf,
                "ttl_bars": 4,
                "conditions": {"knn_k": 7, "lorentzian_conf": ml_conf, "bias": "bullish"},
            })

        # Setup 12: ICT Fair Value Gap Retest Continuation (IFVG)
        if f.get("fvg_bullish_test"):
            fvg_bot = f.get("fvg_bullish_bottom", low)
            setups.append({
                "setup_name": "fvg_retest_continuation",
                "direction": SignalDirection.LONG.value,
                "trigger_price": close,
                "invalidation_price": fvg_bot - (0.15 * atr),
                "confidence": 0.86,
                "ttl_bars": 4,
                "conditions": {
                    "fvg_type": "bullish_bisi_retest",
                    "fvg_top": f.get("fvg_bullish_top"),
                    "fvg_bottom": fvg_bot,
                    "fvg_ce": f.get("fvg_bullish_ce"),
                },
            })
        elif f.get("fvg_bearish_test"):
            fvg_top = f.get("fvg_bearish_top", high)
            setups.append({
                "setup_name": "fvg_retest_continuation",
                "direction": SignalDirection.SHORT.value,
                "trigger_price": close,
                "invalidation_price": fvg_top + (0.15 * atr),
                "confidence": 0.86,
                "ttl_bars": 4,
                "conditions": {
                    "fvg_type": "bearish_sibi_retest",
                    "fvg_top": fvg_top,
                    "fvg_bottom": f.get("fvg_bearish_bottom"),
                    "fvg_ce": f.get("fvg_bearish_ce"),
                },
            })

        # Setup 13: Trader Mayne Dealing Range SFP & Equilibrium Reclaim (DREF)
        if f.get("mayne_sfp_bullish"):
            inval = f.get("mayne_sfp_invalidation") or (low - 0.15 * atr)
            r_high = f.get("dealing_range_high", high)
            r_low = f.get("dealing_range_low", low)
            eq = f.get("dealing_range_eq", (r_high + r_low) / 2.0)
            setups.append({
                "setup_name": "dealing_range_sfp_reclaim",
                "direction": SignalDirection.LONG.value,
                "trigger_price": close,
                "invalidation_price": inval,
                "confidence": 0.89,
                "ttl_bars": 6,
                "conditions": {
                    "sfp_type": "bullish_range_low_reclaim",
                    "range_low": r_low,
                    "range_high": r_high,
                    "eq_target": eq,
                    "terminal_target": r_high,
                    "vol_ratio": f.get("volume_ratio"),
                },
            })
        elif f.get("mayne_sfp_bearish"):
            inval = f.get("mayne_sfp_invalidation") or (high + 0.15 * atr)
            r_high = f.get("dealing_range_high", high)
            r_low = f.get("dealing_range_low", low)
            eq = f.get("dealing_range_eq", (r_high + r_low) / 2.0)
            setups.append({
                "setup_name": "dealing_range_sfp_reclaim",
                "direction": SignalDirection.SHORT.value,
                "trigger_price": close,
                "invalidation_price": inval,
                "confidence": 0.89,
                "ttl_bars": 6,
                "conditions": {
                    "sfp_type": "bearish_range_high_reclaim",
                    "range_low": r_low,
                    "range_high": r_high,
                    "eq_target": eq,
                    "terminal_target": r_low,
                    "vol_ratio": f.get("volume_ratio"),
                },
            })

        return setups
