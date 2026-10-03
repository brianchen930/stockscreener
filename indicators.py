"""以交易日計算技術指標；不補值、不使用未來資料。"""

import pandas as pd


def _wilder_average(values: pd.Series, period=14) -> pd.Series:
    """以首 period 筆 SMA 起算，再使用 (前值×13＋新值)/14。"""
    seeded = values.astype(float).copy()
    if len(seeded) < period:
        return pd.Series(float("nan"), index=values.index)
    seeded.iloc[:period - 1] = float("nan")
    seeded.iloc[period - 1] = values.iloc[:period].mean()
    return seeded.ewm(alpha=1 / period, adjust=False).mean()


def calculate_indicators(history: pd.DataFrame) -> pd.DataFrame:
    result = history.sort_index().copy()
    result["ma20"] = result.Close.rolling(20, min_periods=20).mean()
    result["ma60"] = result.Close.rolling(60, min_periods=60).mean()
    result["return20"] = result.Close.pct_change(20, fill_method=None)
    result["avg_volume20"] = result.Volume.rolling(20, min_periods=20).mean()
    result["bias20"] = result.Close / result.ma20 - 1
    result["return5"] = result.Close.pct_change(5, fill_method=None)
    result["volume_ratio5_20"] = (
        result.Volume.rolling(5, min_periods=5).mean()
        / result.avg_volume20.where(result.avg_volume20 > 0)
    )

    changes = result.Close.diff().iloc[1:]
    gain = _wilder_average(changes.clip(lower=0))
    loss = _wilder_average(-changes.clip(upper=0))
    rsi = 100 - 100 / (1 + gain / loss.where(loss > 0))
    rsi = rsi.mask((loss == 0) & (gain > 0), 100)
    rsi = rsi.mask((loss == 0) & (gain == 0), 50)
    result["rsi14"] = rsi

    previous_close = result.Close.shift(1)
    true_range = pd.concat([
        result.High - result.Low,
        (result.High - previous_close).abs(),
        (result.Low - previous_close).abs(),
    ], axis=1).max(axis=1)
    result["atr14"] = _wilder_average(true_range)
    # 內部保留比例，例如 0.04；輸出格式化為 4.00%。
    result["atr_pct"] = result.atr14 / result.Close
    return result
