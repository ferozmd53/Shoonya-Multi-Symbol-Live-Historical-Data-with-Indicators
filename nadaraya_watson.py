"""Nadaraya-Watson Envelope [LuxAlgo] -> TRUE/FALSE buy/sell on the latest DAILY bar.

Python port of the LuxAlgo Pine v5 indicator, with the user's inputs:
    H = 3.0, MULT = 0.9, SRC = open, REPAINT = True

REPAINT = True  (Pine "Repainting Smoothing" ON, the default)
    barstate.islast block: the kernel is re-computed over the last 500 bars,
        nwe[i] = sum_j src[j]*gauss(i-j) / sum_j gauss(i-j)     (i, j = bars back, 0 = latest)
        sae    = sum_i |src[i]-nwe[i]| / min(499, n-1) * MULT
    arrows on the latest bar (i = 0):
        SELL  src[0] > nwe[0]+sae  and  src[1] < nwe[0]+sae
        BUY   src[0] < nwe[0]-sae  and  src[1] > nwe[0]-sae

REPAINT = False  (end-point method)
    out = one-sided Gaussian-weighted mean of the last 500 bars,
    mae = sma(|src-out|, 499) * MULT,
    BUY  = crossunder(close, out-mae),  SELL = crossover(close, out+mae)

Needs >= 501 daily bars to behave exactly like a TradingView chart (end-point
mode needs ~1000 bars before its bands exist, as in Pine).
"""
from functools import lru_cache

import numpy as np

H = 3.0
MULT = 0.9
SRC = "open"
REPAINT = True
WINDOW = 500  # Pine loops i = 0 .. 499


def gauss(x, h):
    return np.exp(-(np.power(x, 2) / (h * h * 2)))


@lru_cache(maxsize=8)
def _kernel(m, h):
    """Row-normalised Gaussian matrix K[i, j] = gauss(i-j) / sum_j gauss(i-j)."""
    idx = np.arange(m + 1)
    w = gauss(idx[:, None] - idx[None, :], h)
    return w / w.sum(axis=1, keepdims=True)


def _repaint_signal(src, h, mult):
    s_all = np.asarray(src, dtype=float)[::-1]          # index 0 = latest bar
    n = len(s_all) - 1                                  # Pine bar_index of the last bar
    m = min(WINDOW - 1, n - 1)
    if m < 1:
        return False, False
    s = s_all[: m + 1]
    nwe = _kernel(m, float(h)) @ s
    sae = np.abs(s - nwe).sum() / m * mult
    upper0, lower0 = nwe[0] + sae, nwe[0] - sae
    older = s_all[1]                                    # src[1] = previous (older) bar
    sell = bool(s[0] > upper0 and older < upper0)
    buy = bool(s[0] < lower0 and older > lower0)
    return buy, sell


def _endpoint_signal(src, close, h, mult):
    src = np.asarray(src, dtype=float)
    close = np.asarray(close, dtype=float)
    if len(src) < WINDOW + WINDOW:                       # bands need ~1000 bars
        return False, False
    coefs = gauss(np.arange(WINDOW), h)
    out = np.convolve(src, coefs)[: len(src)] / coefs.sum()
    out[: WINDOW - 1] = np.nan
    err = np.abs(src - out)
    k = WINDOW - 1
    # last two values of the 499-bar SMA of |src-out| (NaN-aware like Pine's na)
    mae_now = err[-k:].mean() * mult
    mae_prev = err[-k - 1:-1].mean() * mult
    up_now, lo_now = out[-1] + mae_now, out[-1] - mae_now
    up_prev, lo_prev = out[-2] + mae_prev, out[-2] - mae_prev
    if np.isnan([up_now, lo_now, up_prev, lo_prev]).any():
        return False, False
    buy = bool(close[-1] < lo_now and close[-2] >= lo_prev)
    sell = bool(close[-1] > up_now and close[-2] <= up_prev)
    return buy, sell


def nwe_last_bar_signal(src, close=None, h=H, mult=MULT, repaint=REPAINT):
    """Return (nadarya_buy, nadarya_sell) booleans for the LATEST bar.

    src   : chronological list/array of the chosen source (open) incl. the latest bar
    close : chronological closes incl. the latest bar (only used when repaint=False)
    """
    if src is None or len(src) < 3:
        return False, False
    if repaint:
        return _repaint_signal(src, h, mult)
    if close is None:
        return False, False
    return _endpoint_signal(src, close, h, mult)
