"""Interactive Plotly candlestick chart with indicator overlays and setup levels."""

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from src.core.models import MarketObservation


def create_candlestick_chart(
    observations: list[MarketObservation],
    symbol_label: str,
    trigger_price: float | None = None,
    invalidation_price: float | None = None,
    show_volume: bool = True,
) -> go.Figure:
    """Build a dark-themed interactive chart with price, indicators, and trade levels."""
    if not observations:
        fig = go.Figure()
        fig.update_layout(title="No observation data available")
        return fig

    # Build dataframe
    records = []
    for obs in observations:
        records.append({
            "time": obs.open_time,
            "open": float(obs.open),
            "high": float(obs.high),
            "low": float(obs.low),
            "close": float(obs.close),
            "volume": float(obs.volume),
        })
    df = pd.DataFrame(records).sort_values("time")

    # Rolling indicators for chart display
    df["ema_21"] = df["close"].ewm(span=21, adjust=False).mean()
    df["ema_50"] = df["close"].ewm(span=50, adjust=False).mean()
    df["bb_middle"] = df["close"].rolling(20).mean()
    bb_std = df["close"].rolling(20).std()
    df["bb_upper"] = df["bb_middle"] + 2 * bb_std
    df["bb_lower"] = df["bb_middle"] - 2 * bb_std

    fig = make_subplots(
        rows=2 if show_volume else 1,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.04,
        row_heights=[0.75, 0.25] if show_volume else [1.0],
    )

    # Candlestick
    fig.add_trace(
        go.Candlestick(
            x=df["time"],
            open=df["open"],
            high=df["high"],
            low=df["low"],
            close=df["close"],
            name="OHLC",
            increasing_line_color="#26a69a",
            decreasing_line_color="#ef5350",
        ),
        row=1,
        col=1,
    )

    # EMA overlays
    fig.add_trace(
        go.Scatter(x=df["time"], y=df["ema_21"], line=dict(color="#29b6f6", width=1.2), name="EMA 21"),
        row=1,
        col=1,
    )
    fig.add_trace(
        go.Scatter(x=df["time"], y=df["ema_50"], line=dict(color="#ab47bc", width=1.2), name="EMA 50"),
        row=1,
        col=1,
    )

    # Bollinger Bands
    fig.add_trace(
        go.Scatter(x=df["time"], y=df["bb_upper"], line=dict(color="rgba(180, 180, 180, 0.4)", width=1, dash="dot"), name="BB Upper"),
        row=1,
        col=1,
    )
    fig.add_trace(
        go.Scatter(x=df["time"], y=df["bb_lower"], line=dict(color="rgba(180, 180, 180, 0.4)", width=1, dash="dot"), name="BB Lower"),
        row=1,
        col=1,
    )

    # Trigger Level
    if trigger_price:
        fig.add_hline(
            y=trigger_price,
            line_dash="dash",
            line_color="#00e676",
            line_width=1.5,
            annotation_text=f"Trigger: {trigger_price:.2f}",
            annotation_position="top left",
            row=1,
            col=1,
        )

    # Invalidation Level
    if invalidation_price:
        fig.add_hline(
            y=invalidation_price,
            line_dash="dash",
            line_color="#ff1744",
            line_width=1.5,
            annotation_text=f"Invalidation: {invalidation_price:.2f}",
            annotation_position="bottom left",
            row=1,
            col=1,
        )

    # Volume Sub-plot
    if show_volume:
        colors = ["#26a69a" if c >= o else "#ef5350" for c, o in zip(df["close"], df["open"])]
        fig.add_trace(
            go.Bar(x=df["time"], y=df["volume"], marker_color=colors, name="Volume", opacity=0.8),
            row=2,
            col=1,
        )

    # Layout styling
    fig.update_layout(
        template="plotly_dark",
        title=f"{symbol_label} Chart",
        xaxis_rangeslider_visible=False,
        height=550,
        margin=dict(l=40, r=40, t=50, b=40),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )

    return fig
