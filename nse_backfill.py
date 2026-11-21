"""NSE older-history backfill for stable weekly ATR initialization.

Downloads the available NSE cash-equity history back to the configured,
fixed weekly calculation anchor (normally 2010-01-01), not merely until the
minimum ATR warm-up count is reached. This keeps Wilder RMA initialization
consistent when the amount of downloaded history changes between runs.
NSE rows are cached separately from Shoonya's more recent candle history.
"""
import asyncio
import re
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing

import requests
from datetime import date, datetime, timedelta
from io import StringIO
from pathlib import Path

import pandas as pd

try:
    import aiohttp
except ImportError:  # graceful fallback if optional dependency was not installed
    aiohttp = None


NSE_HOME = "https://www.nseindia.com"
NSE_REPORT = "https://www.nseindia.com/report-detail/eq_security"
NSE_HISTORY = "https://www.nseindia.com/api/historicalOR/generateSecurityWiseHistoricalData"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "text/csv,text/plain,*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": NSE_REPORT,
}
_SCHEMA = """
CREATE TABLE IF NOT EXISTS nse_candles (
    symbol TEXT NOT NULL, date TEXT NOT NULL,
    open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL,
    close REAL NOT NULL, volume REAL NOT NULL DEFAULT 0,
    PRIMARY KEY(symbol, date)
)
"""
_INDEX_NAMES = {
    "NIFTY", "NIFTY50", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY",
    "NIFTYNEXT50", "INDIAVIX", "SENSEX", "SENSEX50", "BANKEX",
}


def _connect(db_path):
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path), timeout=30)
    con.execute(_SCHEMA)
    return con


def _load_cached(db_path, symbol):
    with closing(_connect(db_path)) as con:
        rows = con.execute(
            "SELECT date, open, high, low, close, volume FROM nse_candles "
            "WHERE symbol=? ORDER BY date", (symbol,)
        ).fetchall()
    if not rows:
        return pd.DataFrame(columns=["datetime", "open", "high", "low", "close", "volume"])
    frame = pd.DataFrame(rows, columns=["datetime", "open", "high", "low", "close", "volume"])
    frame["datetime"] = pd.to_datetime(frame["datetime"], errors="coerce")
    return frame.dropna(subset=["datetime"])


def _save_rows(db_path, symbol, frame):
    if frame is None or frame.empty:
        return 0
    rows = []
    for row in frame.itertuples(index=False):
        try:
            d = pd.Timestamp(row.datetime).date().isoformat()
            values = [float(row.open), float(row.high), float(row.low),
                      float(row.close), float(row.volume)]
            if not all(pd.notna(v) for v in values):
                continue
            if values[1] < values[2] or min(values[:4]) <= 0:
                continue
            rows.append((symbol, d, *values))
        except Exception:
            continue
    if rows:
        with closing(_connect(db_path)) as con:
            con.executemany(
                "INSERT OR REPLACE INTO nse_candles VALUES (?,?,?,?,?,?,?)", rows
            )
            con.commit()
    return len(rows)


def _normal_key(value):
    return re.sub(r"[^a-z0-9]", "", str(value).strip().lower())


def _parse_csv(text, symbol):
    if not text or not text.strip():
        return pd.DataFrame()
    try:
        raw = pd.read_csv(StringIO(text.lstrip("\ufeff")))
    except Exception as exc:
        raise ValueError("NSE response was not readable CSV: " + str(exc))

    cols = {_normal_key(c): c for c in raw.columns}

    def pick(*names):
        for name in names:
            if _normal_key(name) in cols:
                return cols[_normal_key(name)]
        return None

    symbol_col = pick("Symbol")
    series_col = pick("Series")
    date_col = pick("Date", "mTIMESTAMP", "CH_TIMESTAMP")
    open_col = pick("Open Price", "Open")
    high_col = pick("High Price", "High")
    low_col = pick("Low Price", "Low")
    close_col = pick("Close Price", "Close")
    volume_col = pick("Total Traded Quantity", "Total Traded Qty", "Volume")

    required = [symbol_col, series_col, date_col, open_col, high_col, low_col, close_col]
    if any(c is None for c in required):
        raise ValueError("NSE CSV is missing one or more required OHLC/series columns")

    raw = raw[raw[symbol_col].astype(str).str.strip().str.upper() == symbol].copy()
    raw = raw[raw[series_col].astype(str).str.strip().str.upper() == "EQ"].copy()
    if raw.empty:
        return pd.DataFrame()

    out = pd.DataFrame()
    out["datetime"] = pd.to_datetime(raw[date_col], dayfirst=True, errors="coerce")
    for target, source in (("open", open_col), ("high", high_col),
                           ("low", low_col), ("close", close_col)):
        out[target] = pd.to_numeric(
            raw[source].astype(str).str.replace(",", "", regex=False), errors="coerce"
        )
    if volume_col:
        out["volume"] = pd.to_numeric(
            raw[volume_col].astype(str).str.replace(",", "", regex=False), errors="coerce"
        ).fillna(0)
    else:
        out["volume"] = 0

    out = out.dropna(subset=["datetime", "open", "high", "low", "close"])
    out = out[(out[["open", "high", "low", "close"]] > 0).all(axis=1)]
    out = out[out["high"] >= out["low"]]
    return out.sort_values("datetime").drop_duplicates("datetime", keep="last").reset_index(drop=True)


async def _fetch_one(session, semaphore, symbol, start_date, end_date):
    params = {
        "from": start_date.strftime("%d-%m-%Y"),
        "to": end_date.strftime("%d-%m-%Y"),
        "symbol": symbol,
        "type": "priceVolumeDeliverable",
        # ALL is requested because NSE can omit EQ rows from some EQ-only responses.
        # The CSV parser then keeps only the exact EQ series.
        "series": "ALL",
        "csv": "true",
    }
    async with semaphore:
        for attempt in range(1, 4):
            try:
                async with session.get(NSE_HISTORY, params=params) as response:
                    body = await response.text()
                    if response.status in (401, 403, 429) or response.status >= 500:
                        raise RuntimeError("HTTP " + str(response.status))
                    response.raise_for_status()
                    return _parse_csv(body, symbol)
            except Exception as exc:
                if attempt == 3:
                    print(
                        f"[NSE BACKFILL] FAILED {symbol} {start_date} to {end_date}: {exc}"
                    )
                    return pd.DataFrame()
                await asyncio.sleep(0.7 * (2 ** (attempt - 1)))
    return pd.DataFrame()


def _fetch_one_sync(symbol, start_date, end_date):
    params = {
        "from": start_date.strftime("%d-%m-%Y"),
        "to": end_date.strftime("%d-%m-%Y"),
        "symbol": symbol,
        "type": "priceVolumeDeliverable",
        "series": "ALL",
        "csv": "true",
    }
    for attempt in range(1, 4):
        try:
            with requests.Session() as session:
                session.headers.update(HEADERS)
                session.get(NSE_HOME, timeout=20)
                time.sleep(0.2)
                session.get(NSE_REPORT, timeout=20)
                response = session.get(NSE_HISTORY, params=params, timeout=60)
                if response.status_code in (401, 403, 429) or response.status_code >= 500:
                    raise RuntimeError("HTTP " + str(response.status_code))
                response.raise_for_status()
                return _parse_csv(response.text, symbol)
        except Exception as exc:
            if attempt == 3:
                print(
                    f"[NSE BACKFILL] FAILED {symbol} {start_date} to {end_date}: {exc}"
                )
                return pd.DataFrame()
            time.sleep(0.7 * (2 ** (attempt - 1)))
    return pd.DataFrame()


def _fetch_range_sync(symbol, start_date, end_date, max_concurrent=3):
    chunks = []
    cursor = start_date
    while cursor <= end_date:
        chunk_end = min(cursor + timedelta(days=179), end_date)
        chunks.append((cursor, chunk_end))
        cursor = chunk_end + timedelta(days=1)
    with ThreadPoolExecutor(max_workers=max_concurrent) as pool:
        results = list(pool.map(
            lambda item: _fetch_one_sync(symbol, item[0], item[1]), chunks
        ))
    good = [frame for frame in results if frame is not None and not frame.empty]
    return pd.concat(good, ignore_index=True) if good else pd.DataFrame()


async def _fetch_range(symbol, start_date, end_date, max_concurrent=3):
    timeout = aiohttp.ClientTimeout(total=60, connect=20)
    connector = aiohttp.TCPConnector(limit=max_concurrent, limit_per_host=max_concurrent)
    async with aiohttp.ClientSession(headers=HEADERS, timeout=timeout,
                                     connector=connector) as session:
        # NSE session cookies are established by visiting the public pages first.
        async with session.get(NSE_HOME) as response:
            await response.read()
        await asyncio.sleep(0.25)
        async with session.get(NSE_REPORT) as response:
            await response.read()
        await asyncio.sleep(0.25)

        chunks = []
        cursor = start_date
        while cursor <= end_date:
            chunk_end = min(cursor + timedelta(days=179), end_date)
            chunks.append((cursor, chunk_end))
            cursor = chunk_end + timedelta(days=1)

        semaphore = asyncio.Semaphore(max_concurrent)
        results = await asyncio.gather(*[
            _fetch_one(session, semaphore, symbol, start, end)
            for start, end in chunks
        ])
    good = [frame for frame in results if frame is not None and not frame.empty]
    return pd.concat(good, ignore_index=True) if good else pd.DataFrame()


def _weekly_count(frame, weekly_start_date):
    if frame is None or frame.empty:
        return 0
    daily = frame[["datetime", "open", "high", "low", "close", "volume"]].copy()
    daily["datetime"] = pd.to_datetime(daily["datetime"], errors="coerce")
    daily = daily.dropna(subset=["datetime"])
    daily = daily[daily["datetime"] >= pd.Timestamp(weekly_start_date)]
    if daily.empty:
        return 0
    return int(daily["datetime"].dt.to_period("W-SUN").nunique())


def get_extra_history(symbol, shoonya_frame, db_path, weekly_start_date,
                      atr_len=200, atr_highest=100, start_bar=200):
    """Return ONLY older NSE candles needed to make weekly upperpink ATR ready.

    The current Pine port reports ATR READY after max(start_bar+1,
    atr_len+atr_highest-1) weekly candles. Existing NSE cache is reused.
    """
    raw_symbol = str(symbol or "").strip().upper()
    if raw_symbol.endswith("-EQ"):
        nse_symbol = raw_symbol[:-3]
    else:
        nse_symbol = raw_symbol
    index_key = re.sub(r"[^A-Z0-9]", "", nse_symbol)
    if not nse_symbol or index_key in _INDEX_NAMES:
        return pd.DataFrame()

    try:
        start_limit = pd.Timestamp(weekly_start_date).date()
        shoonya = shoonya_frame[["datetime", "open", "high", "low", "close", "volume"]].copy()
        shoonya["datetime"] = pd.to_datetime(shoonya["datetime"], errors="coerce")
        shoonya = shoonya.dropna(subset=["datetime"]).sort_values("datetime")
        if shoonya.empty:
            return pd.DataFrame()

        shoonya_first = shoonya["datetime"].min().date()
        required_weeks = max(int(start_bar) + 1, int(atr_len) + int(atr_highest) - 1)
        cached = _load_cached(db_path, nse_symbol)
        cached = cached[cached["datetime"] < pd.Timestamp(shoonya_first)].copy()
        combined = pd.concat([cached, shoonya], ignore_index=True)
        combined = combined.sort_values("datetime").drop_duplicates("datetime", keep="last")
        have_weeks = _weekly_count(combined, start_limit)

        if have_weeks >= required_weeks:
            print(
                f"[NSE BACKFILL] {nse_symbol}: minimum ATR warm-up is ready "
                f"({have_weeks}/{required_weeks}); checking the full fixed history anchor"
            )

        print(
            f"[NSE BACKFILL] {nse_symbol}: need older candles; "
            f"weekly ATR warm-up {have_weeks}/{required_weeks}"
        )

        # Always extend to the fixed calculation anchor, even when the
        # minimum 299-week ATR warm-up is already available. Stopping at the
        # warm-up threshold makes Wilder RMA depend on how much history happened
        # to be downloaded for a particular symbol.
        while earliest_known > start_limit + timedelta(days=7):
            end_date = earliest_known - timedelta(days=1)
            start_date = max(start_limit, end_date - timedelta(days=419))
            if start_date > end_date:
                break

            print(
                f"[NSE BACKFILL] {nse_symbol}: completing fixed history "
                f"{start_date} to {end_date}"
            )
            if aiohttp is not None:
                fetched = asyncio.run(_fetch_range(nse_symbol, start_date, end_date))
            else:
                # The existing Shoonya SDK already installs requests; this keeps
                # backfill working with --no-deps package reinstalls too.
                fetched = _fetch_range_sync(nse_symbol, start_date, end_date)

            if fetched.empty:
                print(
                    f"[NSE BACKFILL] WARNING {nse_symbol}: NSE returned no older EQ "
                    f"candles for {start_date} to {end_date}; cannot verify earlier "
                    f"history (possibly before listing or NSE data unavailable)"
                )
                break

            fetched = fetched[fetched["datetime"].dt.date <= end_date]
            fetched = fetched[fetched["datetime"].dt.date >= start_date]
            saved = _save_rows(db_path, nse_symbol, fetched)
            cached = _load_cached(db_path, nse_symbol)
            cached = cached[cached["datetime"] < pd.Timestamp(shoonya_first)].copy()
            combined = pd.concat([cached, shoonya], ignore_index=True)
            combined = combined.sort_values("datetime").drop_duplicates("datetime", keep="last")
            have_weeks = _weekly_count(combined, start_limit)

            new_earliest = min(
                shoonya_first,
                cached["datetime"].min().date() if not cached.empty else shoonya_first
            )
            print(
                f"[NSE BACKFILL] {nse_symbol}: saved {saved} NSE candles; "
                f"weekly history {have_weeks}/{required_weeks}; "
                f"oldest available candle {new_earliest}"
            )
            if new_earliest >= earliest_known:
                print(
                    f"[NSE BACKFILL] WARNING {nse_symbol}: no earlier candle was "
                    f"added; stopping to avoid repeating the same request"
                )
                break

            earliest_known = new_earliest
            if start_date == start_limit:
                break

        if have_weeks >= required_weeks:
            print(
                f"[NSE BACKFILL] {nse_symbol}: ATR READY "
                f"({have_weeks}/{required_weeks} weekly candles)"
            )
        else:
            print(
                f"[NSE BACKFILL] WARNING {nse_symbol}: only {have_weeks}/"
                f"{required_weeks} weekly candles available; ATR warm-up is incomplete"
            )

        if earliest_known <= start_limit + timedelta(days=7):
            print(
                f"[NSE BACKFILL] {nse_symbol}: fixed history anchor reached "
                f"({start_limit}); earliest candle {earliest_known}"
            )
        else:
            print(
                f"[NSE BACKFILL] WARNING {nse_symbol}: fixed history anchor "
                f"{start_limit} NOT reached; earliest candle {earliest_known}. "
                f"ATR may still differ if additional earlier data becomes available."
            )

        return cached.reset_index(drop=True)

    except Exception as exc:
        print(f"[NSE BACKFILL] {nse_symbol}: backfill skipped safely: {exc}")
        try:
            cached = _load_cached(db_path, nse_symbol)
            return cached[cached["datetime"] < pd.Timestamp(shoonya_first)].reset_index(drop=True)
        except Exception:
            return pd.DataFrame()
