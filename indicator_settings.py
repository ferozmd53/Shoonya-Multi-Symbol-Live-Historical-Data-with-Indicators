"""Indicator settings typed in Excel: sheet LOGIN, column M (name) / N (value) / O (note).

    M1 INDICATOR SETTINGS | N1 VALUE | O1 NOTE
    M2 DAYS_TO_DOWNLOAD   | N2 1500  | ...

* ensure_block(ws)   writes the block with defaults - NEVER overwrites a value you typed
* read_settings(ws)  reads + validates; a bad value falls back to its default (with a warning)
* apply_to(cfg, v)   copies the values onto the script's Config class
"""
from datetime import date, datetime

NAME_COL, VALUE_COL, NOTE_COL = "M", "N", "O"
FIRST_ROW, LAST_ROW = 2, 60
STATUS_KEY = "STATUS"

# (key, default, kind, note, Config attribute)
#   kind: ("int", min, max) | ("float", min, max) | ("choice", [..]) | "bool" | "date_opt"
OHLC = ["open", "high", "low", "close"]
SPEC = [
    ("DAYS_TO_DOWNLOAD", 1500, ("int", 200, 8000),
     "calendar days of daily candles to load (raised automatically if PINK_FIRST_BAR_DATE needs more)", "LOAD_DAYS"),
    ("USE_SQLITE_CACHE", True, "bool",
     "YES = keep daily candles in daily_candles.db and only download the last few days", "USE_CANDLE_CACHE"),
    ("PINK_FIRST_BAR_DATE", "2023-01-02", "date_opt",
     "date of the FIRST bar on your TradingView daily chart (the step line is seeded from it); blank = all bars",
     "PM_FIRST_BAR_DATE"),
    ("NWE_BANDWIDTH", 3.0, ("float", 0.01, 1000), "Nadaraya-Watson Bandwidth (h)", "NWE_H"),
    ("NWE_MULT", 0.9, ("float", 0.0, 100), "Nadaraya-Watson mult", "NWE_MULT"),
    ("NWE_SOURCE", "open", ("choice", OHLC), "Nadaraya-Watson source: open / high / low / close", "NWE_SRC"),
    ("NWE_REPAINT", True, "bool", "TRUE = Repainting Smoothing (TradingView default)", "NWE_REPAINT"),
    ("MIRROR_LENGTH", 5, ("int", 1, 500), "mirror band: SMA / STDEV length", "PM_LEN"),
    ("MIRROR_MULT", -0.3, ("float", -100, 100), "mirror band: multiplier", "PM_MULT"),
    ("MIRROR_REF", "open", ("choice", OHLC), "mirror band: reference price", "PM_REF"),
    ("MIRROR_SMA_SOURCE", "low", ("choice", OHLC), "mirror band: SMA source", "PM_SMA_COL"),
    ("STEP_WIDTH", 0.7, ("float", 0.0, 100), "step line (upperpink): width", "PM_STEP_WIDTH"),
    ("STEP_SOURCE", "low", ("choice", OHLC), "step line (upperpink): source", "PM_STEP_SRC"),
    ("ATR_LENGTH", 200, ("int", 1, 2000), "step line: ATR length", "PM_ATR_LEN"),
    ("ATR_HIGHEST", 100, ("int", 1, 2000), "step line: highest of ATR over N bars", "PM_ATR_HI"),
    ("STEP_START_BAR", 200, ("int", 0, 5000), "step line: seed bar (Pine bar_index == 200)", "PM_START_BAR"),
    ("BB_LENGTH", 20, ("int", 2, 500), "Bollinger Bands length", "BB_LENGTH"),
    ("BB_STD", 2.0, ("float", 0.1, 20), "Bollinger Bands standard deviations", "BB_STD"),
    ("ORDER_BUFFER_PCT", 0.5, ("float", 0.0, 10),
     "Shoonya blocks API market orders: orders go as a LIMIT at LTP +0.5% (BUY) / -0.5% (SELL) so they fill at once",
     "ORDER_BUFFER_PCT"),
    ("AS_OF_DATE", "", "date_opt",
     "blank = latest. A past date (e.g. 2026-11-10): the HISTORICAL block shows that day's candle + indicators",
     "AS_OF_DATE"),
]
DEFAULTS = {k: d for k, d, *_ in SPEC}
ATTR = {k: a for k, _, _, _, a in SPEC}
# changing these needs the candles (re)loaded; everything else is just recalculated
DATA_KEYS = {"DAYS_TO_DOWNLOAD", "USE_SQLITE_CACHE", "PINK_FIRST_BAR_DATE", "NWE_REPAINT"}


def _blank(v):
    return v is None or (isinstance(v, str) and not v.strip())


def _parse_date(v):
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    text = str(v).strip()
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d-%b-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(text[:11] if fmt == "%d-%b-%Y" else text[:10], fmt).date()
        except ValueError:
            continue
    raise ValueError(f"'{text}' is not a date (use YYYY-MM-DD)")


def _parse(kind, v, default):
    """Return the validated value; raise ValueError with a readable reason."""
    if kind == "bool":
        if isinstance(v, bool):
            return v
        t = str(v).strip().upper()
        if t in ("TRUE", "YES", "Y", "1", "ON"):
            return True
        if t in ("FALSE", "NO", "N", "0", "OFF"):
            return False
        raise ValueError(f"'{v}' is not TRUE/FALSE")
    if kind == "date_opt":
        return None if _blank(v) else _parse_date(v)
    if isinstance(kind, tuple):
        if kind[0] == "choice":
            t = str(v).strip().lower()
            if t not in kind[1]:
                raise ValueError(f"'{v}' must be one of {', '.join(kind[1])}")
            return t
        lo, hi = kind[1], kind[2]
        try:
            num = float(v)
        except (TypeError, ValueError):
            raise ValueError(f"'{v}' is not a number")
        if num != num:
            raise ValueError("not a number")
        if kind[0] == "int":
            if abs(num - round(num)) > 1e-9:
                raise ValueError(f"'{v}' must be a whole number")
            num = int(round(num))
        if not lo <= num <= hi:
            raise ValueError(f"{num} is outside {lo}..{hi}")
        return num
    raise ValueError("unknown setting type")


def _default_value(key):
    spec = next(s for s in SPEC if s[0] == key)
    d = spec[1]
    return _parse(spec[2], d, d)


def _cell_value(value):
    """What to type into Excel for a default."""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    return value


def _scan(ws):
    """{key: row} for the rows currently in column M, plus the first empty row."""
    names = ws.range(f"{NAME_COL}{FIRST_ROW}:{NAME_COL}{LAST_ROW}").value or []
    if not isinstance(names, list):
        names = [names]
    rows, last_used = {}, FIRST_ROW - 1
    for i, name in enumerate(names):
        if not _blank(name):
            rows[str(name).strip().upper()] = FIRST_ROW + i
            last_used = FIRST_ROW + i
    return rows, last_used + 1


def ensure_block(ws):
    """Create the header and any missing settings rows (existing values are never touched)."""
    ws.range(f"{NAME_COL}1:{NOTE_COL}1").value = ["INDICATOR SETTINGS", "VALUE", "NOTE"]
    rows, nxt = _scan(ws)
    for key, default, kind, note, _attr in SPEC:
        if key in rows:
            continue
        ws.range(f"{NAME_COL}{nxt}:{NOTE_COL}{nxt}").value = [key, _cell_value(default), note]
        rows[key] = nxt
        nxt += 1
    if STATUS_KEY not in rows:
        ws.range(f"{NAME_COL}{nxt}:{NOTE_COL}{nxt}").value = [
            STATUS_KEY, "", "written by the script - changes here are applied within a few seconds"]
    return rows


def read_settings(ws):
    """-> (values dict, warnings list).  Bad / empty values fall back to the default."""
    rows, _ = _scan(ws)
    block = ws.range(f"{NAME_COL}{FIRST_ROW}:{VALUE_COL}{LAST_ROW}").value or []
    raw = {}
    for name, val in block:
        if not _blank(name):
            raw[str(name).strip().upper()] = val
    values, warnings = {}, []
    for key, default, kind, _note, _attr in SPEC:
        v = raw.get(key)
        if _blank(v) and kind != "date_opt":
            values[key] = _default_value(key)
            if key in raw:
                warnings.append(f"{key} is empty - using {default}")
            continue
        try:
            values[key] = _parse(kind, v, default)
        except ValueError as exc:
            values[key] = _default_value(key)
            warnings.append(f"{key}: {exc} - using {default if default != '' else 'blank'}")
    return values, warnings


def apply_to(cfg, values):
    """Copy validated settings onto the Config class."""
    for key, value in values.items():
        attr = ATTR[key]
        if key == "PINK_FIRST_BAR_DATE":
            value = value.isoformat() if value else None
        setattr(cfg, attr, value)


def write_status(ws, text):
    rows, _ = _scan(ws)
    row = rows.get(STATUS_KEY)
    if row:
        ws.range(f"{VALUE_COL}{row}").value = text
