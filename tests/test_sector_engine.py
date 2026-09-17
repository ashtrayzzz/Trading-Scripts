"""Unit tests for SectorEngine, sector classification, and deviation tracking."""

from src.intelligence.sector_engine import SectorEngine, get_instrument_sector


def test_sector_classification():
    """Verify sector mappings for equities, indices, and crypto perpetuals."""
    assert get_instrument_sector("AAPL") == "Technology"
    assert get_instrument_sector("NVDA") == "Technology"
    assert get_instrument_sector("TSLA") == "Consumer Discretionary"
    assert get_instrument_sector("SPY") == "Broad Market Index"
    assert get_instrument_sector("QQQ") == "Tech Index (Nasdaq 100)"
    assert get_instrument_sector("SHOP.TO") == "Technology"
    assert get_instrument_sector("XIU.TO") == "Canadian TSX 60"
    assert get_instrument_sector("bybit:BTCUSDT:perpetual") == "Crypto Store of Value"
    assert get_instrument_sector("bybit:SOLUSDT:perpetual") == "Crypto Smart Contracts (L1)"
    assert get_instrument_sector("bybit:DOGEUSDT:perpetual") == "Crypto Memecoins & Beta"
    assert get_instrument_sector("bybit:LINKUSDT:perpetual") == "Crypto Oracles & Infra"


def test_sector_engine_deviations_and_alerts():
    """Verify sector performance evaluation, deviation Z-scores, and abnormal alerts."""
    engine = SectorEngine()
    result = engine.evaluate_sector_performance()

    assert "sectors" in result
    assert len(result["sectors"]) >= 9

    # Verify sector row schema
    first_sec = result["sectors"][0]
    assert "ticker" in first_sec
    assert "sector" in first_sec
    assert "rel_perf_1d" in first_sec
    assert "z_score" in first_sec
    assert "status" in first_sec

    # Verify dispersion and alerts
    assert "dispersion_range" in result
    assert result["dispersion_range"] >= 0.0
    assert "abnormal_alerts" in result
    assert isinstance(result["abnormal_alerts"], list)


def test_stock_sector_divergence():
    """Verify stock-to-sector relative alpha divergence computation."""
    engine = SectorEngine()
    divergence = engine.evaluate_stock_sector_divergence(["AAPL", "NVDA", "TSLA"])

    assert len(divergence) >= 3
    for d in divergence:
        assert "stock" in d
        assert "sector" in d
        assert "sector_etf" in d
        assert "stock_ret_1d" in d
        assert "sector_etf_ret_1d" in d
        assert "alpha_divergence" in d
        assert "signal" in d

