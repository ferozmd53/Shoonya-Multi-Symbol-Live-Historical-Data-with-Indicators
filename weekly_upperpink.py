"""Weekly-only TA-Lib ATR cache and upperpink step calculation.

This module is deliberately separate from pink_mirror.py. Daily upperpink keeps
its original Pine-compatible ATR implementation unchanged.

The cache stores completed weekly OHLC + ATR rows in the existing SQLite
candle database. First use bootstraps with TA-Lib. New weeks use the equivalent
Wilder recurrence from the last saved ATR. A changed historical weekly candle
invalidates the cache from that candle forward and triggers a rebuild.

A provisional current week is calculated in memory and is never saved as a
completed ATR row.
"""
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd
import talib


_SCHEMA = """
CREATE TABLE IF NOT EXISTS weekly_atr_cache (
    symbol TEXT NOT NULL,
    week TEXT NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    atr REAL,
    PRIMARY KEY (symbol, week)
);
"""


def _connect(db_path):
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path), timeout=30)
    con.execute("PRAGMA busy_timeout=30000")
    con.execute(_SCHEMA)
    return con



def _database(db_path):
    """Close the SQLite connection as well as committing its transaction."""
    class DatabaseContext:
        def __enter__(self):
            self.connection = _connect(db_path)
            return self.connection

        def __exit__(self, exc_type, exc_value, traceback):
            try:
                if exc_type is None:
                    self.connection.commit()
                else:
                    self.connection.rollback()
            finally:
                self.connection.close()
            return False
    return DatabaseContext()


def _number(value):
    try:
        result = float(value)
        return result if np.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def _same(a, b):
    a, b = _number(a), _number(b)
    if a is None or b is None:
        return a is b
    return abs(a - b) <= max(1e-8, abs(a) * 1e-10)


def _talib_atr(high, low, close, period):
    """TA-Lib ATR aligned to Pine's first valid index (period - 1).

    TA-Lib normally starts ATR one bar later because it has no previous close
    for the first input bar. Prepending a flat synthetic candle at the first
    close makes the first real candle's TR equal to high-low and aligns TA-Lib's
    initial Wilder seed with the existing Pine calculation.
    """
    high = np.asarray(high, dtype=float)
    low = np.asarray(low, dtype=float)
    close = np.asarray(close, dtype=float)
    n = len(close)
    if n == 0:
        return np.asarray([], dtype=float)

    seed_close = float(close[0])
    padded_high = np.concatenate(([seed_close], high))
    padded_low = np.concatenate(([seed_close], low))
    padded_close = np.concatenate(([seed_close], close))
    result = talib.ATR(padded_high, padded_low, padded_close, timeperiod=int(period))
    return np.asarray(result[1:], dtype=float)


def _true_range(high, low, close, previous_close=None):
    if previous_close is None:
        return float(high - low)
    return float(max(
        high - low,
        abs(high - previous_close),
        abs(low - previous_close),
    ))


def atr_values(db_path, symbol, weekly_df, period=200,
               persist_last=True, persist=True):
    """Return TA-Lib/Wilder ATR values aligned to weekly_df rows.

    persist_last=False means the final row is provisional and must not be
    written to SQLite. persist=False is used for historical AS_OF_DATE views.
    """
    if weekly_df is None or weekly_df.empty:
        return np.asarray([], dtype=float)

    frame = weekly_df[["datetime", "open", "high", "low", "close"]].copy()
    frame["datetime"] = pd.to_datetime(frame["datetime"])
    frame = frame.sort_values("datetime").drop_duplicates("datetime").reset_index(drop=True)
    dates = [value.strftime("%Y-%m-%d") for value in frame["datetime"]]
    opens = frame["open"].astype(float).to_numpy()
    highs = frame["high"].astype(float).to_numpy()
    lows = frame["low"].astype(float).to_numpy()
    closes = frame["close"].astype(float).to_numpy()
    n = len(frame)
    save_count = n if persist_last else max(0, n - 1)
    output = np.full(n, np.nan, dtype=float)

    if not persist:
        return _talib_atr(highs, lows, closes, period)

    symbol = str(symbol or "UNKNOWN").strip().upper()
    with _database(db_path) as con:
        rows = con.execute(
            "SELECT week, open, high, low, close, atr "
            "FROM weekly_atr_cache WHERE symbol=? ORDER BY week",
            (symbol,),
        ).fetchall()
        cached = {row[0]: row[1:] for row in rows}

        # Find the first completed weekly candle that is missing or changed.
        first_bad = save_count
        for i in range(save_count):
            old = cached.get(dates[i])
            if old is None or not all((
                _same(old[0], opens[i]),
                _same(old[1], highs[i]),
                _same(old[2], lows[i]),
                _same(old[3], closes[i]),
            )):
                first_bad = i
                break

        if first_bad < save_count:
            con.execute(
                "DELETE FROM weekly_atr_cache WHERE symbol=? AND week>=?",
                (symbol, dates[first_bad]),
            )

        # Load unchanged cached ATR values.
        for i in range(first_bad):
            old = cached.get(dates[i])
            value = _number(old[4]) if old else None
            if value is not None:
                output[i] = value

        # Rebuild from TA-Lib if this is the first bootstrap or a correction
        # affects the ATR seed. Otherwise continue Wilder ATR incrementally.
        if first_bad < save_count:
            can_continue = first_bad >= int(period)
            previous_atr = output[first_bad - 1] if first_bad > 0 else np.nan
            can_continue = can_continue and np.isfinite(previous_atr)

            if not can_continue:
                rebuilt = _talib_atr(highs[:save_count], lows[:save_count],
                                     closes[:save_count], period)
                output[:save_count] = rebuilt
                first_rebuilt = 0
            else:
                first_rebuilt = first_bad
                for i in range(first_bad, save_count):
                    tr = _true_range(
                        highs[i], lows[i], closes[i],
                        closes[i - 1] if i > 0 else None,
                    )
                    output[i] = ((output[i - 1] * (int(period) - 1)) + tr) / int(period)

            payload = []
            for i in range(first_rebuilt, save_count):
                atr = float(output[i]) if np.isfinite(output[i]) else None
                payload.append((
                    symbol, dates[i], float(opens[i]), float(highs[i]),
                    float(lows[i]), float(closes[i]), atr,
                ))
            if payload:
                con.executemany(
                    "INSERT OR REPLACE INTO weekly_atr_cache "
                    "(symbol, week, open, high, low, close, atr) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    payload,
                )
                action = "initialized" if first_rebuilt == 0 else "updated"
                print(
                    f"Weekly TA-Lib ATR cache {action} for {symbol}: "
                    f"{len(payload)} completed weekly candles"
                )

        # Provisional current week: calculate in memory only, never persist it.
        if save_count < n:
            i = save_count
            if i == 0:
                provisional = _talib_atr(highs[:1], lows[:1], closes[:1], period)
                output[i] = provisional[-1] if len(provisional) else np.nan
            else:
                previous_atr = output[i - 1]
                if np.isfinite(previous_atr):
                    tr = _true_range(highs[i], lows[i], closes[i], closes[i - 1])
                    output[i] = ((previous_atr * (int(period) - 1)) + tr) / int(period)
                elif i >= int(period) - 1:
                    provisional = _talib_atr(highs[:i + 1], lows[:i + 1],
                                              closes[:i + 1], period)
                    output[i] = provisional[-1]

    return output


def step_series(high, low, close, src, atr, width=0.7,
                atr_hi=100, start_bar=200, last_bar_confirmed=True):
    """Weekly upperpink step line using the cached TA-Lib ATR series."""
    src = np.asarray(src, dtype=float)
    atr = np.asarray(atr, dtype=float)
    n = len(src)
    st = np.full(n, np.nan, dtype=float)
    if n <= int(start_bar):
        return st

    wide = np.full(n, np.nan, dtype=float)
    first_wide = int(atr_hi) - 1
    for i in range(first_wide, n):
        window = atr[max(0, i - int(atr_hi) + 1):i + 1]
        if len(window) == int(atr_hi) and np.all(np.isfinite(window)):
            wide[i] = float(np.max(window)) * float(width)

    for i in range(n):
        previous = st[i - 1] if i > 0 else np.nan
        upper = previous + wide[i]
        lower = previous - wide[i]
        confirmed = True if i < n - 1 else bool(last_bar_confirmed)
        if i == int(start_bar):
            st[i] = src[i]
        elif confirmed and src[i] > upper:
            st[i] = src[i]
        elif confirmed and src[i] < lower:
            st[i] = src[i]
        else:
            st[i] = previous
    return st


def step_last(high, low, close, src, atr, width=0.7,
              atr_hi=100, start_bar=200, last_bar_confirmed=True):
    values = step_series(
        high, low, close, src, atr, width, atr_hi, start_bar,
        last_bar_confirmed,
    )
    return float(values[-1]) if len(values) else float("nan")
