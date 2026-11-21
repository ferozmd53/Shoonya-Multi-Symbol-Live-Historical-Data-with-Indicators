"""Weekly upperpink from TradingView's own weekly candles (tvDatafeed).

Why: upperpink is a stateful step line seeded at bar_index 500 of the WHOLE chart history.
It only matches TradingView when it is computed from the same weekly candles TradingView
has (split/bonus adjusted, no missing weeks). Daily candles from Shoonya + NSE backfill have
gaps / unadjusted prices, which shifts the seed and the ATR history.

Usage: step_by_week(symbol, last_bar_confirmed) -> {Monday Timestamp: upperpink} or None.
The first call starts a background download (never blocks the live loop) and returns None
until it is ready; callers then fall back to their own computation. version(symbol) changes
when new TradingView data arrives, so callers know to recompute.
"""
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

import pink_mirror

ENABLED = True
START_BAR = 500            # same as the TradingView-matching standalone script
N_BARS = 5000
REFRESH_SECONDS = 3600     # step only moves on closed weeks; hourly is plenty

try:
    from tvDatafeed import TvDatafeed, Interval
except Exception:          # tvDatafeed not installed -> feature silently off
    TvDatafeed = Interval = None

_lock = threading.Lock()
_tv = None
_data = {}                 # symbol -> {"df": DataFrame, "t": time, "ver": int}
_pending = set()
_calc_cache = {}
_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="TVWeekly")


def _split(symbol):
    s = str(symbol or "").strip().upper()
    exch, _, name = s.rpartition(":")
    if name.endswith("-EQ"):
        name = name[:-3]
    return (exch or "NSE"), name


def _fetch(symbol):
    global _tv
    exch, name = _split(symbol)
    try:
        with _lock:
            if _tv is None:
                _tv = TvDatafeed()
            df = _tv.get_hist(symbol=name, exchange=exch, interval=Interval.in_weekly, n_bars=N_BARS)
        if df is not None and not df.empty:
            old = _data.get(symbol)
            _data[symbol] = {"df": df, "t": time.time(), "ver": (old["ver"] + 1) if old else 1}
            print(f"[TV WEEKLY] {symbol}: {len(df)} weekly candles from TradingView")
        else:
            print(f"[TV WEEKLY] {symbol}: no data returned")
    except Exception as exc:
        print(f"[TV WEEKLY] {symbol}: unavailable ({exc})")
    finally:
        _pending.discard(symbol)


def _request(symbol):
    if symbol in _pending:
        return
    _pending.add(symbol)
    _pool.submit(_fetch, symbol)


def version(symbol):
    rec = _data.get(symbol)
    return rec["ver"] if rec else 0


def monday(ts):
    ts = pd.Timestamp(ts).normalize()
    return ts - pd.Timedelta(days=ts.weekday())


def step_by_week(symbol, last_bar_confirmed=True):
    if not ENABLED or TvDatafeed is None or not symbol:
        return None
    rec = _data.get(symbol)
    if rec is None or time.time() - rec["t"] > REFRESH_SECONDS:
        _request(symbol)
    if rec is None:
        return None
    key = (symbol, rec["ver"], bool(last_bar_confirmed))
    out = _calc_cache.get(key)
    if out is None:
        df = rec["df"]
        if len(df) <= START_BAR:
            return None
        st = pink_mirror.step_series(df["high"].values, df["low"].values, df["close"].values,
                                     df["low"].values, 0.7, 200, 100, START_BAR,
                                     bool(last_bar_confirmed))
        out = {monday(ix): float(v) for ix, v in zip(df.index, st) if v == v}
        _calc_cache.clear()
        _calc_cache[key] = out
    return out
