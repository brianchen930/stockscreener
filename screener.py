"""整合股票池 → 日線 → 指標 → 篩選 → 評分。"""

from dataclasses import dataclass
import logging
import math

import numpy as np
import pandas as pd

from data import BENCHMARK, CACHE_DIR, download_end_date, fetch_daily_ohlcv, get_stock_pool
from filters import filter_stocks
from indicators import calculate_indicators
from scoring import SCORE_INPUTS, score_stocks

logger = logging.getLogger(__name__)


@dataclass
class ScreeningResult:
    top: pd.DataFrame
    as_of: pd.Timestamp
    benchmark_return: float
    pool_count: int
    valid_count: int
    passed_count: int
    skipped: dict


def selection_reason(row, benchmark_return):
    risks = []
    if row.bias_penalty > 0:
        risks.append(f"乖離扣{row.bias_penalty:.1f}")
    if row.rsi_penalty > 0:
        risks.append(f"RSI扣{row.rsi_penalty:.1f}")
    if row.atr_penalty > 0:
        risks.append(f"ATR扣{row.atr_penalty:.1f}")
    risk_text = "、".join(risks) if risks else "未觸發過熱/波動扣分"
    return (
        f"MA20 > MA60、收盤站上MA20；勝大盤 {row.excess_return20 * 100:.2f} 個百分點；"
        f"5日量比 {row.volume_ratio5_20:.2f} 倍 / 價格 {row.return5:+.2%}；"
        f"{risk_text}"
    )


def run_screener(top_n=20, min_avg_volume=1_000_000, limit=None,
                 refresh=False, cache_dir=CACHE_DIR):
    if top_n < 1 or (limit is not None and limit < 1):
        raise ValueError("top_n 與 limit 必須大於 0")
    if not math.isfinite(min_avg_volume) or min_avg_volume <= 0:
        raise ValueError("最低均量必須是大於 0 的有限數值")
    pool = get_stock_pool(cache_dir=cache_dir, refresh=refresh)
    if limit is not None:
        pool = pool.head(limit)
    end = download_end_date()
    logger.info("股票池 %d 檔；下載日線與大盤（首次執行需要數分鐘）", len(pool))
    # 先驗證大盤，避免大盤失敗後仍大量下載個股。
    market_data, market_errors = fetch_daily_ohlcv(
        [BENCHMARK], end, cache_dir=cache_dir, refresh=refresh,
    )
    if BENCHMARK not in market_data:
        raise RuntimeError(f"無法取得大盤：{market_errors.get(BENCHMARK, '無資料')}")
    market = market_data[BENCHMARK]
    if len(market) < 60:
        raise RuntimeError("大盤資料不足 60 個交易日，無法建立比較基準")
    as_of = market.index[-1]
    if (pd.Timestamp(end) - as_of).days > 14:
        raise RuntimeError(f"大盤資料過舊（{as_of.date()}），請稍後加 --refresh 重試")
    benchmark_return = float(calculate_indicators(market).iloc[-1].return20)
    histories, errors = fetch_daily_ohlcv(
        pool.symbol.tolist(), end, cache_dir=cache_dir, refresh=refresh,
    )
    candidates, skipped = [], dict(errors)
    expected_dates = market.index[-60:]
    for stock in pool.itertuples(index=False):
        history = histories.get(stock.symbol)
        if history is None:
            skipped.setdefault(stock.symbol, "無日線資料")
            continue
        history = history.loc[history.index <= as_of]
        # Yahoo 有時在休市日補一根零量平盤棒；只移除大盤也沒有
        # 日線的零量日期。大盤開市日的零量仍視為個股資料問題。
        history = history.loc[(history.Volume > 0) | history.index.isin(market.index)]
        # 同一組交易日期，避免停牌/缺值讓個股與大盤比較不同期間。
        if len(history) < 60 or not history.index[-60:].equals(expected_dates):
            skipped[stock.symbol] = "不足60日、資料過期或近60個交易日有缺漏"
            continue
        if (history.Volume.tail(60) <= 0).any():
            skipped[stock.symbol] = "近60日含零成交量日，無法確認連續交易"
            continue
        latest = calculate_indicators(history).iloc[-1]
        # 這是資料品質檢查；有效的高 RSI/ATR 數值不會在此被剔除。
        new_inputs = [key for key in SCORE_INPUTS if key not in ("trend_strength", "excess_return20")]
        if not np.isfinite(latest[new_inputs].to_numpy(dtype=float)).all():
            skipped[stock.symbol] = "評分指標資料不足或無效"
            continue
        candidates.append({"code": stock.code, "name": stock.name,
                           "market": stock.market, "symbol": stock.symbol,
                           **latest.to_dict()})
    if not candidates:
        raise RuntimeError(f"{len(pool)} 檔股票皆無有效資料，請檢查網路或加 --refresh 重試")
    valid = pd.DataFrame(candidates)
    passed = filter_stocks(valid, benchmark_return, min_avg_volume)
    ranked = score_stocks(passed)
    ranked["reason"] = [selection_reason(row, benchmark_return) for row in ranked.itertuples()]
    return ScreeningResult(ranked.head(top_n), as_of, benchmark_return,
                           len(pool), len(valid), len(passed), skipped)
