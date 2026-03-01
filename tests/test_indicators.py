"""Tests for src/indicators/technical.py."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.indicators.technical import (
    calculate_bollinger_bands,
    calculate_macd,
    calculate_moving_average,
    calculate_rsi,
    compute_indicators,
)


# ---------------------------------------------------------------------------
# Fixture: minimal OHLCV DataFrame
# ---------------------------------------------------------------------------

@pytest.fixture
def ohlcv_df() -> pd.DataFrame:
    """Return a 60-row DataFrame with synthetic close prices."""
    rng = np.random.default_rng(42)
    close = 50_000_000.0 + np.cumsum(rng.normal(0, 100_000, 60))
    return pd.DataFrame(
        {
            "open": close * 0.999,
            "high": close * 1.001,
            "low": close * 0.998,
            "close": close,
            "volume": rng.uniform(1, 10, 60),
        }
    )


# ---------------------------------------------------------------------------
# calculate_rsi
# ---------------------------------------------------------------------------

class TestCalculateRsi:
    def test_returns_series(self, ohlcv_df):
        result = calculate_rsi(ohlcv_df)
        assert isinstance(result, pd.Series)

    def test_same_length_as_input(self, ohlcv_df):
        result = calculate_rsi(ohlcv_df)
        assert len(result) == len(ohlcv_df)

    def test_values_between_0_and_100(self, ohlcv_df):
        result = calculate_rsi(ohlcv_df)
        valid = result.dropna()
        assert (valid >= 0).all()
        assert (valid <= 100).all()

    def test_series_name_includes_period(self, ohlcv_df):
        result = calculate_rsi(ohlcv_df, period=14)
        assert result.name == "rsi_14"

    def test_custom_period(self, ohlcv_df):
        result = calculate_rsi(ohlcv_df, period=7)
        assert result.name == "rsi_7"

    def test_constant_prices_returns_nan(self):
        """When price never changes, RSI should be NaN (zero loss/gain)."""
        df = pd.DataFrame({"close": [100.0] * 20})
        result = calculate_rsi(df)
        # After constant series, avg_loss=0 → division produces NaN
        assert result.dropna().empty or (result.dropna() == 100.0).all()


# ---------------------------------------------------------------------------
# calculate_macd
# ---------------------------------------------------------------------------

class TestCalculateMacd:
    def test_returns_dataframe(self, ohlcv_df):
        result = calculate_macd(ohlcv_df)
        assert isinstance(result, pd.DataFrame)

    def test_has_required_columns(self, ohlcv_df):
        result = calculate_macd(ohlcv_df)
        assert set(result.columns) == {"macd", "macd_signal", "macd_hist"}

    def test_same_length_as_input(self, ohlcv_df):
        result = calculate_macd(ohlcv_df)
        assert len(result) == len(ohlcv_df)

    def test_histogram_equals_macd_minus_signal(self, ohlcv_df):
        result = calculate_macd(ohlcv_df)
        expected = result["macd"] - result["macd_signal"]
        pd.testing.assert_series_equal(
            result["macd_hist"], expected, check_names=False, atol=1e-9
        )

    def test_custom_parameters(self, ohlcv_df):
        result = calculate_macd(ohlcv_df, fast=5, slow=10, signal=3)
        assert isinstance(result, pd.DataFrame)
        assert "macd" in result.columns


# ---------------------------------------------------------------------------
# calculate_bollinger_bands
# ---------------------------------------------------------------------------

class TestCalculateBollingerBands:
    def test_returns_dataframe(self, ohlcv_df):
        result = calculate_bollinger_bands(ohlcv_df)
        assert isinstance(result, pd.DataFrame)

    def test_has_required_columns(self, ohlcv_df):
        result = calculate_bollinger_bands(ohlcv_df)
        assert set(result.columns) == {"bb_upper", "bb_middle", "bb_lower", "bb_width"}

    def test_upper_above_middle_above_lower(self, ohlcv_df):
        result = calculate_bollinger_bands(ohlcv_df)
        valid = result.dropna()
        assert (valid["bb_upper"] >= valid["bb_middle"]).all()
        assert (valid["bb_middle"] >= valid["bb_lower"]).all()

    def test_same_length_as_input(self, ohlcv_df):
        result = calculate_bollinger_bands(ohlcv_df)
        assert len(result) == len(ohlcv_df)

    def test_first_rows_are_nan_before_period(self, ohlcv_df):
        result = calculate_bollinger_bands(ohlcv_df, period=20)
        # First 19 rows should be NaN (need period rows to compute)
        assert result["bb_middle"].iloc[:19].isna().all()

    def test_custom_std_multiplier(self, ohlcv_df):
        r1 = calculate_bollinger_bands(ohlcv_df, std=1.0)
        r2 = calculate_bollinger_bands(ohlcv_df, std=3.0)
        valid1 = r1.dropna()
        valid2 = r2.dropna()
        # Wider std => wider bands
        assert (valid2["bb_upper"] >= valid1["bb_upper"]).all()
        assert (valid2["bb_lower"] <= valid1["bb_lower"]).all()


# ---------------------------------------------------------------------------
# compute_indicators — name parser
# ---------------------------------------------------------------------------

class TestComputeIndicators:
    def test_rsi_14_parsed_correctly(self, ohlcv_df):
        result = compute_indicators(ohlcv_df, ["rsi_14"])
        assert "rsi_14" in result
        val = result["rsi_14"]
        assert isinstance(val, float)
        assert 0.0 <= val <= 100.0

    def test_bb_20_2_parsed_correctly(self, ohlcv_df):
        result = compute_indicators(ohlcv_df, ["bb_20_2"])
        assert "bb_20_2" in result
        band = result["bb_20_2"]
        assert isinstance(band, dict)
        assert "bb_upper" in band
        assert "bb_middle" in band
        assert "bb_lower" in band
        assert "bb_width" in band

    def test_macd_12_26_9_parsed_correctly(self, ohlcv_df):
        result = compute_indicators(ohlcv_df, ["macd_12_26_9"])
        assert "macd_12_26_9" in result
        m = result["macd_12_26_9"]
        assert isinstance(m, dict)
        assert "macd" in m
        assert "macd_signal" in m
        assert "macd_hist" in m

    def test_sma_20_returns_float(self, ohlcv_df):
        result = compute_indicators(ohlcv_df, ["sma_20"])
        assert "sma_20" in result
        assert isinstance(result["sma_20"], float)

    def test_ema_10_returns_float(self, ohlcv_df):
        result = compute_indicators(ohlcv_df, ["ema_10"])
        assert "ema_10" in result
        assert isinstance(result["ema_10"], float)

    def test_multiple_indicators_computed_together(self, ohlcv_df):
        result = compute_indicators(ohlcv_df, ["rsi_14", "sma_20", "ema_10"])
        assert "rsi_14" in result
        assert "sma_20" in result
        assert "ema_10" in result

    def test_unknown_indicator_is_skipped(self, ohlcv_df):
        """Unknown indicator names are logged as warnings and omitted from results."""
        result = compute_indicators(ohlcv_df, ["unknown_99"])
        # The source warns and skips; the key is not added to the result dict
        assert "unknown_99" not in result

    def test_empty_indicator_list_returns_empty_dict(self, ohlcv_df):
        result = compute_indicators(ohlcv_df, [])
        assert result == {}
