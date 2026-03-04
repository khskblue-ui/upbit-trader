from __future__ import annotations

import logging
import re

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def calculate_rsi(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Compute Relative Strength Index.

    Args:
        df: DataFrame with a ``close`` column.
        period: Lookback window. Default 14.

    Returns:
        pd.Series of RSI values (0-100), same index as ``df``.
    """
    close = df["close"].astype(float)
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return rsi.rename(f"rsi_{period}")


def calculate_macd(
    df: pd.DataFrame,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> pd.DataFrame:
    """Compute MACD line, signal line, and histogram.

    Args:
        df: DataFrame with a ``close`` column.
        fast: Fast EMA period. Default 12.
        slow: Slow EMA period. Default 26.
        signal: Signal EMA period. Default 9.

    Returns:
        DataFrame with columns ``macd``, ``macd_signal``, ``macd_hist``.
    """
    close = df["close"].astype(float)
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line

    return pd.DataFrame(
        {
            "macd": macd_line,
            "macd_signal": signal_line,
            "macd_hist": histogram,
        },
        index=df.index,
    )


def calculate_bollinger_bands(
    df: pd.DataFrame,
    period: int = 20,
    std: float = 2.0,
) -> pd.DataFrame:
    """Compute Bollinger Bands (upper, middle, lower).

    Args:
        df: DataFrame with a ``close`` column.
        period: Rolling window for SMA and std. Default 20.
        std: Number of standard deviations. Default 2.0.

    Returns:
        DataFrame with columns ``bb_upper``, ``bb_middle``, ``bb_lower``, ``bb_width``.
    """
    close = df["close"].astype(float)
    middle = close.rolling(window=period).mean()
    rolling_std = close.rolling(window=period).std(ddof=0)
    upper = middle + std * rolling_std
    lower = middle - std * rolling_std
    width = (upper - lower) / middle.replace(0, np.nan)

    return pd.DataFrame(
        {
            "bb_upper": upper,
            "bb_middle": middle,
            "bb_lower": lower,
            "bb_width": width,
        },
        index=df.index,
    )


def calculate_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Compute Average True Range (ATR).

    Args:
        df: DataFrame with ``high``, ``low``, ``close`` columns.
        period: Lookback window. Default 14.

    Returns:
        pd.Series of ATR values, same index as ``df``.
    """
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close = df["close"].astype(float)
    prev_close = close.shift(1)

    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)

    atr = tr.ewm(span=period, min_periods=period, adjust=False).mean()
    return atr.rename(f"atr_{period}")


def calculate_moving_average(df: pd.DataFrame, period: int = 20) -> pd.Series:
    """Compute Simple Moving Average.

    Args:
        df: DataFrame with a ``close`` column.
        period: Rolling window. Default 20.

    Returns:
        pd.Series of SMA values, same index as ``df``.
    """
    return df["close"].astype(float).rolling(window=period).mean().rename(f"sma_{period}")


# ---------------------------------------------------------------------------
# Indicator name parser & dispatcher
# ---------------------------------------------------------------------------

_INDICATOR_PATTERN = re.compile(
    r"^(?P<name>[a-z]+)(?:_(?P<p1>\d+(?:\.\d+)?))?(?:_(?P<p2>\d+(?:\.\d+)?))?(?:_(?P<p3>\d+(?:\.\d+)?))?$"
)


def _parse_indicator(indicator: str) -> tuple[str, list[float]]:
    """Parse an indicator name like ``rsi_14``, ``bb_20_2``, ``macd_12_26_9``.

    Returns:
        (base_name, [param1, param2, ...])
    """
    m = _INDICATOR_PATTERN.match(indicator.lower().strip())
    if not m:
        raise ValueError(f"Cannot parse indicator name: '{indicator}'")
    base = m.group("name")
    params = [
        float(m.group(k)) for k in ("p1", "p2", "p3") if m.group(k) is not None
    ]
    return base, params


def compute_indicators(df: pd.DataFrame, indicator_list: list[str]) -> dict:
    """Compute a set of indicators from a list of name strings.

    Supported patterns:
        - ``rsi_<period>``                       -> last RSI value (float)
        - ``macd_<fast>_<slow>_<signal>``        -> dict with macd/macd_signal/macd_hist last values
        - ``bb_<period>_<std>``                  -> dict with bb_upper/bb_middle/bb_lower/bb_width last values
        - ``sma_<period>``                        -> last SMA value (float)
        - ``ema_<period>``                        -> last EMA value (float)

    Args:
        df: OHLCV DataFrame with at least a ``close`` column.
        indicator_list: List of indicator name strings to compute.

    Returns:
        Dict mapping indicator name -> scalar or nested dict of last values.
    """
    result: dict = {}

    for indicator in indicator_list:
        try:
            base, params = _parse_indicator(indicator)

            if base == "rsi":
                period = int(params[0]) if params else 14
                series = calculate_rsi(df, period=period)
                result[indicator] = float(series.iloc[-1]) if not series.empty else None

            elif base == "macd":
                fast = int(params[0]) if len(params) > 0 else 12
                slow = int(params[1]) if len(params) > 1 else 26
                sig = int(params[2]) if len(params) > 2 else 9
                frame = calculate_macd(df, fast=fast, slow=slow, signal=sig)
                last = frame.iloc[-1]
                result[indicator] = {
                    "macd": float(last["macd"]),
                    "macd_signal": float(last["macd_signal"]),
                    "macd_hist": float(last["macd_hist"]),
                }

            elif base == "bb":
                period = int(params[0]) if len(params) > 0 else 20
                std_mult = float(params[1]) if len(params) > 1 else 2.0
                frame = calculate_bollinger_bands(df, period=period, std=std_mult)
                last = frame.iloc[-1]
                result[indicator] = {
                    "bb_upper": float(last["bb_upper"]),
                    "bb_middle": float(last["bb_middle"]),
                    "bb_lower": float(last["bb_lower"]),
                    "bb_width": float(last["bb_width"]),
                }

            elif base == "sma":
                period = int(params[0]) if params else 20
                series = calculate_moving_average(df, period=period)
                result[indicator] = float(series.iloc[-1]) if not series.empty else None

            elif base == "ema":
                period = int(params[0]) if params else 20
                series = (
                    df["close"].astype(float).ewm(span=period, adjust=False).mean()
                )
                result[indicator] = float(series.iloc[-1]) if not series.empty else None

            elif base == "atr":
                period = int(params[0]) if params else 14
                series = calculate_atr(df, period=period)
                result[indicator] = float(series.iloc[-1]) if not series.empty else None

            else:
                logger.warning("Unsupported indicator '%s'; skipping.", indicator)

        except Exception as exc:
            logger.error("Failed to compute indicator '%s': %s", indicator, exc)
            result[indicator] = None

    return result
