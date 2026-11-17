"""upperpink (Price Step Channel step line) + mirrorUpperBand  - Pine port, DAILY bars.

Settings (from the user's TradingView chart)
    Mirror band : length 5, mult -0.3, reference = open, SMA source = low
        sma5            = sma(low, 5)
        std5            = stdev(low, 5)                 (population, ddof = 0)
        realUpperBand   = sma5 - mult * std5
        mirrorUpperBand = 2 * open - realUpperBand
    Step channel: width 0.7, source = low, ATR 200, highest 100, seed bar 200
        atr  = highest(atr(200), 100) * 0.7             (Wilder RMA ATR, like ta.atr)
        up   = st[1] + atr ,  lw = st[1] - atr
        st   = src at bar 200, then jumps to src when a CONFIRMED bar breaks out of
               [lw, up]; otherwise keeps st[1].       -> plotted as "upperpink"

The step only moves on confirmed bars (barstate.isconfirmed), so on the live
daily candle `upperpink` equals the value after the last completed day.
"""
import numpy as np

# ─── Mirror band inputs ──────────────────────────────────────────────
LEN = 5
MULT = -0.3
REF_COL = "open"      # refSource
SMA_COL = "low"       # smaSource

# ─── Step channel inputs ─────────────────────────────────────────────
STEP_WIDTH = 0.7
STEP_SRC = "low"
ATR_LEN = 200
ATR_HIGHEST = 100
START_BAR = 200       # Pine: bar_index == 200


def rma(x, length):
    """Pine ta.rma: seeded with the SMA of the first `length` values, alpha = 1/length."""
    out = np.full(len(x), np.nan)
    if len(x) < length:
        return out
    out[length - 1] = x[:length].mean()
    a = 1.0 / length
    for i in range(length, len(x)):
        out[i] = a * x[i] + (1 - a) * out[i - 1]
    return out


def pine_atr(high, low, close, length):
    high, low, close = (np.asarray(v, dtype=float) for v in (high, low, close))
    pc = np.roll(close, 1)
    tr = np.maximum.reduce([high - low, np.abs(high - pc), np.abs(low - pc)])
    tr[0] = high[0] - low[0]                     # Pine ta.tr on the first bar
    return rma(tr, length)


def step_series(high, low, close, src, width=STEP_WIDTH, atr_len=ATR_LEN,
                atr_hi=ATR_HIGHEST, start_bar=START_BAR, last_bar_confirmed=True):
    """Step line (`upperpink`) on EVERY bar (all nan if there are not enough bars).

    The step is causal (bar i only uses bars 0..i), so value i equals what the indicator
    showed on bar i itself.  last_bar_confirmed=False -> the last candle is still forming
    and cannot move the step.
    """
    src = np.asarray(src, dtype=float)
    n = len(src)
    st = np.full(n, np.nan)
    if n <= start_bar:
        return st
    atr = pine_atr(high, low, close, atr_len)
    # highest(atr, atr_hi) * width  (nan until a full window of valid ATR values exists)
    wide = np.full(n, np.nan)
    for i in range(atr_len - 1 + atr_hi - 1, n):
        wide[i] = atr[i - atr_hi + 1: i + 1].max() * width

    for i in range(n):
        st1 = st[i - 1] if i > 0 else np.nan
        up, lw = st1 + wide[i], st1 - wide[i]       # nan propagates like Pine na
        confirmed = True if i < n - 1 else last_bar_confirmed
        if i == start_bar:
            st[i] = src[i]
        elif confirmed and src[i] > up:
            st[i] = src[i]
        elif confirmed and src[i] < lw:
            st[i] = src[i]
        else:
            st[i] = st1
    return st


def step_last(high, low, close, src, width=STEP_WIDTH, atr_len=ATR_LEN,
              atr_hi=ATR_HIGHEST, start_bar=START_BAR, last_bar_confirmed=True):
    """Step line (`upperpink`) value on the LAST bar (nan if not available)."""
    st = step_series(high, low, close, src, width, atr_len, atr_hi, start_bar, last_bar_confirmed)
    return float(st[-1]) if len(st) else float("nan")


def mirror_series(sma_src, ref, length=LEN, mult=MULT):
    """mirrorUpperBand on every bar (nan until `length` bars exist)."""
    import pandas as pd
    s = pd.Series(np.asarray(sma_src, dtype=float))
    real_upper = s.rolling(length).mean() - mult * s.rolling(length).std(ddof=0)
    return (2.0 * np.asarray(ref, dtype=float) - real_upper.values)


def mirror_last(sma_src, ref, length=LEN, mult=MULT):
    """mirrorUpperBand on the latest bar.

    sma_src : source values (low) INCLUDING the latest bar (for a live bar: today's low so far)
    ref     : reference price of the latest bar (open)
    """
    vals = np.asarray(sma_src, dtype=float)
    if len(vals) < length or ref is None:
        return float("nan")
    w = vals[-length:]
    real_upper = w.mean() - mult * w.std()           # ddof = 0 like Pine
    return float(2.0 * ref - real_upper)
