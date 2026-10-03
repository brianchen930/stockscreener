"""評分行為、Wilder 計算及舊股票池快取的回歸測試。"""

from datetime import datetime
from io import StringIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from data import get_stock_pool
from filters import filter_stocks
from indicators import calculate_indicators
from main import main
from scoring import score_stocks
import test_screener
from test_screener import history


def candidate(**changes):
    return {"code": "1000", "Close": 108., "ma20": 104., "ma60": 100.,
            "return20": .15, "avg_volume20": 2_000_000.,
            "trend_strength": .08, "excess_return20": .10,
            "return5": .04, "volume_ratio5_20": 1.4,
            "bias20": .04, "rsi14": 65., "atr_pct": .03, **changes}


class RankingTests(unittest.TestCase):
    def test_reasonable_candidate_beats_extended_past_winner(self):
        ranked = score_stocks(pd.DataFrame([
            candidate(code="HOT", trend_strength=.35, excess_return20=1.5,
                      bias20=.40, rsi14=95, atr_pct=.12, return5=.20),
            candidate(code="REASONABLE"),
        ]))
        self.assertEqual(ranked.code.tolist(), ["REASONABLE", "HOT"])
        self.assertEqual(ranked.iloc[1].risk_penalty, 65)
        self.assertEqual(len(ranked), 2)  # 風險高只扣分，不淘汰。

    def test_absolute_volume_does_not_change_score(self):
        ranked = score_stocks(pd.DataFrame([
            candidate(code="A", avg_volume20=1e6),
            candidate(code="B", avg_volume20=1e9),
        ]))
        self.assertEqual(ranked.iloc[0].score, ranked.iloc[1].score)
        bars = history()
        bars.loc[bars.index[-5:], "Volume"] = 3_000_000
        scaled = bars.copy()
        scaled.Volume *= 100
        self.assertAlmostEqual(calculate_indicators(bars).iloc[-1].volume_ratio5_20,
                               calculate_indicators(scaled).iloc[-1].volume_ratio5_20)

    def test_down_volume_gets_no_momentum_points_and_reward_is_capped(self):
        rows = [candidate(code="DOWN", return5=-.01, volume_ratio5_20=3),
                candidate(code="CAP", return5=.05, volume_ratio5_20=1.5),
                candidate(code="HUGE", return5=.50, volume_ratio5_20=4)]
        ranked = score_stocks(pd.DataFrame(rows)).set_index("code")
        self.assertEqual(ranked.loc["DOWN", "volume_price_score"], 0)
        self.assertEqual(ranked.loc["CAP", "volume_price_score"], 30)
        self.assertEqual(ranked.loc["HUGE", "volume_price_score"], 30)

    def test_return_reward_stops_at_cap_and_pool_size_does_not_change_score(self):
        rows = [candidate(code="CAP", excess_return20=.15),
                candidate(code="HUGE", excess_return20=2)]
        scored = score_stocks(pd.DataFrame(rows))
        self.assertTrue((scored.relative_strength_score == 25).all())
        alone = score_stocks(pd.DataFrame([rows[0]]))
        self.assertEqual(alone.iloc[0].score, scored.iloc[0].score)

    def test_penalty_boundaries_monotonic_and_total_reconciles(self):
        rows = [candidate(code="LOW", bias20=.05, rsi14=70, atr_pct=.04),
                candidate(code="MID", bias20=.125, rsi14=80, atr_pct=.07),
                candidate(code="HIGH", bias20=.20, rsi14=90, atr_pct=.10)]
        scored = score_stocks(pd.DataFrame(rows))
        np.testing.assert_allclose(scored.risk_penalty, [0, 32.5, 65])
        total = (scored.trend_score + scored.relative_strength_score
                 + scored.volume_price_score - scored.risk_penalty).clip(0, 100)
        np.testing.assert_allclose(scored.score, total)
        self.assertTrue(scored.score.between(0, 100).all())
        zero = score_stocks(pd.DataFrame([candidate(
            trend_strength=.001, excess_return20=.001, return5=-.01,
            bias20=.3, rsi14=100, atr_pct=.15)]))
        self.assertEqual(len(zero), 1)
        self.assertEqual(zero.iloc[0].score, 0)

    def test_rsi_atr_bias_do_not_change_filter_eligibility(self):
        passed = filter_stocks(pd.DataFrame([candidate(bias20=.5, rsi14=100, atr_pct=.20)]), .05)
        self.assertEqual(len(passed), 1)
        self.assertEqual(len(score_stocks(passed)), 1)

    def test_missing_risk_data_cannot_be_scored_as_safe(self):
        for key in ["bias20", "rsi14", "atr_pct"]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                score_stocks(pd.DataFrame([candidate(**{key: np.nan})]))

    def test_cli_shows_components_and_risk_values(self):
        result = test_screener.ScreenerTests().run_fixture()
        with patch("main.run_screener", return_value=result), \
             patch("main.logging.basicConfig"), \
             patch("sys.argv", ["main.py"]), patch("sys.stdout", new_callable=StringIO) as output:
            self.assertEqual(main(), 0)
        for label in ["總分", "趨勢分", "相對強弱分", "量價分", "過熱/風險扣分",
                      "MA20乖離", "RSI14", "ATR%", "入選原因", "-KY"]:
            self.assertIn(label, output.getvalue())


class IndicatorTests(unittest.TestCase):
    def test_rsi_wilder_seed_and_next_step(self):
        changes = np.array([2., -1.] * 7 + [4.])
        close = np.r_[100., 100. + changes.cumsum()]
        bars = pd.DataFrame({"Open": close, "Close": close, "High": close + 1,
                             "Low": close - 1, "Volume": 1e6})
        result = calculate_indicators(bars)
        self.assertTrue(pd.isna(result.iloc[13].rsi14))
        self.assertAlmostEqual(result.iloc[14].rsi14, 100 - 100 / 3)
        self.assertAlmostEqual(result.iloc[15].rsi14, 100 - 100 / (1 + 17 / 6.5))

    def test_rsi_all_up_all_down_and_flat(self):
        for growth, expected in [(.01, 100), (-.01, 0), (0, 50)]:
            with self.subTest(growth=growth):
                self.assertEqual(calculate_indicators(history(growth)).iloc[-1].rsi14, expected)

    def test_atr_includes_gap_and_wilder_smoothing(self):
        bars = history(0, days=15)
        bars.iloc[-1, bars.columns.get_loc("Open")] = 110
        bars.iloc[-1, bars.columns.get_loc("Close")] = 110
        bars.iloc[-1, bars.columns.get_loc("High")] = 111
        bars.iloc[-1, bars.columns.get_loc("Low")] = 109
        result = calculate_indicators(bars)
        self.assertTrue(pd.isna(result.iloc[12].atr14))
        self.assertEqual(result.iloc[13].atr14, 2)
        self.assertAlmostEqual(result.iloc[14].atr14, 37 / 14)
        self.assertAlmostEqual(result.iloc[14].atr_pct, 37 / 14 / 110)

    def test_no_lookahead_and_volume_ratio_definition(self):
        bars = history()
        bars.loc[bars.index[-5:], "Volume"] = 4_000_000
        full = calculate_indicators(bars)
        pd.testing.assert_frame_equal(full.head(60), calculate_indicators(bars.head(60)))
        self.assertAlmostEqual(full.iloc[-1].bias20, bars.Close.iloc[-1] / bars.Close.tail(20).mean() - 1)
        self.assertAlmostEqual(full.iloc[-1].volume_ratio5_20, 4e6 / 2.5e6)


class UniverseTests(unittest.TestCase):
    def test_old_cache_also_excludes_ky_without_network(self):
        today = datetime.now(ZoneInfo("Asia/Taipei")).date()
        with tempfile.TemporaryDirectory() as folder:
            pd.DataFrame([
                {"code": "2330", "name": "台積電", "market": "上市", "symbol": "2330.TW"},
                {"code": "6672", "name": "騰輝電子-KY", "market": "上市", "symbol": "6672.TW"},
            ]).to_csv(Path(folder) / f"universe-{today}.csv", index=False)
            with patch("data.urlopen") as request:
                pool = get_stock_pool(folder)
                request.assert_not_called()
            self.assertEqual(pool.code.tolist(), ["2330"])


if __name__ == "__main__":
    unittest.main()
