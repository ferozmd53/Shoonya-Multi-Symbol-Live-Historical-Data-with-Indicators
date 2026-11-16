"""SQLite cache for DAILY candles (+ a table with the saved daily indicator values).

Why: every start-up used to download ~1,400 calendar days of daily candles for EVERY
symbol from Shoonya.  With this cache the full window is downloaded ONCE; later runs
only ask Shoonya for the last few days (or nothing at all when the cache already holds
the latest completed trading day).

Database file (default: beside the Excel workbook) - 3 tables
    candles     exchange, symbol, date, open, high, low, close, volume
                (only COMPLETED days are stored, never today's forming bar)
    candle_meta exchange, symbol, covered_from, full_fetched_on, last_checked_on
    indicators  exchange, symbol, date, upperpink, mirror_upper_band, nadarya_buy,
                nadarya_sell, bb_upper, bb_lower, saved_at     (one row per symbol/day)

Safety rules
    * the first run (or a longer history window than cached) does a FULL download
    * incremental downloads re-fetch `overlap_days` already cached days and compare the
      closes: if they differ (split / bonus / corporate action adjusted history) the
      symbol is re-downloaded in full
    * a full re-download is also done every `full_refresh_days`
    * if Shoonya returns nothing, the cached candles are used (works offline too)
"""
import sqlite3
from contextlib import closing
from datetime import date, datetime, timedelta

_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
_MONTH_NO = {m.upper(): i + 1 for i, m in enumerate(_MONTHS)}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS candles (
    exchange TEXT NOT NULL, symbol TEXT NOT NULL, date TEXT NOT NULL,
    open REAL, high REAL, low REAL, close REAL, volume REAL,
    PRIMARY KEY (exchange, symbol, date));
CREATE TABLE IF NOT EXISTS candle_meta (
    exchange TEXT NOT NULL, symbol TEXT NOT NULL,
    covered_from TEXT, full_fetched_on TEXT, last_checked_on TEXT,
    PRIMARY KEY (exchange, symbol));
CREATE TABLE IF NOT EXISTS indicators (
    exchange TEXT NOT NULL, symbol TEXT NOT NULL, date TEXT NOT NULL,
    upperpink REAL, mirror_upper_band REAL, nadarya_buy INTEGER, nadarya_sell INTEGER,
    bb_upper REAL, bb_lower REAL, saved_at TEXT,
    PRIMARY KEY (exchange, symbol, date));
"""


def _connect(path):
    con = sqlite3.connect(str(path), timeout=15)
    con.executescript(_SCHEMA)
    return con


# ─── raw Shoonya row <-> (date, o, h, l, c, v) ───────────────────────
def _parse_date(text):
    day, mon, year = str(text).strip().split("-")
    return date(int(year), _MONTH_NO[mon.upper()], int(day))


def parse_raw(item):
    """Shoonya daily row {'time': '13-Nov-2026', 'into', 'inth', 'intl', 'intc', 'intv'}
    -> (date, open, high, low, close, volume) or None if the row is unusable."""
    try:
        d = _parse_date(item["time"])
        vals = [float(item[k]) for k in ("into", "inth", "intl", "intc", "intv")]
    except Exception:
        return None
    if any(v != v for v in vals):
        return None
    return (d, *vals)


def to_raw(row):
    d, o, h, l, c, v = row
    return {
        "time": f"{d.day:02d}-{_MONTHS[d.month - 1]}-{d.year}",
        "into": repr(o), "inth": repr(h), "intl": repr(l), "intc": repr(c), "intv": repr(v),
    }


# ─── storage helpers ─────────────────────────────────────────────────
def _load(con, exchange, symbol, from_date):
    cur = con.execute(
        "SELECT date, open, high, low, close, volume FROM candles "
        "WHERE exchange=? AND symbol=? AND date>=? ORDER BY date",
        (exchange, symbol, from_date.isoformat()))
    return [(date.fromisoformat(r[0]), *r[1:]) for r in cur.fetchall()]


def _save(con, exchange, symbol, rows, today):
    """Store completed days only (never today's forming bar)."""
    data = [(exchange, symbol, r[0].isoformat(), *r[1:]) for r in rows if r[0] < today]
    if data:
        con.executemany("INSERT OR REPLACE INTO candles VALUES (?,?,?,?,?,?,?,?)", data)


def _meta(con, exchange, symbol):
    cur = con.execute("SELECT covered_from, full_fetched_on FROM candle_meta WHERE exchange=? AND symbol=?",
                      (exchange, symbol))
    r = cur.fetchone()
    if not r or not r[0] or not r[1]:
        return None
    return date.fromisoformat(r[0]), date.fromisoformat(r[1])


def _prev_business_day(today):
    d = today - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def _parse_all(raw_items):
    rows = {}
    for item in raw_items or []:
        r = parse_raw(item)
        if r:
            rows[r[0]] = r
    return rows


# ─── main entry point ────────────────────────────────────────────────
def get_daily(db_path, fetch_fn, exchange, symbol, window_start, today=None,
              overlap_days=10, full_refresh_days=30, skip_if_fresh=True):
    """Return (raw_rows, info) - raw rows in the same format Shoonya returns - or (None, info).

    fetch_fn(from_date, to_datetime) -> list of raw Shoonya rows (may be empty).
    window_start: first calendar day of history the indicators need.
    """
    today = today or date.today()
    now = datetime.combine(today, datetime.now().time())
    with closing(_connect(db_path)) as con:
        meta = _meta(con, exchange, symbol)
        cached = _load(con, exchange, symbol, window_start)

        def full(reason):
            fetched = _parse_all(fetch_fn(window_start, now))
            if not fetched:
                if cached:
                    return [to_raw(r) for r in cached], f"cache only - Shoonya returned nothing ({len(cached)} bars)"
                return None, "no data"
            con.execute("DELETE FROM candles WHERE exchange=? AND symbol=? AND date>=?",
                        (exchange, symbol, window_start.isoformat()))
            rows = sorted(fetched.values())
            _save(con, exchange, symbol, rows, today)
            con.execute("INSERT OR REPLACE INTO candle_meta VALUES (?,?,?,?,?)",
                        (exchange, symbol, window_start.isoformat(), today.isoformat(), today.isoformat()))
            con.commit()
            return [to_raw(r) for r in rows], f"full download {len(rows)} bars ({reason})"

        if (meta is None or not cached or meta[0] > window_start
                or (today - meta[1]).days >= full_refresh_days):
            reason = ("first run" if meta is None or not cached else
                      "longer history needed" if meta[0] > window_start else "monthly refresh")
            return full(reason)

        last_cached = cached[-1][0]
        if skip_if_fresh and last_cached >= _prev_business_day(today):
            return [to_raw(r) for r in cached], f"cache {len(cached)} bars, no download needed"

        from_date = max(window_start, last_cached - timedelta(days=overlap_days))
        fetched = _parse_all(fetch_fn(from_date, now))
        if not fetched:
            return [to_raw(r) for r in cached], f"cache {len(cached)} bars (Shoonya returned nothing)"

        old = {r[0]: r for r in cached}
        for d, r in fetched.items():
            if d < today and d in old and old[d][4] and abs(r[4] / old[d][4] - 1.0) > 1e-4:
                return full("price history changed - split/bonus")

        new_rows = [r for d, r in fetched.items() if d not in old and d < today]
        _save(con, exchange, symbol, list(fetched.values()), today)
        con.execute("UPDATE candle_meta SET last_checked_on=? WHERE exchange=? AND symbol=?",
                    (today.isoformat(), exchange, symbol))
        con.commit()
        merged = dict(old)
        merged.update(fetched)                      # fetched wins; may include today's forming bar
        rows = [merged[d] for d in sorted(merged)]
        return [to_raw(r) for r in rows], f"cache + {len(new_rows)} new bars"


def save_indicators(db_path, exchange, symbol, bar_date, values):
    """Store one row of daily indicator values (upperpink, mirror_upper_band, ...)."""
    def num(x):
        try:
            x = float(x)
            return None if x != x else x
        except (TypeError, ValueError):
            return None

    with closing(_connect(db_path)) as con:
        con.execute(
            "INSERT OR REPLACE INTO indicators VALUES (?,?,?,?,?,?,?,?,?,?)",
            (exchange, symbol, bar_date.isoformat(),
             num(values.get("upperpink")), num(values.get("mirror_upper_band")),
             int(bool(values.get("nadarya_buy"))), int(bool(values.get("nadarya_sell"))),
             num(values.get("bb_upper")), num(values.get("bb_lower")),
             datetime.now().isoformat(timespec="seconds")))
        con.commit()
