"""離線驗證：python -m unittest -v。"""

from datetime import datetime
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from data import clean_ohlcv, download_end_date, fetch_daily_ohlcv, parse_stock_pool
from filters import filter_stocks
from indicators import calculate_indicators
from scoring import score_stocks
from screener import run_screener


def history(growth=0.01, days=80, volume=2_000_000):
    close = 100 * (1 + growth) ** np.arange(days)
    return pd.DataFrame({"Open": close, "High": close + 1, "Low": close - 1,
                         "Close": close, "Volume": volume},
                        index=pd.bdate_range("2026-05-01", periods=days, name="Date"))


class ScreenerTests(unittest.TestCase):
    def test_indicators_use_20_intervals_and_60_observations(self):
        bars = history()
        result = calculate_indicators(bars)
        self.assertTrue(pd.isna(result.iloc[58].ma60))
        self.assertAlmostEqual(result.iloc[-1].ma20, bars.Close.tail(20).mean())
        self.assertAlmostEqual(result.iloc[-1].ma60, bars.Close.tail(60).mean())
        self.assertAlmostEqual(result.iloc[-1].return20, 1.01 ** 20 - 1)
        self.assertEqual(result.iloc[-1].avg_volume20, 2_000_000)

    def test_all_filters_and_strict_boundaries(self):
        base = {"code": "2330", "Close": 120., "ma20": 110., "ma60": 100.,
                "return20": .12, "avg_volume20": 1_000_000.}
        rows = [base, {**base, "ma20": 100}, {**base, "Close": 110},
                {**base, "return20": .10}, {**base, "avg_volume20": 999_999},
                {**base, "ma60": np.nan}, {**base, "return20": np.inf}]
        passed = filter_stocks(pd.DataFrame(rows), .10)
        self.assertEqual(list(passed.index), [0])
        self.assertAlmostEqual(passed.iloc[0].excess_return20, .02)
        with self.assertRaises(ValueError):
            filter_stocks(pd.DataFrame(rows), .10, float("nan"))

    def test_score_order_and_stable_ties(self):
        stocks = pd.DataFrame([
            {"code": "3000", "excess_return20": .1, "trend_strength": .1, "avg_volume20": 1e6},
            {"code": "2000", "excess_return20": .2, "trend_strength": .2, "avg_volume20": 2e6},
            {"code": "1000", "excess_return20": .2, "trend_strength": .2, "avg_volume20": 2e6},
        ])
        stocks = stocks.assign(bias20=.03, rsi14=60., atr_pct=.02,
                               return5=.03, volume_ratio5_20=1.2)
        ranked = score_stocks(stocks)
        self.assertEqual(ranked.code.tolist(), ["1000", "2000", "3000"])
        self.assertTrue(ranked.score.between(0, 100).all())

    def test_stock_pool_excludes_non_common_shares(self):
        html = "<table>" + "".join(
            "<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>"
            for row in [
                ["2330　台積電", "TW0002330008", "1994/09/05", "上市", "半導體", "ESVUFR"],
                ["0050　ETF", "", "", "上市", "", "CEOGEU"],
                ["2838A　特別股", "", "", "上市", "", "EPXXX"],
                ["6488　環球晶", "", "", "上櫃", "", "ESVUFR"],
                ["6672　騰輝電子-KY", "", "", "上市", "", "ESVUFR"],
                ["9999　測試-ky股票", "", "", "上櫃", "", "ESVUFR"],
            ]) + "</table>"
        pool = parse_stock_pool(html, "上市", ".TW")
        self.assertEqual(pool.symbol.tolist(), ["2330.TW"])
        self.assertEqual(pool.iloc[0]["name"], "台積電")
        self.assertEqual(parse_stock_pool(html, "上櫃", ".TWO").symbol.tolist(), ["6488.TWO"])

    def test_clean_invalid_bars_and_duplicates(self):
        bars = history().tail(4).astype(float)
        bars.iloc[0, bars.columns.get_loc("Close")] = -1
        bars.iloc[1, bars.columns.get_loc("Volume")] = np.inf
        bars = pd.concat([bars, bars.tail(1)])
        clean = clean_ohlcv(bars)
        self.assertEqual(len(clean), 2)
        self.assertTrue(clean.index.is_unique)

    def test_intraday_excludes_unfinished_daily_bar(self):
        tz = ZoneInfo("Asia/Taipei")
        before = download_end_date(datetime(2026, 10, 2, 14, 59, tzinfo=tz))
        after = download_end_date(datetime(2026, 10, 2, 15, 0, tzinfo=tz))
        self.assertEqual(str(before), "2026-10-02")
        self.assertEqual(str(after), "2026-10-03")

    def test_download_multiindex_and_csv_cache(self):
        bars = history()
        end = (bars.index[-1] + pd.Timedelta(days=1)).date()
        downloaded = pd.concat({"2330.TW": bars}, axis=1)
        with tempfile.TemporaryDirectory() as folder:
            with patch("data.yf.download", return_value=downloaded) as download:
                frames, errors = fetch_daily_ohlcv(["2330.TW", "9999.TW"], end, folder)
                self.assertEqual(len(frames["2330.TW"]), 80)
                self.assertIn("9999.TW", errors)
                self.assertFalse(download.call_args.kwargs["auto_adjust"])
            with patch("data.yf.download") as download:
                cached, errors = fetch_daily_ohlcv(["2330.TW"], end, folder)
                download.assert_not_called()
                self.assertFalse(errors)
                self.assertEqual(len(cached["2330.TW"]), 80)

    def run_fixture(self, growth=.01, count=4, holiday_placeholder=False, zero_trading_day=False):
        market = history(.001)
        valid = history(growth)
        if holiday_placeholder:
            placeholder = valid.iloc[[-2]].copy()
            placeholder.index = pd.DatetimeIndex([pd.Timestamp("2026-08-15")], name="Date")
            placeholder["Volume"] = 0
            valid = pd.concat([valid, placeholder]).sort_index()
        frames = {"1000.TW": valid, "1001.TW": valid.iloc[:-1],
                  "1002.TW": valid.drop(valid.index[-30]), "1003.TW": valid.tail(59)}
        if count > 4:
            frames = {f"{1000 + i}.TW": valid for i in range(count)}
        if zero_trading_day:
            frames["1001.TW"] = history(growth)
            frames["1001.TW"].iloc[-2, frames["1001.TW"].columns.get_loc("Volume")] = 0
        pool = pd.DataFrame([{"code": symbol[:4], "name": "測試股票", "market": "上市",
                              "symbol": symbol} for symbol in frames])
        end = (market.index[-1] + pd.Timedelta(days=1)).date()
        with patch("screener.get_stock_pool", return_value=pool), \
             patch("screener.download_end_date", return_value=end), \
             patch("screener.fetch_daily_ohlcv", side_effect=[({"^TWII": market}, {}), (frames, {})]):
            return run_screener()

    def test_integration_rejects_missing_stale_and_short_data(self):
        result = self.run_fixture()
        self.assertEqual(result.pool_count, 4)
        self.assertEqual(result.valid_count, 1)
        self.assertEqual(len(result.skipped), 3)
        self.assertEqual(result.top.code.tolist(), ["1000"])
        self.assertIn("MA20", result.top.iloc[0].reason)
        self.assertIn("大盤", result.top.iloc[0].reason)

    def test_integration_empty_passed_is_valid_result(self):
        result = self.run_fixture(growth=-.001)
        self.assertEqual(result.valid_count, 1)
        self.assertEqual(result.passed_count, 0)
        self.assertTrue(result.top.empty)

    def test_zero_volume_holiday_is_ignored_but_open_day_is_rejected(self):
        result = self.run_fixture(holiday_placeholder=True, zero_trading_day=True)
        self.assertEqual(result.top.code.tolist(), ["1000"])
        self.assertIn("零成交量", result.skipped["1001.TW"])

    def test_top20_limits_output_after_scoring_all_passers(self):
        result = self.run_fixture(count=25)
        self.assertEqual(result.passed_count, 25)
        self.assertEqual(len(result.top), 20)
        self.assertEqual(result.top.code.tolist(), [str(1000 + i) for i in range(20)])

    def test_benchmark_failure_stops_screening(self):
        pool = pd.DataFrame([{"symbol": "2330.TW"}])
        with patch("screener.get_stock_pool", return_value=pool), \
             patch("screener.fetch_daily_ohlcv", return_value=({}, {"^TWII": "下載失敗"})) as fetch:
            with self.assertRaisesRegex(RuntimeError, "無法取得大盤"):
                run_screener()
            self.assertEqual(fetch.call_count, 1)


if __name__ == "__main__":
    unittest.main()
