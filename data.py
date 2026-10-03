"""台股普通股清單、Yahoo 日線與簡單 CSV 快取。"""

from datetime import datetime, timedelta
import logging
from pathlib import Path
import re
import sys
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
from curl_cffi import CurlOpt, requests as curl_requests
import numpy as np
import pandas as pd
import yfinance as yf

BENCHMARK = "^TWII"
CACHE_DIR = Path(__file__).resolve().parent / ".cache"
OHLCV = ["Open", "High", "Low", "Close", "Volume"]
logger = logging.getLogger(__name__)


def exclude_ky_stocks(pool):
    excluded = pool["name"].str.contains("-KY", case=False, regex=False, na=False)
    if excluded.any():
        logger.info("股票池排除名稱含 -KY：%d 檔", excluded.sum())
    return pool.loc[~excluded].reset_index(drop=True)


def download_end_date(now=None):
    """Yahoo end 不含當日；台北 15:00 後才納入當日日線。"""
    now = now or datetime.now(ZoneInfo("Asia/Taipei"))
    now = now.astimezone(ZoneInfo("Asia/Taipei"))
    return now.date() + timedelta(days=int(now.hour >= 15))


def parse_stock_pool(html, market, suffix):
    rows = []
    for tr in BeautifulSoup(html, "html.parser").find_all("tr"):
        cells = [td.get_text(strip=True) for td in tr.find_all("td")]
        if len(cells) < 6 or cells[3] != market:
            continue
        match = re.fullmatch(r"(\d{4})\s+(.+)", cells[0])
        # ES = 普通股；排除 ETF、特別股、權證、存託憑證等。
        if match and cells[5].startswith("ES"):
            code, name = match.groups()
            rows.append({"code": code, "name": name, "market": market,
                         "symbol": code + suffix})
    if not rows:
        raise ValueError(f"{market}股票池為空，請檢查證交所資料格式")
    return exclude_ky_stocks(pd.DataFrame(rows))


def get_stock_pool(cache_dir=CACHE_DIR, refresh=False):
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now(ZoneInfo("Asia/Taipei")).date()
    path = cache_dir / f"universe-{today}.csv"
    if path.exists() and not refresh:
        pool = pd.read_csv(path, dtype=str)
        if not pool.empty and {"code", "name", "market", "symbol"} <= set(pool.columns):
            # 舊版快取也必須套用新規則，不能只在下載當下排除。
            return exclude_ky_stocks(pool)
    markets = []
    for mode, market, suffix in [(2, "上市", ".TW"), (4, "上櫃", ".TWO")]:
        request = Request(
            f"https://isin.twse.com.tw/isin/C_public.jsp?strMode={mode}",
            headers={"User-Agent": "Mozilla/5.0"},
        )
        # 使用系統信任憑證；CP950 支援證交所使用的 Big5 擴充字元。
        with urlopen(request, timeout=30) as response:
            html = response.read().decode("cp950")
        markets.append(parse_stock_pool(html, market, suffix))
    pool = pd.concat(markets, ignore_index=True).drop_duplicates("symbol")
    pool = pool.sort_values("code").reset_index(drop=True)
    pool.to_csv(path, index=False, encoding="utf-8-sig")
    return pool


def clean_ohlcv(frame):
    if frame.empty or not set(OHLCV) <= set(frame.columns):
        raise ValueError("缺少 OHLCV 日線")
    frame = frame[OHLCV].copy()
    index = pd.to_datetime(frame.index)
    if index.tz is not None:
        index = index.tz_convert("Asia/Taipei").tz_localize(None)
    frame.index = index.normalize()
    frame.index.name = "Date"
    frame = frame[~frame.index.duplicated(keep="last")].sort_index()
    frame = frame.apply(pd.to_numeric, errors="coerce")
    frame = frame.replace([np.inf, -np.inf], np.nan).dropna()
    valid = (
        (frame[["Open", "High", "Low", "Close"]] > 0).all(axis=1)
        & (frame.Volume >= 0)
        & (frame.High >= frame[["Open", "Close", "Low"]].max(axis=1))
        & (frame.Low <= frame[["Open", "Close", "High"]].min(axis=1))
    )
    return frame.loc[valid]


def fetch_daily_ohlcv(symbols, end, cache_dir=CACHE_DIR, refresh=False):
    """抓取約 6 個月未還原股利日線；回傳資料及逐股錯誤。"""
    cache_dir = Path(cache_dir)
    folder = cache_dir / f"daily-{end}"
    folder.mkdir(parents=True, exist_ok=True)
    # yfinance 的時區/cookie SQLite 也限制在專案目錄內。
    yf.set_tz_cache_location(str(cache_dir / "yfinance"))
    histories, errors, missing = {}, {}, []
    for symbol in dict.fromkeys(symbols):
        path = folder / f"{symbol.replace('^', '_')}.csv"
        if path.exists() and not refresh:
            try:
                frame = clean_ohlcv(pd.read_csv(path, index_col="Date", parse_dates=True))
                frame = frame.loc[frame.index < pd.Timestamp(end)]
                if not frame.empty:
                    histories[symbol] = frame
                    continue
            except (ValueError, OSError, KeyError):
                pass  # 壞掉的快取重新下載。
        missing.append(symbol)
    # CURLSSLOPT_NATIVE_CA = 1 << 4：Windows 使用系統信任憑證，
    # 避免 curl 的 CA 檔案路徑在中文目錄下失效；仍完整驗證 HTTPS。
    curl_options = {CurlOpt.SSL_OPTIONS: 1 << 4, CurlOpt.CAINFO: ""} if sys.platform == "win32" else {}
    for offset in range(0, len(missing), 50):
        batch = missing[offset:offset + 50]
        logger.info("下載日線 %d–%d / %d（其餘使用快取）",
                    offset + 1, offset + len(batch), len(missing))
        try:
            with curl_requests.Session(impersonate="chrome", curl_options=curl_options) as session:
                downloaded = yf.download(
                    batch, start=end - timedelta(days=200), end=end,
                    interval="1d", auto_adjust=False, actions=False,
                    group_by="ticker", multi_level_index=True,
                    threads=4, progress=False, timeout=20, session=session,
                )
        except Exception as exc:
            errors.update({symbol: f"下載失敗：{exc}" for symbol in batch})
            continue
        for symbol in batch:
            try:
                if downloaded is None:
                    raise ValueError("下載未回傳資料")
                frame = downloaded[symbol] if isinstance(downloaded.columns, pd.MultiIndex) else downloaded
                frame = clean_ohlcv(frame)
                frame = frame.loc[frame.index < pd.Timestamp(end)]
                if frame.empty:
                    raise ValueError("無有效日線（可能停牌或下載失敗）")
                histories[symbol] = frame
                frame.to_csv(folder / f"{symbol.replace('^', '_')}.csv")
            except (ValueError, KeyError, TypeError) as exc:
                errors[symbol] = str(exc)
    return histories, errors
