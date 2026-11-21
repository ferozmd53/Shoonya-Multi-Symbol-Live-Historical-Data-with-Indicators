"""Point-in-time indicator values for DAILY bars (single source of truth).

  snapshot(df, p)       indicator values on the LAST bar of df, using only df
  table(df, days, p)    the same values for each of the last `days` bars (INDICATOR_HISTORY sheet)

`p` is a dict of settings (see Extreme_Reversal_Signal.indicator_params):
  bb_length bb_std | nwe_h nwe_mult nwe_src nwe_repaint |
  pm_len pm_mult pm_ref pm_sma | step_width step_src atr_len atr_hi start_bar | first_bar_date

df: columns datetime, open, high, low, close, volume (chronological).
Every value in table() equals snapshot() of the df cut at that bar - what the indicator
showed on that day (Nadarya repaints, so a past day is computed from data up to that day).
"""
import numpy as np
import pandas as pd

import nadaraya_watson
import pink_mirror

TABLE_COLUMNS = ["Date", "Open", "High", "Low", "Close", "Volume", "BB Upper", "BB Lower",
                 "NADARYA BUY", "NADARYA SELL", "upperpink", "mirrorUpperBand",
                 "upperpink ATR Status"]


def to_weekly(df):
    """Aggregate chronological daily OHLCV candles into Monday-labelled weekly candles."""
    if df is None or df.empty:
        return pd.DataFrame(columns=["datetime", "open", "high", "low", "close", "volume"])
    daily = df[["datetime", "open", "high", "low", "close", "volume"]].copy()
    daily["datetime"] = pd.to_datetime(daily["datetime"])
    daily["week"] = daily["datetime"].dt.to_period("W-SUN").dt.start_time
    grouped = daily.groupby("week", sort=True)
    weekly = pd.DataFrame({
        "open": grouped["open"].first(),
        "high": grouped["high"].max(),
        "low": grouped["low"].min(),
        "close": grouped["close"].last(),
        "volume": grouped["volume"].sum(),
    })
    weekly.index.name = "datetime"
    return weekly.reset_index()


def _f(df, col):
    return df[col].astype(float).values


def _trim_first_bar(df, first_bar_date):
    if first_bar_date:
        return df[df["datetime"] >= pd.Timestamp(first_bar_date)].reset_index(drop=True)
    return df.reset_index(drop=True)


def _bb(df, p):
    close = df["close"].astype(float)
    sma = close.rolling(p["bb_length"]).mean()
    sd = close.rolling(p["bb_length"]).std(ddof=0)
    return (sma + p["bb_std"] * sd).values, (sma - p["bb_std"] * sd).values


def snapshot(df, p, last_bar_confirmed=True):
    """Indicator values on the last bar of df (dict of raw floats / bools)."""
    df = df.reset_index(drop=True)
    bb_u, bb_l = _bb(df, p)
    nwe_buy, nwe_sell = nadaraya_watson.nwe_last_bar_signal(
        _f(df, p["nwe_src"]), _f(df, "close"), p["nwe_h"], p["nwe_mult"], p["nwe_repaint"])
    pm = _trim_first_bar(df, p["first_bar_date"])
    pink = pink_mirror.step_last(
        _f(pm, "high"), _f(pm, "low"), _f(pm, "close"), _f(pm, p["step_src"]),
        p["step_width"], p["atr_len"], p["atr_hi"], p["start_bar"], last_bar_confirmed)
    mirror = pink_mirror.mirror_last(_f(df, p["pm_sma"]), float(df[p["pm_ref"]].iloc[-1]),
                                     p["pm_len"], p["pm_mult"])
    last = df.iloc[-1]
    return {
        "datetime": last["datetime"], "open": float(last["open"]), "high": float(last["high"]),
        "low": float(last["low"]), "close": float(last["close"]), "volume": float(last["volume"]),
        "bb_upper": float(bb_u[-1]), "bb_lower": float(bb_l[-1]),
        "nwe_buy": bool(nwe_buy), "nwe_sell": bool(nwe_sell),
        "pink": float(pink), "mirror": float(mirror),
    }


def table(df, days, p, last_bar_confirmed=True):
    """DataFrame (newest first) with the indicator values of the last `days` bars."""
    df = df.reset_index(drop=True)
    n = len(df)
    days = max(1, min(int(days), n))
    bb_u, bb_l = _bb(df, p)
    mirror = pink_mirror.mirror_series(_f(df, p["pm_sma"]), _f(df, p["pm_ref"]), p["pm_len"], p["pm_mult"])

    pm = _trim_first_bar(df, p["first_bar_date"])
    pink = np.full(n, np.nan)
    if len(pm):
        st = pink_mirror.step_series(
            _f(pm, "high"), _f(pm, "low"), _f(pm, "close"), _f(pm, p["step_src"]),
            p["step_width"], p["atr_len"], p["atr_hi"], p["start_bar"], last_bar_confirmed)
        pink[n - len(pm):] = st

    src, close = _f(df, p["nwe_src"]), _f(df, "close")
    # Show whether upperpink has enough bars for both its seed and ATR-highest window.
    first_bar = pd.Timestamp(p["first_bar_date"]) if p.get("first_bar_date") else None
    pm_offset = int(np.searchsorted(pd.to_datetime(df["datetime"]).values,
                                    first_bar.to_datetime64(), side="left")) if first_bar is not None else 0
    seed_required = int(p["start_bar"]) + 1
    atr_required = int(p["atr_len"]) + int(p["atr_hi"]) - 1
    pink_status = []
    for bar_index in range(n):
        if bar_index < pm_offset:
            pink_status.append("BEFORE START DATE")
            continue
        bars_available = bar_index - pm_offset + 1
        if bars_available < seed_required:
            pink_status.append(f"SEED WARMUP {bars_available}/{seed_required}")
        elif bars_available < atr_required:
            pink_status.append(f"ATR WARMUP {bars_available}/{atr_required}")
        else:
            pink_status.append("ATR READY")

    rows = []
    for i in range(n - days, n):
        buy, sell = nadaraya_watson.nwe_last_bar_signal(
            src[: i + 1], close[: i + 1], p["nwe_h"], p["nwe_mult"], p["nwe_repaint"])
        r = df.iloc[i]
        rows.append([r["datetime"].strftime("%d/%m/%Y"),
                     float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]),
                     float(r["volume"]), bb_u[i], bb_l[i], bool(buy), bool(sell), pink[i], mirror[i],
                     pink_status[i]])
    out = pd.DataFrame(rows, columns=TABLE_COLUMNS)
    return out.iloc[::-1].reset_index(drop=True)
