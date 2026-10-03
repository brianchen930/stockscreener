"""執行：python main.py；快速驗證：python main.py --limit 30。"""

import argparse
import logging
import sys

from screener import run_screener


def main():
    parser = argparse.ArgumentParser(description="台股選股助手 MVP")
    parser.add_argument("--top", type=int, default=20, help="最多顯示幾檔（預設20）")
    parser.add_argument("--min-volume", type=float, default=1_000_000,
                        help="20日平均成交量下限，單位：股（預設100萬股）")
    parser.add_argument("--limit", type=int, help="只掃描股票池前N檔，供快速驗證")
    parser.add_argument("--refresh", action="store_true", help="忽略快取，重新下載")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        result = run_screener(args.top, args.min_volume, args.limit, args.refresh)
    except Exception as exc:
        print(f"選股失敗：{exc}", file=sys.stderr)
        return 1
    print(f"\n台股選股助手｜資料日期 {result.as_of:%Y-%m-%d}")
    print(f"大盤：加權指數 ^TWII｜20日報酬 {result.benchmark_return:+.2%}")
    print(f"流動性門檻：20日均量至少 {args.min_volume:,.0f} 股（{args.min_volume / 1000:,.0f} 張）")
    print(f"掃描 {result.pool_count} 檔｜有效 {result.valid_count} 檔｜"
          f"資料排除 {len(result.skipped)} 檔｜通過 {result.passed_count} 檔")
    if args.limit:
        print("本次為部分股票池驗證，非全市場排名。")
    print("股票池：已排除名稱含 -KY 的股票")
    print("評分：趨勢45＋相對強弱25＋量價30－過熱/風險扣分；總分最低0（非獲利機率）")
    print("均量僅用於流動性篩選；量價採5日均量/20日均量，搭配5日價格變化。")
    if result.top.empty:
        print("沒有股票符合全部篩選條件。")
    else:
        print(f"\nTop {args.top}（實際列出 {len(result.top)} 檔）")
        for rank, row in enumerate(result.top.itertuples(), start=1):
            print(f"{rank:>2}. {row.code} {row.name}（{row.market}）｜"
                  f"總分 {row.score:.2f}｜收盤 {row.Close:.2f}")
            print(f"    趨勢分 {row.trend_score:.2f}｜相對強弱分 {row.relative_strength_score:.2f}｜"
                  f"量價分 {row.volume_price_score:.2f}｜過熱/風險扣分 -{row.risk_penalty:.2f}")
            print(f"    MA20乖離 {row.bias20:+.2%}｜RSI14 {row.rsi14:.1f}｜ATR% {row.atr_pct:.2%}")
            print(f"    入選原因：{row.reason}")
    if result.skipped:
        print("\n資料排除範例（最多5檔）：")
        for symbol, reason in list(result.skipped.items())[:5]:
            print(f"  {symbol}：{reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
