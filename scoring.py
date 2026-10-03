"""固定封頂分數：趨勢45＋相對強弱25＋量價30－風險扣分。

門檻為初始啟發式規則，尚未經績效回測；所有報酬/乖離/ATR 都用比例。
"""

import numpy as np

SCORE_INPUTS = ["trend_strength", "excess_return20", "return5",
                "volume_ratio5_20", "bias20", "rsi14", "atr_pct"]

# 集中列出權重與門檻，避免強勢越延伸、加分就無限增加。
TREND_POINTS, STRENGTH_POINTS, VOLUME_PRICE_POINTS = 45, 25, 30
TREND_CAP, EXCESS_RETURN_CAP = .10, .15
VOLUME_RATIO_FLOOR, VOLUME_RATIO_CAP, RETURN5_CAP = .8, 1.5, .05
BIAS_START, BIAS_CAP, BIAS_PENALTY = .05, .20, 30
RSI_START, RSI_CAP, RSI_PENALTY = 70, 90, 20
ATR_START, ATR_CAP, ATR_PENALTY = .04, .10, 15


def _ramp(values, start, cap):
    return ((values - start) / (cap - start)).clip(0, 1)


def score_stocks(stocks):
    result = stocks.copy()
    if not np.isfinite(result[SCORE_INPUTS].to_numpy(dtype=float)).all():
        raise ValueError("評分指標缺值或無限值，不能視為零風險")
    result["trend_score"] = TREND_POINTS * _ramp(result.trend_strength, 0, TREND_CAP)
    result["relative_strength_score"] = STRENGTH_POINTS * _ramp(result.excess_return20, 0, EXCESS_RETURN_CAP)
    result["volume_price_score"] = (
        VOLUME_PRICE_POINTS
        * _ramp(result.volume_ratio5_20, VOLUME_RATIO_FLOOR, VOLUME_RATIO_CAP)
        * _ramp(result.return5, 0, RETURN5_CAP)
    )
    result["bias_penalty"] = BIAS_PENALTY * _ramp(result.bias20, BIAS_START, BIAS_CAP)
    result["rsi_penalty"] = RSI_PENALTY * _ramp(result.rsi14, RSI_START, RSI_CAP)
    result["atr_penalty"] = ATR_PENALTY * _ramp(result.atr_pct, ATR_START, ATR_CAP)
    result["risk_penalty"] = result.bias_penalty + result.rsi_penalty + result.atr_penalty
    result["score"] = (result.trend_score + result.relative_strength_score
                       + result.volume_price_score - result.risk_penalty).clip(0, 100)
    # 扣分只調整名次，不依 RSI/ATR/乖離或總分剔除通過 filter 的股票。
    return result.sort_values(
        ["score", "risk_penalty", "code"],
        ascending=[False, True, True], kind="stable",
    ).reset_index(drop=True)
