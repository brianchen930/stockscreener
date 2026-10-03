"""四個條件必須全部成立。成交量單位為股。"""

import math


def filter_stocks(candidates, benchmark_return, min_avg_volume=1_000_000):
    if not math.isfinite(min_avg_volume) or min_avg_volume <= 0:
        raise ValueError("最低均量必須是大於 0 的有限數值")
    if not math.isfinite(benchmark_return):
        raise ValueError("大盤報酬無效")
    required = ["Close", "ma20", "ma60", "return20", "avg_volume20"]
    candidates = candidates.replace([float("inf"), -float("inf")], float("nan"))
    candidates = candidates.dropna(subset=required)
    passed = candidates.loc[
        (candidates.ma20 > candidates.ma60)
        & (candidates.Close > candidates.ma20)
        & (candidates.return20 > benchmark_return)
        & (candidates.avg_volume20 >= min_avg_volume)
    ].copy()
    passed["excess_return20"] = passed.return20 - benchmark_return
    passed["trend_strength"] = passed.ma20 / passed.ma60 - 1
    return passed
