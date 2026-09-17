"""Deterministic feature calculation pipeline (crypto + equity agnostic)."""

from datetime import datetime
from typing import Any
import numpy as np
import pandas as pd
import ta
import structlog

from src.core.enums import QualityStatus
from src.core.models import FeatureSnapshot, MarketObservation
from src.core.time import utc_now

logger = structlog.get_logger()
FEATURE_VERSION = "0.1.0"


def observations_to_dataframe(observations: list[MarketObservation]) -> pd.DataFrame:
    """Convert a sequence of MarketObservations into a standardized DataFrame sorted by time."""
    if not observations:
        return pd.DataFrame()

    data = []
    for obs in observations:
        data.append({
            "id": obs.id,
            "open_time": obs.open_time,
            "close_time": obs.close_time,
            "open": float(obs.open),
            "high": float(obs.high),
            "low": float(obs.low),
            "close": float(obs.close),
            "volume": float(obs.volume),
        })

    df = pd.DataFrame(data)
    df.sort_values(by="open_time", inplace=True)
    df.reset_index(drop=True, inplace=True)
    return df


def detect_swing_structure(df: pd.DataFrame, window: int = 3) -> dict[str, Any]:
    """
    Detect swing highs and swing lows to classify market structure (HH/HL vs LH/LL).
    """
    if len(df) < window * 2 + 1:
        return {"trend": "neutral", "recent_swing_high": None, "recent_swing_low": None}

    highs = df["high"].values
    lows = df["low"].values
    n = len(df)

    swing_highs = []
    swing_lows = []

    for i in range(window, n - window):
        # Local peak
        if highs[i] == max(highs[i - window : i + window + 1]):
            swing_highs.append((i, highs[i]))
        # Local trough
        if lows[i] == min(lows[i - window : i + window + 1]):
            swing_lows.append((i, lows[i]))

    recent_sh = swing_highs[-1][1] if swing_highs else float(highs[-1])
    recent_sl = swing_lows[-1][1] if swing_lows else float(lows[-1])

    trend = "neutral"
    if len(swing_highs) >= 2 and len(swing_lows) >= 2:
        sh1, sh2 = swing_highs[-2][1], swing_highs[-1][1]
        sl1, sl2 = swing_lows[-2][1], swing_lows[-1][1]

        if sh2 > sh1 and sl2 > sl1:
            trend = "uptrend_hh_hl"
        elif sh2 < sh1 and sl2 < sl1:
            trend = "downtrend_lh_ll"

    return {
        "trend": trend,
        "recent_swing_high": float(recent_sh),
        "recent_swing_low": float(recent_sl),
        "swing_highs_count": len(swing_highs),
        "swing_lows_count": len(swing_lows),
    }


def detect_fair_value_gaps(df: pd.DataFrame, atr: float) -> dict[str, Any]:
    """
    Detect 3-candle Fair Value Gaps (FVG / ICT Imbalances) and retest reactions.
    
    A 3-candle sequence [i-2, i-1, i]:
    - Bullish FVG (BISI): low[i] > high[i-2].
      Zone = [fvg_low, fvg_high] where fvg_low = high[i-2], fvg_high = low[i].
      Consequent Encroachment (50% CE) = (fvg_low + fvg_high) / 2.0.
      Unmitigated if no subsequent bar closed below fvg_low.
      Test reaction in latest bar: latest_low enters zone and latest_close holds above fvg_low, closing bullish.
    - Bearish FVG (SIBI): high[i] < low[i-2].
      Zone = [fvg_low, fvg_high] where fvg_low = high[i], fvg_high = low[i-2].
      Consequent Encroachment (50% CE) = (fvg_low + fvg_high) / 2.0.
      Unmitigated if no subsequent bar closed above fvg_high.
      Test reaction in latest bar: latest_high enters zone and latest_close holds below fvg_high, closing bearish.
    """
    default_result: dict[str, Any] = {
        "fvg_bullish_test": False,
        "fvg_bearish_test": False,
        "fvg_bullish_top": None,
        "fvg_bullish_bottom": None,
        "fvg_bullish_ce": None,
        "fvg_bearish_top": None,
        "fvg_bearish_bottom": None,
        "fvg_bearish_ce": None,
        "active_bullish_fvgs_count": 0,
        "active_bearish_fvgs_count": 0,
    }
    if len(df) < 4:
        return default_result

    n = len(df)
    latest_idx = n - 1
    latest_open = float(df["open"].iloc[latest_idx])
    latest_high = float(df["high"].iloc[latest_idx])
    latest_low = float(df["low"].iloc[latest_idx])
    latest_close = float(df["close"].iloc[latest_idx])

    # Scan up to past 25 bars for active FVGs formed before the latest bar
    scan_start = max(2, n - 25)

    active_bullish: list[dict[str, float]] = []
    active_bearish: list[dict[str, float]] = []

    for i in range(scan_start, latest_idx):
        # 3-candle window: [i-2, i-1, i]
        bar_prev2_high = float(df["high"].iloc[i - 2])
        bar_prev2_low = float(df["low"].iloc[i - 2])
        bar_curr_high = float(df["high"].iloc[i])
        bar_curr_low = float(df["low"].iloc[i])

        # 1. Bullish FVG (BISI)
        if bar_curr_low > bar_prev2_high:
            fvg_bot = bar_prev2_high
            fvg_top = bar_curr_low
            invalidated = False
            for k in range(i + 1, latest_idx):
                if float(df["close"].iloc[k]) < fvg_bot:
                    invalidated = True
                    break
            if not invalidated:
                active_bullish.append({
                    "bottom": fvg_bot,
                    "top": fvg_top,
                    "ce": (fvg_bot + fvg_top) / 2.0,
                    "bar_index": float(i),
                })

        # 2. Bearish FVG (SIBI)
        if bar_curr_high < bar_prev2_low:
            fvg_bot = bar_curr_high
            fvg_top = bar_prev2_low
            invalidated = False
            for k in range(i + 1, latest_idx):
                if float(df["close"].iloc[k]) > fvg_top:
                    invalidated = True
                    break
            if not invalidated:
                active_bearish.append({
                    "bottom": fvg_bot,
                    "top": fvg_top,
                    "ce": (fvg_bot + fvg_top) / 2.0,
                    "bar_index": float(i),
                })

    result = dict(default_result)
    result["active_bullish_fvgs_count"] = len(active_bullish)
    result["active_bearish_fvgs_count"] = len(active_bearish)

    # Check for test/rebound on latest candle from most recent unmitigated bullish FVG
    if active_bullish:
        most_recent_bull = active_bullish[-1]
        result["fvg_bullish_bottom"] = float(most_recent_bull["bottom"])
        result["fvg_bullish_top"] = float(most_recent_bull["top"])
        result["fvg_bullish_ce"] = float(most_recent_bull["ce"])

        b_bot = most_recent_bull["bottom"]
        b_top = most_recent_bull["top"]

        # Latest low enters the gap (at or below top of gap) and holds above bottom
        if latest_low <= b_top and latest_close >= b_bot and latest_close > latest_open:
            result["fvg_bullish_test"] = True

    # Check for test/rejection on latest candle from most recent unmitigated bearish FVG
    if active_bearish:
        most_recent_bear = active_bearish[-1]
        result["fvg_bearish_bottom"] = float(most_recent_bear["bottom"])
        result["fvg_bearish_top"] = float(most_recent_bear["top"])
        result["fvg_bearish_ce"] = float(most_recent_bear["ce"])

        be_bot = most_recent_bear["bottom"]
        be_top = most_recent_bear["top"]

        # Latest high enters the gap (at or above bottom of gap) and holds below top
        if latest_high >= be_bot and latest_close <= be_top and latest_close < latest_open:
            result["fvg_bearish_test"] = True

    return result


def detect_dealing_range_sfp(
    df: pd.DataFrame,
    volume_ratio: float,
    atr: float,
    range_lookback: int = 25,
) -> dict[str, Any]:
    """
    Detect Trader Mayne Dealing Range, 50% Equilibrium (EQ), and Swing Failure Pattern (SFP) reclaims.
    
    Range defined over the past `range_lookback` closed bars (excluding the latest bar):
    - R_H = max(high[-lookback:-1])
    - R_L = min(low[-lookback:-1])
    - EQ = (R_H + R_L) / 2.0
    
    Bullish SFP Reclaim on latest bar:
    - low < R_L (liquidity stop-sweep of range low)
    - close > R_L (body reclaim back inside range)
    - close > open (bullish momentum)
    - volume expansion (volume_ratio >= 1.10)
    
    Bearish SFP Reclaim on latest bar:
    - high > R_H (liquidity stop-sweep of range high)
    - close < R_H (body reclaim back inside range)
    - close < open (bearish momentum)
    - volume expansion (volume_ratio >= 1.10)
    """
    if len(df) < 10:
        latest_close = float(df["close"].iloc[-1]) if len(df) > 0 else 0.0
        return {
            "dealing_range_high": latest_close * 1.02,
            "dealing_range_low": latest_close * 0.98,
            "dealing_range_eq": latest_close,
            "mayne_sfp_bullish": False,
            "mayne_sfp_bearish": False,
            "mayne_sfp_invalidation": None,
            "mayne_sfp_target_eq": None,
            "mayne_sfp_target_terminal": None,
        }

    lookback = min(len(df) - 1, range_lookback)
    prior_bars = df.iloc[-(lookback + 1):-1]

    r_high = float(prior_bars["high"].max())
    r_low = float(prior_bars["low"].min())
    eq = (r_high + r_low) / 2.0

    latest_open = float(df["open"].iloc[-1])
    latest_high = float(df["high"].iloc[-1])
    latest_low = float(df["low"].iloc[-1])
    latest_close = float(df["close"].iloc[-1])

    safe_atr = atr if atr > 0 else (latest_close * 0.01)

    sfp_bullish = False
    sfp_bearish = False
    invalidation = None
    target_eq = float(eq)
    target_term = None

    if (
        latest_low < r_low
        and latest_close > r_low
        and latest_close > latest_open
        and volume_ratio >= 1.10
    ):
        sfp_bullish = True
        invalidation = float(latest_low - 0.1 * safe_atr)
        target_term = float(r_high)

    elif (
        latest_high > r_high
        and latest_close < r_high
        and latest_close < latest_open
        and volume_ratio >= 1.10
    ):
        sfp_bearish = True
        invalidation = float(latest_high + 0.1 * safe_atr)
        target_term = float(r_low)

    return {
        "dealing_range_high": float(r_high),
        "dealing_range_low": float(r_low),
        "dealing_range_eq": float(eq),
        "mayne_sfp_bullish": sfp_bullish,
        "mayne_sfp_bearish": sfp_bearish,
        "mayne_sfp_invalidation": invalidation,
        "mayne_sfp_target_eq": target_eq,
        "mayne_sfp_target_terminal": target_term,
    }


def compute_features_for_bars(
    instrument_id: str,
    horizon: str,
    observations: list[MarketObservation],
    as_of: datetime | None = None,
) -> FeatureSnapshot | None:
    """
    Compute comprehensive features deterministically for a series of closed bars.
    
    Minimum 25 bars required for core indicators.
    """
    if len(observations) < 20:
        logger.warning("Insufficient bars for feature computation", instrument_id=instrument_id, count=len(observations))
        return None

    df = observations_to_dataframe(observations)
    now = utc_now()
    cutoff = as_of or now

    # Core indicators
    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"]

    # 1. ATR (14)
    atr_series = ta.volatility.average_true_range(high=high, low=low, close=close, window=14)
    atr_val = float(atr_series.iloc[-1]) if not pd.isna(atr_series.iloc[-1]) else 0.0

    # ATR Percentile rank (over available bars)
    clean_atr = atr_series.dropna()
    if len(clean_atr) > 5:
        atr_pct_rank = float((clean_atr < atr_val).mean() * 100.0)
    else:
        atr_pct_rank = 50.0

    # 2. Bollinger Bands (20, 2)
    bb = ta.volatility.BollingerBands(close=close, window=20, window_dev=2)
    bb_upper = float(bb.bollinger_hband().iloc[-1])
    bb_middle = float(bb.bollinger_mavg().iloc[-1])
    bb_lower = float(bb.bollinger_lband().iloc[-1])
    bb_w_series = bb.bollinger_wband()
    bb_width = float(bb_w_series.iloc[-1]) if not pd.isna(bb_w_series.iloc[-1]) else 0.0

    # BB Width Percentile rank (compression detection)
    clean_bb_w = bb_w_series.dropna()
    if len(clean_bb_w) > 5:
        bb_width_pct_rank = float((clean_bb_w < bb_width).mean() * 100.0)
    else:
        bb_width_pct_rank = 50.0

    # 3. Keltner Channels (20, 1.5)
    kc = ta.volatility.KeltnerChannel(high=high, low=low, close=close, window=20, window_atr=14, original_version=False)
    kc_upper = float(kc.keltner_channel_hband().iloc[-1])
    kc_lower = float(kc.keltner_channel_lband().iloc[-1])

    # Squeeze: Bollinger Bands inside Keltner Channel
    is_in_squeeze = bool(bb_upper < kc_upper and bb_lower > kc_lower)

    # 4. RSI (14)
    rsi_series = ta.momentum.rsi(close=close, window=14)
    rsi_val = float(rsi_series.iloc[-1]) if not pd.isna(rsi_series.iloc[-1]) else 50.0

    # 5. EMAs (21, 50, 200)
    ema_21_s = ta.trend.ema_indicator(close=close, window=21)
    ema_21 = float(ema_21_s.iloc[-1]) if not pd.isna(ema_21_s.iloc[-1]) else float(close.iloc[-1])

    ema_50_s = ta.trend.ema_indicator(close=close, window=50) if len(df) >= 50 else pd.Series([np.nan] * len(df))
    ema_50 = float(ema_50_s.iloc[-1]) if not pd.isna(ema_50_s.iloc[-1]) else None

    ema_200_s = ta.trend.ema_indicator(close=close, window=200) if len(df) >= 200 else pd.Series([np.nan] * len(df))
    ema_200 = float(ema_200_s.iloc[-1]) if not pd.isna(ema_200_s.iloc[-1]) else None

    # 6. Volume metrics
    vol_sma_20 = float(volume.rolling(window=20).mean().iloc[-1]) if len(df) >= 20 else float(volume.mean())
    current_vol = float(volume.iloc[-1])
    volume_ratio = float(current_vol / vol_sma_20) if vol_sma_20 > 0 else 1.0

    # 7. On-Balance Volume (OBV)
    obv_series = ta.volume.on_balance_volume(close=close, volume=volume)
    obv_val = float(obv_series.iloc[-1]) if not pd.isna(obv_series.iloc[-1]) else 0.0
    obv_slope = float(obv_series.diff(5).iloc[-1]) if len(df) >= 6 else 0.0

    # 8. VWAP (approximation based on available bars)
    typical_price = (high + low + close) / 3.0
    cum_pv = (typical_price * volume).cumsum()
    cum_v = volume.cumsum()
    vwap_s = cum_pv / cum_v
    vwap_val = float(vwap_s.iloc[-1]) if not pd.isna(vwap_s.iloc[-1]) else float(close.iloc[-1])

    # 9. Structure & Range
    structure = detect_swing_structure(df)

    # 10-bar range detection
    last_10_high = float(high.iloc[-10:].max()) if len(df) >= 10 else float(high.max())
    last_10_low = float(low.iloc[-10:].min()) if len(df) >= 10 else float(low.min())
    range_10_height = last_10_high - last_10_low

    latest_close = float(close.iloc[-1])
    latest_open = float(df["open"].iloc[-1])
    latest_high = float(high.iloc[-1])
    latest_low = float(low.iloc[-1])

    # -------------------------------------------------------------
    # 10. TRADINGVIEW COMMUNITY INDICATORS (Pro Extensions)
    # -------------------------------------------------------------

    # A. ChrisMoody Squeeze Pro (High, Mid, Low Squeeze)
    kc_10 = ta.volatility.KeltnerChannel(high=high, low=low, close=close, window=20, window_atr=10, original_version=False)
    kc_15 = ta.volatility.KeltnerChannel(high=high, low=low, close=close, window=20, window_atr=14, original_version=False)
    kc_20 = ta.volatility.KeltnerChannel(high=high, low=low, close=close, window=20, window_atr=20, original_version=False)

    kc_10_u, kc_10_l = float(kc_10.keltner_channel_hband().iloc[-1]), float(kc_10.keltner_channel_lband().iloc[-1])
    kc_15_u, kc_15_l = float(kc_15.keltner_channel_hband().iloc[-1]), float(kc_15.keltner_channel_lband().iloc[-1])
    kc_20_u, kc_20_l = float(kc_20.keltner_channel_hband().iloc[-1]), float(kc_20.keltner_channel_lband().iloc[-1])

    squeeze_pro_level = "no_squeeze"
    if bb_upper < kc_10_u and bb_lower > kc_10_l:
        squeeze_pro_level = "high_squeeze"  # Ultra compressed
    elif bb_upper < kc_15_u and bb_lower > kc_15_l:
        squeeze_pro_level = "mid_squeeze"   # Standard squeeze
    elif bb_upper < kc_20_u and bb_lower > kc_20_l:
        squeeze_pro_level = "low_squeeze"   # Mild compression

    # Squeeze momentum direction
    mom_delta = close - ((kc_15_u + kc_15_l) / 2.0 + bb_middle) / 2.0
    squeeze_momentum = float(mom_delta.iloc[-1]) if not pd.isna(mom_delta.iloc[-1]) else 0.0
    squeeze_momentum_slope = float(mom_delta.diff(2).iloc[-1]) if len(df) >= 3 else 0.0

    # B. LuxAlgo Style Liquidity Sweeps & Order Blocks
    recent_sh = structure.get("recent_swing_high")
    recent_sl = structure.get("recent_swing_low")
    liquidity_sweep_bullish = False
    liquidity_sweep_bearish = False

    if recent_sl and len(df) >= 5:
        # Wicked below recent swing low but closed back above it with volume surge
        if latest_low < recent_sl and latest_close > recent_sl and latest_close > latest_open and volume_ratio >= 1.2:
            liquidity_sweep_bullish = True

    if recent_sh and len(df) >= 5:
        # Wicked above recent swing high but closed back below it
        if latest_high > recent_sh and latest_close < recent_sh and latest_close < latest_open and volume_ratio >= 1.2:
            liquidity_sweep_bearish = True

    # Order block zones (last opposite candle before recent aggressive push)
    order_block_high = float(high.iloc[-3:].max())
    order_block_low = float(low.iloc[-3:].min())

    # C. Fixed Range Volume Profile (POC, VAH, VAL over last 40 bars)
    vp_window = min(len(df), 40)
    vp_df = df.iloc[-vp_window:]
    min_p, max_p = float(vp_df["low"].min()), float(vp_df["high"].max())

    if max_p > min_p and len(vp_df) >= 10:
        bin_counts = 20
        bin_edges = np.linspace(min_p, max_p, bin_counts + 1)
        bin_mids = (bin_edges[:-1] + bin_edges[1:]) / 2.0
        bin_vols = np.zeros(bin_counts)

        for _, row in vp_df.iterrows():
            idx = int(np.clip(np.digitize(row["close"], bin_edges) - 1, 0, bin_counts - 1))
            bin_vols[idx] += row["volume"]

        poc_idx = int(np.argmax(bin_vols))
        poc_price = float(bin_mids[poc_idx])

        # Value Area (70% total volume around POC)
        total_vol = bin_vols.sum()
        target_vol = total_vol * 0.70
        curr_vol = bin_vols[poc_idx]
        up_i, dn_i = poc_idx, poc_idx

        while curr_vol < target_vol and (up_i < bin_counts - 1 or dn_i > 0):
            up_v = bin_vols[up_i + 1] if up_i < bin_counts - 1 else 0
            dn_v = bin_vols[dn_i - 1] if dn_i > 0 else 0
            if up_v >= dn_v and up_i < bin_counts - 1:
                up_i += 1
                curr_vol += up_v
            elif dn_i > 0:
                dn_i -= 1
                curr_vol += dn_v
            else:
                break

        vah_price = float(bin_edges[up_i + 1])
        val_price = float(bin_edges[dn_i])
    else:
        poc_price = latest_close
        vah_price = latest_close * 1.01
        val_price = latest_close * 0.99

    vp_position = "inside_value"
    if latest_close > vah_price:
        vp_position = "above_vah"  # Bullish breakout zone
    elif latest_close < val_price:
        vp_position = "below_val"  # Discount / Breakdown zone

    # D. Ripster EMA Clouds (5-12 Short Cloud, 34-50 Long Cloud)
    ema_5_s = ta.trend.ema_indicator(close=close, window=5)
    ema_12_s = ta.trend.ema_indicator(close=close, window=12)
    ema_34_s = ta.trend.ema_indicator(close=close, window=34) if len(df) >= 34 else ema_21_s

    ema_5 = float(ema_5_s.iloc[-1]) if not pd.isna(ema_5_s.iloc[-1]) else latest_close
    ema_12 = float(ema_12_s.iloc[-1]) if not pd.isna(ema_12_s.iloc[-1]) else latest_close
    ema_34 = float(ema_34_s.iloc[-1]) if not pd.isna(ema_34_s.iloc[-1]) else latest_close

    short_cloud_bullish = bool(ema_5 > ema_12)
    long_cloud_bullish = bool(ema_34 > (ema_50 or ema_34))
    clouds_aligned_bullish = bool(short_cloud_bullish and long_cloud_bullish)
    clouds_aligned_bearish = bool((not short_cloud_bullish) and (not long_cloud_bullish))
    cloud_pullback_active = bool(long_cloud_bullish and latest_low <= ema_34 and latest_close >= (ema_50 or ema_34))

    # E. Anchored VWAP (AVWAP from recent swing high & swing low)
    # Re-anchor from recent inflection point
    sh_idx = structure.get("swing_highs_count", 0)
    avwap_from_low = vwap_val
    avwap_from_high = vwap_val
    if len(df) >= 15:
        # Approximate anchored window
        low_idx = int(df["low"].iloc[-25:].idxmin())
        high_idx = int(df["high"].iloc[-25:].idxmax())
        
        slice_l = df.loc[low_idx:]
        avwap_from_low = float(((slice_l["high"] + slice_l["low"] + slice_l["close"]) / 3.0 * slice_l["volume"]).sum() / slice_l["volume"].sum())
        
        slice_h = df.loc[high_idx:]
        avwap_from_high = float(((slice_h["high"] + slice_h["low"] + slice_h["close"]) / 3.0 * slice_h["volume"]).sum() / slice_h["volume"].sum())

    # F. Lorentzian Distance Classifier (KNN Machine Learning)
    lorentzian_bias = "neutral"
    lorentzian_confidence = 0.50

    if len(df) >= 30:
        # Feature 1: RSI (0 - 1)
        f_rsi = rsi_series / 100.0
        # Feature 2: Normalized Volume (0 - 1)
        vol_norm = (volume / (vol_sma_20 + 1e-6)).clip(0.0, 3.0) / 3.0
        # Feature 3: ATR % of price (0 - 1)
        f_atrp = (atr_series / close).clip(0.0, 0.1) * 10.0

        current_vec = np.array([f_rsi.iloc[-1], vol_norm.iloc[-1], f_atrp.iloc[-1]])

        # Calculate Lorentzian distance: d = sum(ln(1 + |u - v|))
        history_len = min(len(df) - 5, 25)
        distances = []
        for h_i in range(len(df) - 5 - history_len, len(df) - 5):
            h_vec = np.array([f_rsi.iloc[h_i], vol_norm.iloc[h_i], f_atrp.iloc[h_i]])
            d = np.sum(np.log(1.0 + np.abs(current_vec - h_vec)))
            # Subsequent 4-bar return
            future_ret = close.iloc[h_i + 4] - close.iloc[h_i]
            distances.append((d, future_ret))

        distances.sort(key=lambda x: x[0])
        k = 7
        top_k = distances[:k]
        bulls = sum(1 for _, ret in top_k if ret > 0)
        bears = sum(1 for _, ret in top_k if ret < 0)

        if bulls >= 5:
            lorentzian_bias = "bullish"
            lorentzian_confidence = round(bulls / k, 2)
        elif bears >= 5:
            lorentzian_bias = "bearish"
            lorentzian_confidence = round(bears / k, 2)

    # G. ICT Fair Value Gaps (BISI / SIBI)
    fvg_features = detect_fair_value_gaps(df, atr=atr_val)

    # H. Trader Mayne Dealing Range & SFP Reclaim
    mayne_features = detect_dealing_range_sfp(df, volume_ratio=volume_ratio, atr=atr_val)

    values: dict[str, Any] = {
        "latest_close": latest_close,
        "latest_open": latest_open,
        "latest_high": latest_high,
        "latest_low": latest_low,
        "latest_volume": current_vol,
        "atr_14": atr_val,
        "atr_pct_rank": atr_pct_rank,
        "bb_upper": bb_upper,
        "bb_middle": bb_middle,
        "bb_lower": bb_lower,
        "bb_width": bb_width,
        "bb_width_pct_rank": bb_width_pct_rank,
        "keltner_upper": kc_15_u,
        "keltner_lower": kc_15_l,
        "in_squeeze": is_in_squeeze,
        "squeeze_pro_level": squeeze_pro_level,
        "squeeze_momentum": squeeze_momentum,
        "squeeze_momentum_slope": squeeze_momentum_slope,
        "rsi_14": rsi_val,
        "ema_5": ema_5,
        "ema_12": ema_12,
        "ema_21": ema_21,
        "ema_34": ema_34,
        "ema_50": ema_50,
        "ema_200": ema_200,
        "short_cloud_bullish": short_cloud_bullish,
        "long_cloud_bullish": long_cloud_bullish,
        "clouds_aligned_bullish": clouds_aligned_bullish,
        "clouds_aligned_bearish": clouds_aligned_bearish,
        "cloud_pullback_active": cloud_pullback_active,
        "volume_sma_20": vol_sma_20,
        "volume_ratio": volume_ratio,
        "obv": obv_val,
        "obv_slope": obv_slope,
        "vwap": vwap_val,
        "structure_trend": structure["trend"],
        "recent_swing_high": structure["recent_swing_high"],
        "recent_swing_low": structure["recent_swing_low"],
        "range_10_high": last_10_high,
        "range_10_low": last_10_low,
        "range_10_height": range_10_height,
        "liquidity_sweep_bullish": liquidity_sweep_bullish,
        "liquidity_sweep_bearish": liquidity_sweep_bearish,
        "order_block_high": order_block_high,
        "order_block_low": order_block_low,
        "poc_price": poc_price,
        "vah_price": vah_price,
        "val_price": val_price,
        "volume_profile_position": vp_position,
        "avwap_from_low": avwap_from_low,
        "avwap_from_high": avwap_from_high,
        "lorentzian_bias": lorentzian_bias,
        "lorentzian_confidence": lorentzian_confidence,
        "bars_count": len(df),
    }
    values.update(fvg_features)
    values.update(mayne_features)

    source_ids = [str(obs.id) for obs in observations[-50:]]

    snapshot = FeatureSnapshot(
        instrument_id=instrument_id,
        horizon=horizon,
        feature_version="0.2.0",
        feature_values=values,
        source_bar_ids=source_ids,
        producer="feature_pipeline",
        producer_version="0.2.0",
        as_of=observations[-1].close_time,
        available_at=now,
        quality_status=QualityStatus.VALID.value,
    )
    return snapshot
