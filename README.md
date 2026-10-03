# 台股選股助手 MVP

## 執行

在 PowerShell 進入本目錄後執行（程式放在本目錄，不放 `.venv`）：

```powershell
cd C:\Users\brian\OneDrive\Desktop\project\選股
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py
```

若尚未建立虛擬環境，先執行 `python -m venv .venv`。已安裝套件的 Python 也可以直接執行 `python main.py`。

```powershell
python main.py --limit 30                 # 小型連線驗證，非全市場排名
python main.py --min-volume 2000000       # 均量門檻200萬股（2000張）
python main.py --refresh                  # 重新下載，忽略快取
python -m unittest -v                    # 不連網的自動測試
```

預設掃描全股票池並列出 Top 20；不足 20 檔就列出實際通過數，零檔也會明確顯示。第一次全市場下載可能需要數分鐘；進度訊息與下載錯誤在 stderr，結果在 stdout。

## 模組

- `data.py`：證交所上市／上櫃普通股清單，Yahoo Finance 日 OHLCV 與 CSV 快取。
- `indicators.py`：均線、報酬、均量、MA20乖離、RSI14、ATR% 與5日量比。
- `filters.py`：四個條件的交集。
- `scoring.py`：評分並排序。
- `screener.py`：整合流程，回傳結果及資料排除原因。
- `main.py`：CLI 參數與 Top 20 中文輸出。

## 資料及計算定義

股票池來自[證交所 ISIN 上市清單](https://isin.twse.com.tw/isin/C_public.jsp?strMode=2)與[上櫃清單](https://isin.twse.com.tw/isin/C_public.jsp?strMode=4)。保留四位數且 CFI 以 ES 開頭的普通股，排除名稱含 `-KY`（不分大小寫）的股票、ETF、特別股、權證、興櫃與存託憑證。上市使用 `.TW`、上櫃使用 `.TWO`。舊版曾包含 KY，現在即使讀取舊快取也會套用排除規則，不必先刪除快取。

透過 [yfinance.download](https://ranaroussi.github.io/yfinance/reference/api/yfinance.download.html) 取得近 200 個日曆日的每日 OHLCV。明確設定 `auto_adjust=False`；以資料源 Close 計算**價格報酬**，不含現金股利再投資。成交量單位為股，輸出除以 1,000 顯示張數。

台北時間 15:00 前排除當日日線。以大盤最新有效交易日為共同截止日；大盤超過 14 個日曆日未更新即停止，輸出一律標示實際資料日期。大盤使用加權指數 `^TWII`，上市、上櫃採相同基準。

- MA20、MA60：最近 20／60 個交易日收盤價的簡單平均。
- 20日報酬：`Close[t] / Close[t-20] - 1`，需要 21 筆價格。
- 20日平均成交量：最近 20 個交易日成交股數的簡單平均。
- MA20乖離：`Close / MA20 - 1`。
- 5日量比：`5日平均成交量 / 20日平均成交量`，兩段均含最新交易日；量價方向使用 `Close[t] / Close[t-5] - 1`。
- RSI14：使用前14筆收盤價變化的平均漲幅／平均跌幅初始化，再以 Wilder 平滑更新。連漲無跌為100、連跌無漲為0、完全平盤為50；不足15筆收盤價不產生 RSI。計算依據：[RSI公式](https://www.fidelity.com/learning-center/trading-investing/technical-analysis/technical-indicator-guide/RSI)、[Wilder平滑說明](https://www.fidelity.com/bin-public/060_www_fidelity_com/documents/learning-center/trading-with-momentum-transcript.pdf)。
- ATR14：TR 為 `max(High-Low, abs(High-前收), abs(Low-前收))`；第一筆 TR 使用 High-Low，首14筆 TR 平均初始化，再以 Wilder 平滑更新。`ATR% = ATR14 / Close`。計算依據：[ATR公式](https://www.fidelity.com/learning-center/trading-investing/technical-analysis/technical-indicator-guide/atr)。
- 入選：`MA20 > MA60`、`Close > MA20`、`return20 > 大盤return20`、`avg_volume20 >= 門檻`，必須全部成立。預設門檻 1,000,000 股。

先排除 Yahoo 在大盤無日線日期補出的零量平盤棒，再要求個股近 60 筆日期與大盤近 60 個交易日完全一致，且成交量皆大於零。資料不足、停牌、缺漏、下載失敗的股票會排除並計數，不補值。若所有股票皆無有效資料，程式以失敗狀態結束；有有效資料但無股票通過則正常輸出零檔。

## 評分

只對四個條件全部通過者評分。均量的**絕對大小只作流動性篩選**，不占排名權重；量價分只使用相對量能變化。新的分數採固定門檻與上限，避免最大漲幅、最大量股票持續獲得額外加分。

`總分 = max(0, 趨勢分 + 相對強弱分 + 量價分 - 過熱/風險扣分)`

以下 `clip(x)` 表示將 x 限制在 0～1；程式內百分比用小數比例表示。

| 項目 | 分數 | 規則 |
| --- | --- | --- |
| 趨勢分 | 0～45 | `45 × clip((MA20/MA60 - 1) / 10%)`；均線差達10%後不再加分 |
| 相對強弱分 | 0～25 | `25 × clip(20日超額報酬 / 15%)`；超額15個百分點後封頂 |
| 量價分 | 0～30 | `30 × clip((5日量比 - 0.8) / 0.7) × clip(5日報酬 / 5%)`；量比1.5倍、5日上漲5%即封頂，近5日下跌或平盤不給量價加分 |
| MA20乖離扣分 | 0～30 | 乖離≤5%不扣，5%～20%線性增加，≥20%扣30分 |
| RSI過熱扣分 | 0～20 | RSI≤70不扣，70～90線性增加，≥90扣20分 |
| ATR波動扣分 | 0～15 | ATR%≤4%不扣，4%～10%線性增加，≥10%扣15分 |

三項扣分相加最多65分。RSI、ATR%、乖離只扣分、不直接淘汰；總分為0仍保留在通過清單。若評分所需資料缺值，列為資料排除，避免把未知風險當成零風險。總分同分時先依風險扣分較少者，再依股票代碼升冪排序。

終端逐檔顯示總分、趨勢分、相對強弱分、量價分、過熱/風險扣分，以及 MA20乖離、RSI14、ATR% 和簡短原因。`run_screener().top` 也保留各分項，含 `bias_penalty`、`rsi_penalty`、`atr_penalty`。計算及排序使用未四捨五入數字，畫面分數四捨五入至兩位。

這組權重和門檻是「強勢、但抑制過度延伸」的初始啟發式規則，尚未經績效回測；分數不是獲利機率，也不代表已確認買點。權重與門檻集中於 `scoring.py`，不會依當次股票池重新計算百分位。

## 快取與限制

`.cache/` 位於程式目錄下，股票池每日更新一次，OHLCV 按本次下載截止日分開保存；可用 `--refresh` 取得資料源的後續修正。網路失敗不會改用模擬資料或偷偷讀取其他日期的舊快取。

免費行情源可能延遲、限流或缺資料，部分下載失敗時排名只涵蓋有效股票，終端會列出資料排除數及最多五筆原因。`run_screener()` 回傳的 `skipped` 字典保留完整原因。盤中未完整日線不參與篩選；本版不包含 AI、法人、基本面或支撐壓力。

Windows 使用 curl 的[原生 CA 信任庫選項](https://curl.se/libcurl/c/CURLOPT_SSL_OPTIONS.html)，保留 HTTPS 驗證，並避免中文安裝路徑造成憑證檔案讀取失敗。
