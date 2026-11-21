# Extreme_Reversal_Signal.py - FIXED DATA POSITIONS

# ─── NorenApi import with automatic fallback ───
# Prefers NorenRestApiOAuth (real package — correctly formats jData).
# Falls back to NorenRestApiPy + a shim that mimics NorenRestApiOAuth's
# raw-string jData payload (NOT a URL-encoded dict).
try:
    from NorenRestApiOAuth.NorenApi import NorenApi
    if not hasattr(NorenApi, "getAccessToken"):
        raise ImportError("getAccessToken missing")
    print("✅ Using NorenRestApiOAuth (real package)")
except Exception:
    try:
        from NorenRestApiPy.NorenApi import NorenApi as _BaseNorenApi
        import requests as _req
        import json as _json
        import hashlib as _hashlib

        class NorenApi(_BaseNorenApi):
            """NorenRestApiPy with getAccessToken that mimics NorenRestApiOAuth exactly."""

            def getAccessToken(self, auth_code, secret_key, client_id, uid):
                """Exchange a fresh Shoonya OAuth code with its required SHA256 checksum."""
                if not all(str(v or "").strip() for v in (auth_code, secret_key, client_id, uid)):
                    print("❌ OAuth exchange stopped: auth code or API credentials are blank")
                    return None

                url = "https://api.shoonya.com/NorenWClientAPI/GenAcsTok"
                checksum = _hashlib.sha256(
                    (str(client_id).strip() + str(secret_key).strip() + str(auth_code).strip()).encode("utf-8")
                ).hexdigest()
                payload = "jData=" + _json.dumps({
                    "code": str(auth_code).strip(),
                    "checksum": checksum,
                })

                try:
                    response = _req.post(
                        url,
                        data=payload,
                        headers={"Content-Type": "application/x-www-form-urlencoded"},
                        timeout=30,
                    )
                    ret = response.json()
                except Exception as exc:
                    print(f"❌ getAccessToken HTTP error: {exc}")
                    return None

                if ret.get("stat") == "Ok" and (ret.get("access_token") or ret.get("susertoken")):
                    access_token = ret.get("access_token") or ret.get("susertoken")
                    returned_uid = ret.get("uid") or ret.get("USERID") or uid
                    actid = ret.get("actid")
                    refresh_token = ret.get("refresh_token")
                    try:
                        self._NorenApi__access_token = access_token
                        self._NorenApi__username = returned_uid
                        self._NorenApi__accountid = actid
                        self._NorenApi__susertoken = ret.get("susertoken", access_token)
                    except Exception:
                        pass
                    self.access_token = access_token
                    self.username = returned_uid
                    self.accountid = actid
                    print(f"✅ getAccessToken OK (uid={returned_uid})")
                    return (access_token, returned_uid, refresh_token, actid)

                print(f"❌ getAccessToken failed: {ret}")
                return None

            def injectOAuthHeader(self, token, uid, actid=None):
                try:
                    self._NorenApi__susertoken = token
                    self._NorenApi__access_token = token
                    self._NorenApi__username = uid
                    self._NorenApi__accountid = actid or uid
                    self._NorenApi__OAuthHeaders = {
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json; charset=utf-8",
                    }
                except Exception:
                    pass
                self.access_token = token
                self.username     = uid
                self.accountid    = actid or uid
                print(f"✅ injectOAuthHeader OK (uid={uid})")

            def place_order(
                self, buy_or_sell, product_type, exchange, tradingsymbol,
                quantity, discloseqty, price_type, price=0.0,
                trigger_price=None, retention="DAY", amo=None, remarks=None,
                bookloss_price=0.0, bookprofit_price=0.0, trail_price=0.0,
                algo_id=None
            ):
                """OAuth-compatible PlaceOrder for the legacy NorenRestApiPy fallback."""
                config = self._NorenApi__service_config
                url = f"{config['host']}{config['routes']['placeorder']}"
                values = {
                    "ordersource": "API",
                    "uid": self._NorenApi__username,
                    "actid": self._NorenApi__accountid or self._NorenApi__username,
                    "trantype": getattr(buy_or_sell, "value", buy_or_sell),
                    "prd": getattr(product_type, "value", product_type),
                    "exch": exchange,
                    "tsym": tradingsymbol,
                    "qty": str(quantity),
                    "dscqty": str(discloseqty),
                    "prctyp": getattr(price_type, "value", price_type),
                    "prc": str(price),
                    "trgprc": str(trigger_price if trigger_price is not None else 0),
                    "ret": retention,
                    "remarks": remarks,
                    "algo_id": algo_id,
                }
                if amo is not None:
                    values["amo"] = amo
                if product_type in ("H", "B"):
                    values["blprc"] = str(bookloss_price)
                if product_type == "B":
                    values["bpprc"] = str(bookprofit_price)
                if trail_price:
                    values["trailprc"] = str(trail_price)

                payload = "jData=" + _json.dumps(values)
                headers = getattr(self, "_NorenApi__OAuthHeaders", None)
                if headers:
                    response = _req.post(url, data=payload, headers=headers, timeout=30)
                else:
                    token = getattr(self, "_NorenApi__susertoken", "")
                    response = _req.post(
                        url, data=payload + "&jKey=" + token, timeout=30
                    )
                response.raise_for_status()
                try:
                    return response.json()
                except Exception:
                    return {"stat": "Not_Ok", "emsg": response.text}

            # Override the legacy NorenRestApiPy WebSocket callbacks.
            # The legacy package sends t="c" with pwd/susertoken; OAuth requires
            # t="a" with accesstoken and an ak/ck connection acknowledgement.
            def _NorenApi__on_open_callback(self, ws=None):
                self._NorenApi__websocket_connected = True
                payload = {
                    "t": "a",
                    "uid": self._NorenApi__username,
                    "actid": self._NorenApi__accountid or self._NorenApi__username,
                    "source": "API",
                    "accesstoken": self._NorenApi__access_token,
                }
                self._NorenApi__ws_send(_json.dumps(payload))

            def _NorenApi__on_data_callback(
                self, ws=None, message=None, data_type=None, continue_flag=None
            ):
                try:
                    data = _json.loads(message) if isinstance(message, str) else message
                    if not isinstance(data, dict):
                        return
                    msg_type = data.get("t")
                    if msg_type in ("ak", "ck"):
                        if str(data.get("s", "")).strip().lower() in ("ok", "success"):
                            callback = self._NorenApi__on_open
                            if callback:
                                callback()
                        else:
                            error_callback = self._NorenApi__on_error
                            if error_callback:
                                error_callback(data)
                            else:
                                print(f"❌ Shoonya WebSocket authentication rejected: {data}")
                        return
                    if msg_type in ("tk", "tf", "dk", "df"):
                        callback = self._NorenApi__subscribe_callback
                        if callback:
                            callback(data)
                        return
                    if msg_type == "om":
                        callback = self._NorenApi__order_update_callback
                        if callback:
                            callback(data)
                        return
                except Exception as e:
                    error_callback = self._NorenApi__on_error
                    if error_callback:
                        error_callback(e)
                    else:
                        print(f"❌ WebSocket message error: {e}")

        print("⚠️  Using NorenRestApiPy with jData-raw-string shim")
        print("   For the real package: pip install --no-cache-dir --no-deps NorenRestApiOAuth")
    except ImportError:
        raise ImportError(
            "Install one of:\n"
            "  pip install --no-cache-dir --no-deps NorenRestApiOAuth\n"
            "  pip install --no-cache-dir --no-deps NorenRestApiPy"
        )

import time
import datetime
from datetime import datetime, timedelta
import numpy as np
import json
import xlwings as xw
import pandas as pd
import string
import warnings
from pathlib import Path
import sys
import threading
import queue
from concurrent.futures import ThreadPoolExecutor
from instrument_master import InstrumentMaster, SUPPORTED_EXCHANGES
from Weekly import WeeklyDataEngine
warnings.filterwarnings('ignore')

# ============================================
# GLOBAL VARIABLES
# ============================================

# Locate the workbook beside run.py / in the working folder first.
# The installed package may live in Python\\Lib\\site-packages, so do not
# assume the workbook is stored beside this module.
WORKBOOK_NAME = "VII_CLASS.xlsm"
RUN_FOLDER = Path(sys.argv[0]).resolve().parent if sys.argv and sys.argv[0] else Path.cwd()
PACKAGE_FOLDER = Path(__file__).resolve().parent

_workbook_candidates = [
    RUN_FOLDER / WORKBOOK_NAME,
    RUN_FOLDER / "CLASS_VII" / WORKBOOK_NAME,
    Path.cwd() / WORKBOOK_NAME,
    Path.cwd() / "CLASS_VII" / WORKBOOK_NAME,
    PACKAGE_FOLDER / WORKBOOK_NAME,
]

WORKBOOK_PATH = next((p for p in _workbook_candidates if p.is_file()), None)
if WORKBOOK_PATH is None:
    searched = "\\n".join(f"  - {p}" for p in _workbook_candidates)
    raise FileNotFoundError(
        f"Excel workbook '{WORKBOOK_NAME}' was not found. Searched:\\n{searched}\\n"
        "Place the workbook beside run.py or in the CLASS_VII folder."
    )

print(f"📘 Excel workbook: {WORKBOOK_PATH}")
excel_name = xw.Book(str(WORKBOOK_PATH))

def _ensure_sheet(sheet_name):
    try:
        return excel_name.sheets[sheet_name]
    except Exception:
        sheet = excel_name.sheets.add(sheet_name)
        if sheet_name == "LOGIN":
            sheet.range("A1:B11").value = [
                ["Field", "Value"], ["CLIENT_ID", ""], ["USER_ID", ""],
                ["PASSWORD", ""], ["TOTP_SECRET", ""], ["API_SECRET", ""],
                ["AUTH_CODE", ""], ["", ""], ["", ""], ["", ""], ["IP_ADDRESS", ""],
            ]
        elif sheet_name == "symbols":
            sheet.range("A1").value = "Symbol"
        return sheet

_ensure_sheet("LOGIN")
_ensure_sheet("symbols")
instrument_master = InstrumentMaster()
api = None
weekly_data_engine = WeeklyDataEngine()
feed_opened = False
live_data = {}
symbol_tokens = {}
token_symbols = {}
historical_data_cache = {}
order_history = []
pending_order_updates = []
order_update_lock = threading.Lock()
order_result_queue = queue.Queue()
order_executor = ThreadPoolExecutor(max_workers=20, thread_name_prefix="ShoonyaOrder")
order_inflight = set()
order_inflight_lock = threading.Lock()
order_reconcile_future = None
last_order_reconcile = 0
last_order_signal_scan = 0
tick_count = 0
last_symbol_check = 0
last_excel_update = 0
last_indicator_excel_update = 0
last_hist_excel_update = 0

# ============================================
# COLUMN UTILITY FUNCTIONS
# ============================================

def get_column_letter(col_num):
    """Convert column number to letter (1=A, 2=B, etc.)"""
    result = ""
    while col_num > 0:
        col_num -= 1
        result = chr(65 + col_num % 26) + result
        col_num //= 26
    return result

def get_column_number(col_letter):
    """Convert column letter to number (A=1, B=2, etc.)"""
    col_letter = col_letter.upper()
    result = 0
    for char in col_letter:
        result = result * 26 + (ord(char) - ord('A') + 1)
    return result

def get_column_range(start_col, count):
    """Get list of column letters starting from start_col for 'count' columns"""
    start_num = get_column_number(start_col)
    cols = []
    for i in range(count):
        cols.append(get_column_letter(start_num + i))
    return cols

def get_live_headers():
    """Get live column headers"""
    return [
        'LTP', 'Open', 'High', 'Low', 'Close', 'Volume',
        'NADARYA BUY', 'NADARYA SELL',
        'BB Upper', 'BB Lower',
        'upperpink', 'mirrorUpperBand',
        'Last Update'
    ]

def get_hist_headers():
    """Get historical column headers"""
    return [
        'Date', 'Open', 'High', 'Low', 'Close', 'Volume',
        'NADARYA BUY', 'NADARYA SELL',
        'BB Upper', 'BB Lower',
        'upperpink', 'mirrorUpperBand'
    ]

def get_order_headers():
    """R:AB order panel with BUY/SELL limit price (PRC), broker status and order number."""
    return [
        'Quantity',
        'BUY', 'ORDER TYPE (CNC/MIS)', 'BUY PRC', 'BUY Broker Status', 'BUY Order No',
        'SELL', '', 'SELL PRC', 'SELL Broker Status', 'SELL Order No'
    ]

import nadaraya_watson
import pink_mirror
import candle_cache
import indicator_settings
import indicator_history

# ============================================
# CONFIGURATION - FULLY CUSTOMIZABLE
# ============================================

class Config:
    BB_LENGTH = 20
    BB_STD = 2.0
    RSI_LENGTH = 14
    STOCH_LENGTH = 14
    STOCH_UPPER = 70
    STOCH_LOWER = 30
    EXCEL_UPDATE_INTERVAL = 0.1          # All live B:Q fields refreshed together
    INDICATOR_EXCEL_UPDATE_INTERVAL = 0.1  # Kept for compatibility; live fields share one write
    HIST_EXCEL_UPDATE_INTERVAL = 5.0
    LOAD_DAYS = 1500
    KEEP_DAYS = 500
    # Nadaraya-Watson Envelope [LuxAlgo] (daily, source = open) - see nadaraya_watson.py
    NWE_H = nadaraya_watson.H                 # 3.0  Bandwidth
    NWE_MULT = nadaraya_watson.MULT           # 0.9  mult
    NWE_SRC = nadaraya_watson.SRC             # 'open'
    NWE_REPAINT = nadaraya_watson.REPAINT     # True Repainting Smoothing
    # TradingView looks back 500 bars: ~340 trading bars (500 calendar days) is too
    # few, so fetch more calendar days (end-point mode needs ~1000 bars before it
    # has bands).
    NWE_LOAD_DAYS = 1100 if nadaraya_watson.REPAINT else 1700
    # upperpink (step line) + mirrorUpperBand - see pink_mirror.py
    # The step line is seeded at bar 200 of the FIRST bar loaded, so the first bar
    # must be FIXED (otherwise the seed drifts every day). Set this to the date of
    # the first bar your TradingView chart loaded to line the step up with it.
    PM_FIRST_BAR_DATE = '2023-01-02'
    # Weekly step indicator needs enough weekly bars for ATR(200) + highest(100).
    WEEKLY_FIRST_BAR_DATE = '2020-01-06'
    # SQLite cache of the DAILY candles (candle_cache.py): the full history is downloaded once,
    # later runs only fetch the last few days (or nothing when the cache is already up to date).
    USE_CANDLE_CACHE = True
    CANDLE_DB_PATH = str(WORKBOOK_PATH.parent / "daily_candles.db")   # beside the workbook
    CANDLE_OVERLAP_DAYS = 10          # already-cached days re-fetched to detect splits/bonus
    CANDLE_FULL_REFRESH_DAYS = 30     # full re-download this often
    # mirror band + step line inputs (overridden from the LOGIN sheet, column M:N)
    PM_LEN = pink_mirror.LEN
    PM_MULT = pink_mirror.MULT
    PM_REF = pink_mirror.REF_COL
    PM_SMA_COL = pink_mirror.SMA_COL
    PM_STEP_WIDTH = pink_mirror.STEP_WIDTH
    PM_STEP_SRC = pink_mirror.STEP_SRC
    PM_ATR_LEN = pink_mirror.ATR_LEN
    PM_ATR_HI = pink_mirror.ATR_HIGHEST
    PM_START_BAR = pink_mirror.START_BAR
    ORDER_BUFFER_RS = 0.0             # fixed rupees added (BUY) / subtracted (SELL) from LTP; 0 = use the % below
    ORDER_BUFFER_PCT = 0.5            # Shoonya refuses API MKT orders -> marketable LIMIT at LTP +/- this % (LOGIN: ORDER_BUFFER_PCT)
    AS_OF_DATE = None                 # date -> historical block shows that past day
    SETTINGS_POLL_SECONDS = 2.0
    HISTORY_SHEET = 'INDICATOR_HISTORY'
    HISTORY_MAX_DAYS = 500
    
    # ==========================================
    # COLUMN CONFIGURATION - CHANGE ANY START COLUMN
    # ==========================================
    
    SYMBOL_COL = 'A'           # Symbols column
    
    LIVE_START = 'B'           # Live data starts here
    # Columns are CONTIGUOUS (no gaps): Symbol A | LIVE B.. | ORDERS | HISTORICAL.
    # Orders start right after the live block; history right after the orders.
    ORDER_START = get_column_letter(get_column_number(LIVE_START) + len(get_live_headers()))
    HIST_START = get_column_letter(get_column_number(ORDER_START) + len(get_order_headers()))

# ============================================
# AUTO-CALCULATE COLUMN POSITIONS
# ============================================

LIVE_COUNT = len(get_live_headers())
HIST_COUNT = len(get_hist_headers())
ORDER_COUNT = len(get_order_headers())

# Get all column ranges
LIVE_COLS = get_column_range(Config.LIVE_START, LIVE_COUNT)
HIST_COLS = get_column_range(Config.HIST_START, HIST_COUNT)
ORDER_COLS = get_column_range(Config.ORDER_START, ORDER_COUNT)

# Auto-calculate END columns
LIVE_END = LIVE_COLS[-1] if LIVE_COLS else Config.LIVE_START
HIST_END = HIST_COLS[-1] if HIST_COLS else Config.HIST_START
ORDER_END = ORDER_COLS[-1] if ORDER_COLS else Config.ORDER_START

# Order-panel ranges derived from the order columns (never hard-code letters)
ORDER_ALL = f"{ORDER_COLS[0]}2:{ORDER_COLS[10]}200"          # Quantity .. SELL Order No
ORDER_BUY_BLOCK = f"{ORDER_COLS[3]}2:{ORDER_COLS[5]}200"     # BUY PRC, status, order no
ORDER_SELL_BLOCK = f"{ORDER_COLS[8]}2:{ORDER_COLS[10]}200"   # SELL PRC, status, order no

# Get ALL columns in order: A, LIVE, ORDERS, HISTORICAL
ALL_COLS = [Config.SYMBOL_COL] + LIVE_COLS + ORDER_COLS + HIST_COLS

# ============================================
# API CLASS
# ============================================

class ShoonyaApiPy(NorenApi):
    def __init__(self):
        super().__init__(
            host='https://api.shoonya.com/NorenWClientAPI/',
            websocket='wss://api.shoonya.com/NorenWSAPI/'
        )

# ============================================
# EXACT TRADINGVIEW FORMULAS
# ============================================

def tradingview_rsi(close, length=14):
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    alpha = 1.0 / length
    avg_gain = gain.ewm(alpha=alpha, adjust=False, ignore_na=True).mean()
    avg_loss = loss.ewm(alpha=alpha, adjust=False, ignore_na=True).mean()
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50)

def tradingview_stochrsi(rsi, length=14):
    lowest_rsi = rsi.rolling(length).min()
    highest_rsi = rsi.rolling(length).max()
    stoch = 100 * (rsi - lowest_rsi) / (highest_rsi - lowest_rsi)
    return stoch.fillna(50)

def tradingview_bb(close, length=20, std=2.0):
    sma = close.rolling(length).mean()
    stdev = close.rolling(length).std(ddof=0)
    upper = sma + (std * stdev)
    lower = sma - (std * stdev)
    return sma, upper, lower

# ============================================
# PLACE ORDER FUNCTION
# ============================================

def place_order(symbol, qty, buy_sell, price=0, product_type='C', algo_id=None):
    """Submit one Shoonya order and return the complete broker response."""
    try:
        if symbol not in symbol_tokens:
            print(f"❌ Symbol {symbol} not found")
            return None

        tk = symbol_tokens[symbol]
        exchange, tradingsymbol = parse_symbol_input(symbol)
        print(f"📊 Placing {buy_sell} order: {qty} shares of {symbol}")
        buy_or_sell = 'B' if buy_sell.upper() == 'BUY' else 'S'

        if price <= 0:
            if tk and tk in live_data:
                current_price = live_data[tk].get('ltp', 0)
                if current_price > 0:
                    # marketable limit: crosses the spread so it fills like a market order
                    # (price protection stays in place); rounded to a 0.05 tick
                    move = (Config.ORDER_BUFFER_RS if Config.ORDER_BUFFER_RS > 0
                            else current_price * Config.ORDER_BUFFER_PCT / 100.0)
                    raw_price = current_price + move if buy_sell.upper() == 'BUY' else current_price - move
                    price = max(round(round(raw_price / 0.05) * 0.05, 2), 0.05)
                else:
                    price = 100
            else:
                price = 100

        if not algo_id:
            print("❌ Algo ID / Client ID is blank in LOGIN!B2")
            return {"local_error": True, "emsg": "Algo ID / Client ID is blank in LOGIN!B2"}
        algo_id = str(algo_id).strip()

        order_params = {
            'buy_or_sell': buy_or_sell,
            'product_type': product_type,
            'exchange': exchange,
            'tradingsymbol': tradingsymbol,
            'quantity': int(qty),
            'discloseqty': 0,
            'price_type': 'LMT',
            'price': str(round(float(price), 2)),
            'trigger_price': None,
            'retention': 'DAY',
            'amo': None,
            'remarks': 'Python_Auto_Trade',
            'bookloss_price': 0.0,
            'bookprofit_price': 0.0,
            'trail_price': 0.0,
            'algo_id': algo_id
        }

        print(f"📋 Order Params: {order_params}")
        result = api.place_order(**order_params)
        print(f"📋 API Response: {result}")

        if isinstance(result, dict) and result.get('norenordno'):
            order_info = {
                'order_no': normalize_order_no(result['norenordno']),
                'symbol': symbol,
                'qty': int(qty),
                'type': buy_sell.upper(),
                'price': float(price),
                'status': normalize_order_status(result.get('status') or result.get('order_status') or ''),
                'time': datetime.now().strftime('%H:%M:%S')
            }
            order_history.append(order_info)
            # Keep the submitted limit price until the asynchronous broker update
            # supplies its authoritative PRC field.
            result = dict(result)
            result['_submitted_prc'] = str(round(float(price), 2))
            print(f"📋 Broker order number received; awaiting final status. Order No: {order_info['order_no']}")
            return result

        print(f"❌ Broker did not accept the order: {result}")
        return result if isinstance(result, dict) else None

    except Exception as e:
        print(f"❌ Error placing order: {e}")
        return {"local_error": True, "emsg": str(e)}


# ============================================
# FETCH HISTORICAL DATA
# ============================================

def _download_daily_rows(symbol, from_date, to_dt):
    """Raw daily rows from Shoonya between from_date and to_dt (list of dicts)."""
    start_epoch = int(datetime.combine(from_date, datetime.min.time()).timestamp())
    end_epoch = int(to_dt.timestamp())
    ret = api.get_daily_price_series(
        exchange="NSE", tradingsymbol=symbol,
        startdate=str(start_epoch), enddate=str(end_epoch)
    )
    rows = []
    for item in ret or []:
        if isinstance(item, str):
            try:
                rows.append(json.loads(item))
            except Exception:
                continue
    return rows


def history_days():
    """Calendar days of daily candles to load (settings + what each indicator needs)."""
    nwe = 1100 if Config.NWE_REPAINT else 1700
    pm = 1100
    if Config.PM_FIRST_BAR_DATE:
        first = datetime.strptime(Config.PM_FIRST_BAR_DATE, '%Y-%m-%d').date()
        pm = (datetime.now().date() - first).days + 30
    weekly = 0
    if Config.WEEKLY_FIRST_BAR_DATE:
        first_week = datetime.strptime(Config.WEEKLY_FIRST_BAR_DATE, '%Y-%m-%d').date()
        weekly = (datetime.now().date() - first_week).days + 30
    return max(Config.LOAD_DAYS, nwe, pm, weekly)


def indicator_params():
    """Indicator settings as the dict used by indicator_history."""
    return {
        'bb_length': Config.BB_LENGTH, 'bb_std': Config.BB_STD,
        'nwe_h': Config.NWE_H, 'nwe_mult': Config.NWE_MULT,
        'nwe_src': Config.NWE_SRC, 'nwe_repaint': Config.NWE_REPAINT,
        'pm_len': Config.PM_LEN, 'pm_mult': Config.PM_MULT,
        'pm_ref': Config.PM_REF, 'pm_sma': Config.PM_SMA_COL,
        'step_width': Config.PM_STEP_WIDTH, 'step_src': Config.PM_STEP_SRC,
        'atr_len': Config.PM_ATR_LEN, 'atr_hi': Config.PM_ATR_HI,
        'start_bar': Config.PM_START_BAR, 'first_bar_date': Config.PM_FIRST_BAR_DATE,
    }


def _rnd(v):
    return round(v, 2) if v == v else ''


def _row_from_snapshot(snap):
    """snapshot dict -> the same key format as hist['yesterday'] (only what the sheet shows)."""
    return {
        'date': snap['datetime'].strftime('%d/%m/%Y'),
        'open': snap['open'], 'high': snap['high'], 'low': snap['low'],
        'close': snap['close'], 'volume': snap['volume'],
        'bb_upper': _rnd(snap['bb_upper']), 'bb_lower': _rnd(snap['bb_lower']),
        'nwe_buy': snap['nwe_buy'], 'nwe_sell': snap['nwe_sell'],
        'pink': _rnd(snap['pink']), 'mirror': _rnd(snap['mirror']),
    }


def _weekly_snapshot(base, as_of=None, live_bar=None, last_bar_confirmed=None):
    """Build a complete weekly OHLCV + indicator snapshot from daily candles."""
    try:
        if base is None or base.empty:
            return None

        daily = base[['datetime', 'open', 'high', 'low', 'close', 'volume']].copy()
        daily['datetime'] = pd.to_datetime(daily['datetime'])
        if as_of is not None:
            daily = daily[daily['datetime'] <= pd.Timestamp(as_of)].copy()
        if daily.empty:
            return None

        # Overlay the live session's OHLCV on the daily candles before weekly aggregation.
        if live_bar is not None:
            ltp = safe_float(live_bar.get('ltp', 0))
            tick_time = live_bar.get('timestamp')
            today = pd.Timestamp(datetime.now().date())
            if ltp <= 0 or tick_time is None or pd.Timestamp(tick_time).date() != today.date():
                return None

            current = _current_bar(live_bar, ltp)
            live_row = {
                'datetime': today,
                'open': safe_float(current.get('open'), ltp),
                'high': safe_float(current.get('high'), ltp),
                'low': safe_float(current.get('low'), ltp),
                'close': ltp,
                'volume': safe_float(live_bar.get('volume', 0)),
            }
            today_mask = daily['datetime'].dt.date == today.date()
            if today_mask.any():
                idx = daily.index[today_mask][-1]
                for key, value in live_row.items():
                    daily.loc[idx, key] = value
            else:
                daily = pd.concat([daily, pd.DataFrame([live_row])], ignore_index=True)
            daily = daily.sort_values('datetime').reset_index(drop=True)

        weekly = indicator_history.to_weekly(daily)

        # Weekly Historical must contain completed weeks only. Exclude the
        # entire week containing the reference date (current week for live
        # operation, or the selected AS_OF_DATE when one is configured).
        # Keep the full earlier history so weekly indicators retain warm-up.
        if live_bar is None:
            reference_date = (
                pd.Timestamp(as_of).date()
                if as_of is not None
                else datetime.now().date()
            )
            current_week_start = pd.Timestamp(
                reference_date - timedelta(days=reference_date.weekday())
            )
            weekly = weekly[weekly['datetime'] < current_week_start].copy()

        if Config.WEEKLY_FIRST_BAR_DATE:
            weekly = weekly[weekly['datetime'] >= pd.Timestamp(Config.WEEKLY_FIRST_BAR_DATE)]
        weekly = weekly.reset_index(drop=True)
        if len(weekly) <= Config.PM_START_BAR:
            return None

        if last_bar_confirmed is None:
            week_start = weekly['datetime'].iloc[-1].date()
            reference_date = pd.Timestamp(as_of).date() if as_of is not None else datetime.now().date()
            last_bar_confirmed = reference_date > week_start + timedelta(days=4)

        # Weekly calculations must warm up from the weekly start date, not the daily start date.
        params = indicator_params()
        params['first_bar_date'] = Config.WEEKLY_FIRST_BAR_DATE
        return indicator_history.snapshot(weekly, params, last_bar_confirmed)
    except Exception as exc:
        print(f"⚠️ Weekly calculation: {exc}")
        return None


def _weekly_history_row(symbol, base, as_of=None):
    snap = _weekly_snapshot(base, as_of=as_of)
    if not snap:
        return [''] * 13
    return [
        symbol, snap['datetime'].strftime('%d/%m/%Y'),
        snap['open'], snap['high'], snap['low'], snap['close'], snap['volume'],
        snap['nwe_buy'], snap['nwe_sell'], _rnd(snap['bb_upper']), _rnd(snap['bb_lower']),
        _rnd(snap['pink']), _rnd(snap['mirror'])
    ]


def _weekly_live_row(symbol, base, live_bar):
    # The live weekly candle is provisional: allow upperpink to react to its current price.
    # Weekly Historical remains confirmed and is calculated independently above.
    snap = _weekly_snapshot(base, live_bar=live_bar, last_bar_confirmed=True)
    if not snap:
        return [''] * 14
    tick_time = live_bar.get('timestamp')
    return [
        symbol, snap['datetime'].strftime('%d/%m/%Y'), snap['close'],
        snap['open'], snap['high'], snap['low'], snap['volume'],
        snap['nwe_buy'], snap['nwe_sell'], _rnd(snap['bb_upper']), _rnd(snap['bb_lower']),
        _rnd(snap['pink']), _rnd(snap['mirror']),
        tick_time.strftime('%H:%M:%S') if tick_time else ''
    ]

def _download_df(symbol):
    """Daily candles (DataFrame) + source text, or (None, reason)."""
    end_date = datetime.now()
    start_date = end_date - timedelta(days=history_days())
    parsed_data, source = None, "Shoonya"
    if Config.USE_CANDLE_CACHE:
        try:
            parsed_data, source = candle_cache.get_daily(
                Config.CANDLE_DB_PATH,
                lambda frm, to: _download_daily_rows(symbol, frm, to),
                "NSE", symbol, start_date.date(),
                overlap_days=Config.CANDLE_OVERLAP_DAYS,
                full_refresh_days=Config.CANDLE_FULL_REFRESH_DAYS)
        except Exception as exc:
            print(f"⚠️ Candle cache unavailable ({exc}); loading {symbol} from Shoonya directly")
            parsed_data, source = None, "Shoonya (cache error)"
    if parsed_data is None and source in ("Shoonya", "Shoonya (cache error)"):
        parsed_data = _download_daily_rows(symbol, start_date.date(), end_date)
    if not parsed_data:
        return None, source

    df = pd.DataFrame(parsed_data)
    df.rename(columns={
        'time': 'datetime', 'into': 'open', 'inth': 'high',
        'intl': 'low', 'intc': 'close', 'intv': 'volume'
    }, inplace=True)
    df['datetime'] = pd.to_datetime(df['datetime'], format='%d-%b-%Y')
    for col in ['open', 'high', 'low', 'close', 'volume']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')
    df = df.sort_values('datetime').dropna().reset_index(drop=True)
    return df, source


def fetch_historical_data(symbol):
    try:
        df, source = _download_df(symbol)
        if df is None:
            return None
        return _build_hist(df, symbol, source)
    except Exception as e:
        print(f"Error fetching data: {e}")
        return None


def _build_hist(df, symbol, source):
    """Indicators from already-downloaded candles (no network).  Used at start-up and on a settings change."""
    try:
        base = df[['datetime', 'open', 'high', 'low', 'close', 'volume']].copy().reset_index(drop=True)
        df = base.copy()
        if len(df) < max(Config.BB_LENGTH, 3):
            return None

        df['RSI'] = tradingview_rsi(df['close'], Config.RSI_LENGTH)
        df['STOCH_RSI'] = tradingview_stochrsi(df['RSI'], Config.STOCH_LENGTH)
        df['SMA_STOCH'] = df['STOCH_RSI'].rolling(3).mean()

        bb_middle, bb_upper, bb_lower = tradingview_bb(df['close'], Config.BB_LENGTH, Config.BB_STD)
        df['BB_MIDDLE'] = bb_middle
        df['BB_UPPER'] = bb_upper
        df['BB_LOWER'] = bb_lower

        df['BUY_SIGNAL'] = (
            (df['close'].shift(1) < df['BB_LOWER'].shift(1)) &
            (df['close'] > df['BB_LOWER']) &
            (df['SMA_STOCH'].shift(1) < Config.STOCH_LOWER)
        )
        df['SELL_SIGNAL'] = (
            (df['close'].shift(1) > df['BB_UPPER'].shift(1)) &
            (df['close'] < df['BB_UPPER']) &
            (df['SMA_STOCH'].shift(1) > Config.STOCH_UPPER)
        )

        params = indicator_params()
        confirmed = df['datetime'].iloc[-1].date() != datetime.now().date()
        snap = indicator_history.snapshot(base, params, confirmed)
        nwe_buy, nwe_sell = snap['nwe_buy'], snap['nwe_sell']
        pm_step, pm_mirror = snap['pink'], snap['mirror']

        yesterday = df.iloc[-1]
        day_before = df.iloc[-2] if len(df) >= 2 else yesterday

        buy_signal = 1 if yesterday['BUY_SIGNAL'] else ''
        sell_signal = 1 if yesterday['SELL_SIGNAL'] else ''
        signal_text = 'BUY' if yesterday['BUY_SIGNAL'] else ('SELL' if yesterday['SELL_SIGNAL'] else '')

        yesterday_data = {
            'date': yesterday['datetime'].strftime('%d/%m/%Y'),
            'open': yesterday['open'],
            'high': yesterday['high'],
            'low': yesterday['low'],
            'close': yesterday['close'],
            'volume': yesterday['volume'],
            'bb_upper': round(yesterday['BB_UPPER'], 2),
            'bb_middle': round(yesterday['BB_MIDDLE'], 2),
            'bb_lower': round(yesterday['BB_LOWER'], 2),
            'rsi': round(yesterday['RSI'], 2),
            'stoch_rsi': round(yesterday['STOCH_RSI'], 2),
            'sma_stoch': round(yesterday['SMA_STOCH'], 2),
            'nwe_buy': nwe_buy,
            'nwe_sell': nwe_sell,
            'pink': _rnd(pm_step),
            'mirror': _rnd(pm_mirror),
            'buy': buy_signal,
            'sell': sell_signal,
            'signal': signal_text,
            'prev_close': day_before['close'],
            'prev_bb_upper': day_before['BB_UPPER'],
            'prev_bb_lower': day_before['BB_LOWER'],
            'prev_sma_stoch': day_before['SMA_STOCH']
        }

        # AS_OF_DATE: what the indicators showed on that past day (point-in-time)
        asof_row = None
        if Config.AS_OF_DATE:
            cut = base[base['datetime'] <= pd.Timestamp(Config.AS_OF_DATE)]
            if len(cut) >= 3:
                try:
                    asof_row = _row_from_snapshot(indicator_history.snapshot(cut.reset_index(drop=True), params, True))
                except Exception as exc:
                    print(f"⚠️ AS_OF_DATE {Config.AS_OF_DATE} for {symbol}: {exc}")

        try:
            # keep a record of the daily indicator values (one row per symbol / completed day)
            if Config.USE_CANDLE_CACHE and yesterday['datetime'].date() < datetime.now().date():
                candle_cache.save_indicators(Config.CANDLE_DB_PATH, "NSE", symbol,
                                             yesterday['datetime'].date(), {
                    'upperpink': pm_step, 'mirror_upper_band': pm_mirror,
                    'nadarya_buy': nwe_buy, 'nadarya_sell': nwe_sell,
                    'bb_upper': yesterday['BB_UPPER'], 'bb_lower': yesterday['BB_LOWER']})
        except Exception:
            pass

        return {
            'source': source,
            'symbol': symbol,
            'base': base,
            'df': df,
            'all_closes': df['close'].tolist(),
            'all_highs': df['high'].tolist(),
            'all_lows': df['low'].tolist(),
            'all_opens': df['open'].tolist(),
            'last_date': yesterday['datetime'].date(),
            'yesterday': yesterday_data,
            'asof': asof_row,
        }

    except Exception as e:
        print(f"Error building indicators for {symbol}: {e}")
        return None


def _preload_live(d, hist_data):
    """Copy the latest completed-day indicator values into a live_data entry (before ticks arrive)."""
    y = hist_data['yesterday']
    d['rsi'] = y['rsi']
    d['nwe_buy'] = y['nwe_buy']
    d['nwe_sell'] = y['nwe_sell']
    d['pink'] = y.get('pink', '')
    d['mirror'] = y.get('mirror', '')
    d['stoch_rsi'] = y['stoch_rsi']
    d['sma_stoch'] = y['sma_stoch']
    d['bb_upper'] = y['bb_upper']
    d['bb_middle'] = y['bb_middle']
    d['bb_lower'] = y['bb_lower']
    d['prev_close'] = y['prev_close']
    d['prev_bb_upper'] = y['prev_bb_upper']
    d['prev_bb_lower'] = y['prev_bb_lower']
    d['prev_sma_stoch'] = y['prev_sma_stoch']
    d.pop('_nwe_key', None)

def _current_bar(d, ltp):
    """Today's forming daily bar using accumulated ticks and exchange day high/low."""
    today_open = safe_float(d.get('open', 0)) or ltp
    day_high = safe_float(d.get('day_high', 0))
    day_low = safe_float(d.get('day_low', 0))
    high_now = safe_float(d.get('high', 0)) or ltp
    low_now = safe_float(d.get('low', 0)) or ltp
    if day_high > 0:
        high_now = max(high_now, day_high)
    if day_low > 0:
        low_now = min(low_now, day_low)
    return {'open': today_open, 'high': high_now, 'low': low_now, 'close': ltp}


_HIST_KEY = {'open': 'all_opens', 'high': 'all_highs', 'low': 'all_lows', 'close': 'all_closes'}


def _series_with_today(hist, name, cur):
    series = list(hist[_HIST_KEY[name]])
    if hist.get('last_date') == datetime.now().date():     # history already holds today's bar
        series[-1] = cur[name]
    else:
        series.append(cur[name])
    return series


def live_mirror(hist, d, ltp):
    """mirrorUpperBand on today's forming daily bar (today's open / low so far)."""
    cur = _current_bar(d, ltp)
    series = _series_with_today(hist, Config.PM_SMA_COL, cur)
    val = pink_mirror.mirror_last(series, cur[Config.PM_REF], Config.PM_LEN, Config.PM_MULT)
    return round(val, 2) if val == val else ''


def live_nwe_signal(hist, d, ltp):
    """Live NADARYA BUY / SELL for today's forming daily bar.

    The result is cached per (symbol, settings, today's source value) - and per tick only
    when the source / mode actually changes with the price.
    """
    cur = _current_bar(d, ltp)
    moving = (not Config.NWE_REPAINT) or Config.NWE_SRC != 'open'
    key = (id(hist), Config.NWE_H, Config.NWE_MULT, Config.NWE_SRC, Config.NWE_REPAINT,
           round(cur[Config.NWE_SRC], 4), round(ltp, 4) if moving else None)
    if d.get('_nwe_key') == key and '_nwe_val' in d:
        return d['_nwe_val']

    series = _series_with_today(hist, Config.NWE_SRC, cur)
    closes = _series_with_today(hist, 'close', cur)
    val = nadaraya_watson.nwe_last_bar_signal(
        series, closes, Config.NWE_H, Config.NWE_MULT, Config.NWE_REPAINT)
    d['_nwe_key'], d['_nwe_val'] = key, val
    return val

# ============================================
# SAFE CONVERSION
# ============================================

def safe_float(value, default=0):
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except:
            return default
    return default

def safe_int(value, default=0):
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        try:
            return int(float(value))
        except:
            return default
    return default

# ============================================
# LOGIN
# ============================================

def _validate_cached_shoonya_session(test_api):
    """Validate a restored access token with a harmless authenticated account request."""
    try:
        if not hasattr(test_api, "get_limits"):
            print("⚠️ Cached token cannot be checked: get_limits() is unavailable.")
            return False
        response = test_api.get_limits()
        if isinstance(response, dict) and str(response.get("stat", "")).strip().lower() == "ok":
            return True
        print(f"⚠️ Shoonya did not validate the cached token: {response}")
        return False
    except Exception as exc:
        print(f"Cached Shoonya session validation failed: {exc}")
        return False


def Shoonya_login(auth_code_provider=None):
    """Reuse a valid saved OAuth token; open the browser only when a fresh code is needed."""
    global api
    try:
        login_sheet = excel_name.sheets['LOGIN']
        session_sheet = _ensure_sheet("SESSION")

        # Make the saved OAuth-session storage visible in Excel.
        # Values are retained in their existing cells; only labels are added.
        session_sheet.range("A1").value = "SHOONYA OAUTH SESSION STORAGE"
        session_sheet.range("A2").value = "ACCESS TOKEN (saved)"
        session_sheet.range("A3").value = "SESSION CREATED"
        session_sheet.range("A4").value = "ACCOUNT ID"
        session_sheet.range("A6").value = "USER ID"
        session_sheet.range("A:A").column_width = 30
        session_sheet.range("B:B").column_width = 55
        client_id_value = login_sheet.range('B2').value
        userid_value = login_sheet.range('B3').value
        secret_value = login_sheet.range('B6').value
        if not userid_value or not secret_value:
            print("❌ LOGIN sheet is missing USER_ID (B3) or API_SECRET (B6).")
            return 0

        userid = str(userid_value).strip()
        secret = str(secret_value).strip()
        client_id = str(client_id_value).strip() if client_id_value else f"{userid}_U"
        cached_token = login_sheet.range('B9').value or session_sheet.range('B1').value
        cached_created_epoch = login_sheet.range('B12').value
        cached_created_iso = session_sheet.range('B3').value
        cached_actid = login_sheet.range('B13').value or session_sheet.range('B4').value
        cached_uid = session_sheet.range('B6').value

        age_seconds = -1
        try:
            if isinstance(cached_created_epoch, datetime):
                created_at = cached_created_epoch
                if created_at.tzinfo is not None:
                    created_at = created_at.replace(tzinfo=None)
                age_seconds = (datetime.now() - created_at).total_seconds()
            elif cached_created_epoch not in (None, ""):
                try:
                    age_seconds = time.time() - float(cached_created_epoch)
                except (TypeError, ValueError):
                    created_at = datetime.fromisoformat(str(cached_created_epoch).strip())
                    age_seconds = (datetime.now() - created_at.replace(tzinfo=None)).total_seconds()
            elif cached_created_iso:
                created_at = cached_created_iso if isinstance(cached_created_iso, datetime) else datetime.fromisoformat(str(cached_created_iso).strip())
                if created_at.tzinfo is not None:
                    created_at = created_at.replace(tzinfo=None)
                age_seconds = (datetime.now() - created_at).total_seconds()
        except Exception as exc:
            print(f"⚠️ Could not read saved OAuth token creation time: {exc}")

        if cached_token and 0 <= age_seconds < (23 * 60 * 60 + 50 * 60):
            if not cached_uid or str(cached_uid).strip() == userid:
                try:
                    candidate_api = ShoonyaApiPy()
                    candidate_api.injectOAuthHeader(str(cached_token).strip(), userid, str(cached_actid or userid).strip())
                    if _validate_cached_shoonya_session(candidate_api):
                        api = candidate_api
                        login_sheet.range('B8').value = str(cached_token)
                        login_sheet.range('B9').value = str(cached_token)
                        login_sheet.range('B13').value = str(cached_actid or userid)
                        session_sheet.range('B1').value = str(cached_token)
                        if isinstance(cached_created_epoch, datetime):
                            session_sheet.range('B3').value = cached_created_epoch.isoformat(timespec="seconds")
                        elif cached_created_epoch not in (None, ""):
                            try:
                                session_sheet.range('B3').value = datetime.fromtimestamp(float(cached_created_epoch)).isoformat(timespec="seconds")
                            except (TypeError, ValueError):
                                session_sheet.range('B3').value = cached_created_iso
                        else:
                            session_sheet.range('B3').value = cached_created_iso
                        session_sheet.range('B4').value = str(cached_actid or userid)
                        session_sheet.range('B6').value = userid
                        print("✅ Reusing valid Shoonya OAuth session. Browser will not open.")
                        return 1
                except Exception as exc:
                    print(f"⚠️ Could not restore cached Shoonya session: {exc}")
            else:
                print("⚠️ Saved session belongs to a different USER_ID; not reusing it.")
        elif cached_token:
            print("ℹ️ Saved Shoonya token is expired or its saved creation time is invalid.")

        auth = str(login_sheet.range('B7').value or "").strip()

        def obtain_fresh_code():
            if not callable(auth_code_provider):
                return ""
            print("🌐 No valid saved Shoonya session. Starting browser OAuth now.")
            code = str(auth_code_provider() or "").strip()
            if code:
                login_sheet.range('B7').value = code
                excel_name.save()
            return code

        if not auth:
            auth = obtain_fresh_code()
        if not auth:
            print("❌ No usable saved session or OAuth code was obtained.")
            return 0

        api = ShoonyaApiPy()
        try:
            result = api.getAccessToken(auth, secret, client_id, userid)
        except Exception as exc:
            print(f"⚠️ OAuth code exchange raised an error: {exc}")
            result = None

        if not result and callable(auth_code_provider):
            print("⚠️ Saved AUTH code was rejected. Requesting one fresh OAuth code and retrying.")
            login_sheet.range('B7').value = ""
            auth = obtain_fresh_code()
            if auth:
                api = ShoonyaApiPy()
                try:
                    result = api.getAccessToken(auth, secret, client_id, userid)
                except Exception as exc:
                    print(f"❌ Fresh OAuth code exchange raised an error: {exc}")
                    result = None
        if not result:
            print("❌ OAuth code exchange failed. No access token was returned.")
            return 0

        acc_tok, response_uid, ref_tok, actid = result
        if not acc_tok:
            print("❌ Shoonya did not return an access token.")
            return 0
        api.injectOAuthHeader(acc_tok, userid, actid or userid)
        created_epoch = int(time.time())
        created_iso = datetime.now().isoformat(timespec="seconds")
        login_sheet.range('B8').value = str(acc_tok)
        login_sheet.range('B9').value = str(acc_tok)
        login_sheet.range('B12').value = created_epoch
        login_sheet.range('B13').value = str(actid or userid)
        login_sheet.range('B14').value = str(ref_tok) if ref_tok else ""
        session_sheet.range('B1').value = str(acc_tok)
        session_sheet.range('B3').value = created_iso
        session_sheet.range('B4').value = str(actid or userid)
        session_sheet.range('B6').value = userid
        try:
            session_sheet.range('A1:A6').value = [
                ["ACCESS_TOKEN"], ["WS_TOKEN"], ["CREATED_AT"],
                ["ACCOUNT_ID"], ["REFRESH_TOKEN"], ["USER_ID"]
            ]
            session_sheet.range('B5').value = str(ref_tok) if ref_tok else ""
            session_sheet.visible = False
        except Exception:
            pass
        login_sheet.range('B7').value = ""
        excel_name.save()
        print("✅ New Shoonya OAuth session saved for reuse.")
        return 1
    except Exception as exc:
        print(f"Login error: {exc}")
        return 0


# ============================================
# GET TOKEN
# ============================================

def parse_symbol_input(value):
    """Accept EXCHANGE:TRADINGSYMBOL; plain symbols default to NSE equity."""
    raw = str(value or "").strip().upper()
    if not raw:
        return "NSE", ""
    if ":" in raw:
        exchange, trading_symbol = raw.split(":", 1)
    elif "|" in raw:
        exchange, trading_symbol = raw.split("|", 1)
    else:
        exchange, trading_symbol = "NSE", raw
    exchange, trading_symbol = exchange.strip().upper(), trading_symbol.strip().upper()
    if exchange not in SUPPORTED_EXCHANGES:
        raise ValueError(f"Unsupported exchange {exchange}; use {', '.join(SUPPORTED_EXCHANGES)}")
    if exchange == "NSE" and trading_symbol not in ("NIFTY","BANKNIFTY","FINNIFTY","MIDCPNIFTY","INDIAVIX") and not trading_symbol.endswith("-EQ"):
        trading_symbol += "-EQ"
    return exchange, trading_symbol


def normalized_symbol_key(value):
    exchange, trading_symbol = parse_symbol_input(value)
    return f"{exchange}:{trading_symbol}" if trading_symbol else ""


def GetToken(exchange, tradingsymbol):
    return instrument_master.get_token(exchange, tradingsymbol, api=api)

# ============================================
# CHECK ORDER SIGNALS FROM EXCEL
# ============================================

def normalize_order_no(value):
    """Keep Shoonya order numbers as exact text, never scientific notation."""
    if value is None or value == '':
        return ''
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    text_value = str(value).strip()
    if text_value.endswith('.0') and text_value[:-2].isdigit():
        return text_value[:-2]
    return text_value


def normalize_order_status(value):
    raw = str(value or 'PENDING').strip().upper().replace(' ', '_')
    mapping = {
        'OPEN': 'OPEN', 'PENDING': 'PENDING', 'WAITING': 'PENDING',
        'NEW': 'PENDING', 'SUBMITTED': 'PENDING',
        'MODIFY_PENDING': 'PENDING', 'CANCEL_PENDING': 'PENDING',
        'TRIGGER_PENDING': 'OPEN', 'COMPLETE': 'COMPLETE',
        'COMPLETED': 'COMPLETE', 'REJECTED': 'REJECTED',
        'CANCELED': 'CANCELED', 'CANCELLED': 'CANCELED',
        'PARTIAL': 'OPEN', 'PARTIALLY_FILLED': 'OPEN'
    }
    return mapping.get(raw, raw or 'PENDING')


def order_status_text(order):
    raw_status = order.get('status') or order.get('order_status')
    if not raw_status:
        return ''
    status = normalize_order_status(raw_status)
    filled = order.get('fillshares') or order.get('filled_qty') or ''
    average = order.get('avgprc') or order.get('average_price') or ''
    reason = order.get('rejreason') or order.get('rejection_reason') or ''
    details = []
    if filled not in ('', None):
        details.append(f"Filled {filled}")
    if average not in ('', None, '0', 0, '0.00'):
        details.append(f"Avg {average}")
    if status == 'REJECTED' and reason:
        details.append(str(reason))
    return status + ((" | " + " | ".join(details)) if details else "")


def find_order_cells(order_no):
    """Find an order and return its row, status, order-number and PRC columns."""
    target = normalize_order_no(order_no)
    if not target:
        return None
    ws = excel_name.sheets['symbols']
    symbols_data = ws.range(f"{Config.SYMBOL_COL}2:{Config.SYMBOL_COL}200").value or []
    buy_order_col, buy_status_col, buy_prc_col = ORDER_COLS[5], ORDER_COLS[4], ORDER_COLS[3]
    sell_order_col, sell_status_col, sell_prc_col = ORDER_COLS[10], ORDER_COLS[9], ORDER_COLS[8]
    for idx, symbol_cell in enumerate(symbols_data):
        if not symbol_cell:
            continue
        row_num = idx + 2
        if normalize_order_no(ws.range(f"{buy_order_col}{row_num}").value) == target:
            return row_num, buy_status_col, buy_order_col, buy_prc_col
        if normalize_order_no(ws.range(f"{sell_order_col}{row_num}").value) == target:
            return row_num, sell_status_col, sell_order_col, sell_prc_col
    return None


def apply_order_update(order):
    if not isinstance(order, dict):
        return
    order_no = normalize_order_no(order.get('norenordno') or order.get('order_no'))
    cells = find_order_cells(order_no)
    if not cells:
        return
    row_num, status_col, order_col, prc_col = cells
    ws = excel_name.sheets['symbols']
    broker_prc = order.get('prc') or order.get('price')
    if broker_prc not in ('', None):
        ws.range(f"{prc_col}{row_num}").value = broker_prc
    new_status = order_status_text(order)
    old_status = str(ws.range(f"{status_col}{row_num}").value or '').upper()
    old_base = normalize_order_status(old_status.split('|')[0].strip()) if old_status else ''
    raw_status = order.get('status') or order.get('order_status')
    if not raw_status:
        return
    new_base = normalize_order_status(raw_status)
    final_states = ('COMPLETE', 'REJECTED', 'CANCELED')
    if old_base in final_states and new_base not in final_states:
        return
    ws.range(f"{order_col}{row_num}").value = order_no
    ws.range(f"{status_col}{row_num}").value = new_status


def process_order_updates():
    """Apply queued broker updates in batches; never let per-order cell scans delay live refresh."""
    global pending_order_updates
    with order_update_lock:
        updates = pending_order_updates[:]
        pending_order_updates.clear()
    if not updates:
        return

    try:
        ws = excel_name.sheets['symbols']
        panel = ws.range(ORDER_ALL).value or []
        buy_block = []
        sell_block = []
        for row in panel:
            values = list(row) if isinstance(row, (list, tuple)) else [row]
            values += [""] * (11 - len(values))
            buy_block.append(values[3:6])
            sell_block.append(values[8:11])

        buy_changed = False
        sell_changed = False
        order_rows = {}
        for row_idx, row in enumerate(panel):
            values = list(row) if isinstance(row, (list, tuple)) else [row]
            values += [""] * (11 - len(values))
            buy_no = normalize_order_no(values[5])
            sell_no = normalize_order_no(values[10])
            if buy_no:
                order_rows[buy_no] = (row_idx, "BUY")
            if sell_no:
                order_rows[sell_no] = (row_idx, "SELL")

        for update in updates:
            if not isinstance(update, dict):
                continue
            order_no = normalize_order_no(update.get("norenordno") or update.get("order_no"))
            target = order_rows.get(order_no)
            if not target:
                continue
            row_idx, side = target
            raw_status = update.get("status") or update.get("order_status")
            broker_prc = update.get("prc") or update.get("price")
            if side == "BUY":
                block = buy_block
                prc_idx, status_idx, order_idx = 0, 1, 2
                buy_changed = True
            else:
                block = sell_block
                prc_idx, status_idx, order_idx = 0, 1, 2
                sell_changed = True

            if broker_prc not in ("", None):
                block[row_idx][prc_idx] = broker_prc
            if not raw_status:
                continue

            new_status = order_status_text(update)
            old_status = str(block[row_idx][status_idx] or "").upper()
            old_base = normalize_order_status(old_status.split("|")[0].strip()) if old_status else ""
            new_base = normalize_order_status(raw_status)
            final_states = ("COMPLETE", "REJECTED", "CANCELED")
            if old_base in final_states and new_base not in final_states:
                continue
            block[row_idx][order_idx] = order_no
            block[row_idx][status_idx] = new_status

        if buy_changed:
            ws.range(ORDER_BUY_BLOCK).value = buy_block
        if sell_changed:
            ws.range(ORDER_SELL_BLOCK).value = sell_block
    except Exception as exc:
        print(f"Order update batch write error: {exc}")

def _submit_order_worker(symbol, qty, side, product_code, algo_id):
    """Worker thread: REST request only. It never reads or writes Excel COM."""
    try:
        return place_order(symbol, qty, side, price=0, product_type=product_code, algo_id=algo_id)
    except Exception as exc:
        return {"local_error": True, "emsg": str(exc)}


def _order_worker_done(future, key, row_num, side, done_col, status_col, order_col, trigger_col):
    try:
        result = future.result()
    except Exception as exc:
        result = {"local_error": True, "emsg": str(exc)}
    order_result_queue.put({
        "key": key, "row": row_num, "side": side, "done_col": done_col,
        "status_col": status_col, "order_col": order_col,
        "trigger_col": trigger_col, "result": result
    })


def process_order_results():
    """Batch-write broker order numbers, PRC and statuses without touching triggers or type."""
    ws = excel_name.sheets['symbols']
    items = []
    while True:
        try:
            items.append(order_result_queue.get_nowait())
        except queue.Empty:
            break
    if not items:
        return

    try:
        panel = ws.range(ORDER_ALL).value or []
        buy_block = []
        sell_block = []
        for row in panel:
            values = list(row) if isinstance(row, (list, tuple)) else [row]
            values += [""] * (11 - len(values))
            buy_block.append(values[3:6])
            sell_block.append(values[8:11])

        buy_changed = False
        sell_changed = False
        for item in items:
            key = item["key"]
            try:
                result = item["result"]
                if not isinstance(result, dict):
                    print(f"❌ {item['side']} {key[0]}: no broker response; no broker status written.")
                    continue

                row_idx = int(item["row"]) - 2
                if row_idx < 0 or row_idx >= len(panel):
                    continue
                side = item["side"]
                block = buy_block if side == "BUY" else sell_block
                prc_idx, status_idx, order_idx = 0, 1, 2

                if result.get("norenordno"):
                    order_no = normalize_order_no(result["norenordno"])
                    block[row_idx][order_idx] = order_no
                    submitted_prc = result.get("_submitted_prc") or result.get("prc")
                    if submitted_prc not in ("", None):
                        block[row_idx][prc_idx] = submitted_prc
                    broker_status = result.get("status") or result.get("order_status")
                    if broker_status:
                        block[row_idx][status_idx] = normalize_order_status(broker_status)
                    if side == "BUY":
                        buy_changed = True
                    else:
                        sell_changed = True
                    print(f"📋 Broker response for {side} {key[0]}: "
                          f"{broker_status or 'Order number received'} | {order_no}")
                elif result.get("local_error"):
                    print(f"⚠️ Local submission error for {side} {key[0]}: "
                          f"{result.get('emsg', 'Unknown error')}")
                elif str(result.get("stat", "")).lower() in ("not_ok", "not ok") and result.get("emsg"):
                    block[row_idx][status_idx] = "REJECTED | " + str(result["emsg"])
                    if side == "BUY":
                        buy_changed = True
                    else:
                        sell_changed = True
                    print(f"📋 Broker rejected {side} {key[0]}: {result['emsg']}")
                else:
                    print(f"⚠️ No broker order number/status for {side} {key[0]}: {result}")
            except Exception as exc:
                print(f"Excel broker-result batch error for {key}: {exc}")
            finally:
                with order_inflight_lock:
                    order_inflight.discard(key)

        if buy_changed:
            ws.range(ORDER_BUY_BLOCK).value = buy_block
        if sell_changed:
            ws.range(ORDER_SELL_BLOCK).value = sell_block
    except Exception as exc:
        print(f"Excel broker-result write error: {exc}")

def reconcile_order_statuses():
    """Fetch the order book asynchronously and batch-apply broker status changes."""
    global last_order_reconcile, order_reconcile_future
    now = time.time()
    if order_reconcile_future is not None and order_reconcile_future.done():
        try:
            orders = order_reconcile_future.result()
            if isinstance(orders, list):
                ws = excel_name.sheets['symbols']
                panel = ws.range(ORDER_ALL).value or []
                buy_block = []
                sell_block = []
                order_rows = {}
                for row_idx, row in enumerate(panel):
                    values = list(row) if isinstance(row, (list, tuple)) else [row]
                    values += [""] * (11 - len(values))
                    buy_block.append(values[3:6])
                    sell_block.append(values[8:11])
                    buy_no = normalize_order_no(values[5])
                    sell_no = normalize_order_no(values[10])
                    if buy_no:
                        order_rows[buy_no] = (row_idx, "BUY")
                    if sell_no:
                        order_rows[sell_no] = (row_idx, "SELL")

                buy_changed = False
                sell_changed = False
                for broker_order in orders:
                    if not isinstance(broker_order, dict):
                        continue
                    order_no = normalize_order_no(broker_order.get("norenordno"))
                    target = order_rows.get(order_no)
                    if not target:
                        continue
                    row_idx, side = target
                    block = buy_block if side == "BUY" else sell_block
                    broker_prc = broker_order.get("prc") or broker_order.get("price")
                    if broker_prc not in ("", None):
                        block[row_idx][0] = broker_prc
                        if side == "BUY":
                            buy_changed = True
                        else:
                            sell_changed = True

                    broker_status = broker_order.get("status") or broker_order.get("order_status")
                    if not broker_status:
                        continue
                    new_status = order_status_text(broker_order)
                    old_status = str(block[row_idx][1] or "")
                    old_base = normalize_order_status(old_status.split("|")[0].strip()) if old_status else ""
                    new_base = normalize_order_status(broker_status)
                    final_states = ("COMPLETE", "REJECTED", "CANCELED")
                    if old_base in final_states and new_base not in final_states:
                        continue
                    if new_status != old_status:
                        block[row_idx][1] = new_status
                        if side == "BUY":
                            buy_changed = True
                        else:
                            sell_changed = True

                if buy_changed:
                    ws.range(ORDER_BUY_BLOCK).value = buy_block
                if sell_changed:
                    ws.range(ORDER_SELL_BLOCK).value = sell_block
        except Exception as exc:
            print(f"Broker order-book read/apply error: {exc}")
        finally:
            order_reconcile_future = None

    if order_reconcile_future is None and now - last_order_reconcile >= 2:
        last_order_reconcile = now
        order_reconcile_future = order_executor.submit(api.get_order_book)

def check_order_signals():
    """Bulk-scan manual inputs; both sides use the one shared type in column T."""
    global last_order_signal_scan
    now = time.monotonic()
    if now - last_order_signal_scan < 0.2:
        return
    last_order_signal_scan = now
    try:
        ws = excel_name.sheets['symbols']
        symbols_data = ws.range(f"{Config.SYMBOL_COL}2:{Config.SYMBOL_COL}200").value or []
        panel = ws.range(ORDER_ALL).value or []
        login_sheet = excel_name.sheets['LOGIN']
        algo_id_value = login_sheet.range('B2').value
        algo_id = str(algo_id_value).strip() if algo_id_value else ''
        if not algo_id:
            return

        for idx, symbol_cell in enumerate(symbols_data):
            if not symbol_cell:
                continue
            try:
                symbol = normalized_symbol_key(symbol_cell)
            except ValueError:
                continue
            if symbol not in symbol_tokens:
                continue

            row = panel[idx] if idx < len(panel) else []
            row = list(row) if isinstance(row, (list, tuple)) else [row]
            row += [""] * (11 - len(row))
            try:
                qty = max(1, int(float(row[0]))) if row[0] not in ('', None) else 1
            except Exception:
                qty = 1

            for side, trigger_index, type_index, status_index, order_index in (
                ("BUY", 1, 2, 4, 5),
                ("SELL", 6, 2, 9, 10)
            ):
                trigger = str(row[trigger_index] or '').strip().upper()
                broker_status = str(row[status_index] or '').strip()
                order_no = normalize_order_no(row[order_index])
                if trigger != side or broker_status or order_no:
                    continue

                product_text = str(row[type_index] or '').strip().upper()
                product_code = {"CNC": "C", "MIS": "I"}.get(product_text)
                if not product_code:
                    print(f"⚠️ {symbol} {side}: select CNC or MIS in Excel; no order sent.")
                    continue

                key = (symbol, side)
                with order_inflight_lock:
                    if key in order_inflight:
                        continue
                    order_inflight.add(key)

                status_col = ORDER_COLS[4 if side == "BUY" else 9]
                order_col = ORDER_COLS[5 if side == "BUY" else 10]
                print(f"🔔 Sending {side} request for {symbol} (Qty: {qty}, Product: {product_text})")
                try:
                    future = order_executor.submit(
                        _submit_order_worker, symbol, qty, side, product_code, algo_id
                    )
                    future.add_done_callback(
                        lambda f, k=key, r=idx + 2, sd=side, sc=status_col, oc=order_col:
                        _order_worker_done(f, k, r, sd, "", sc, oc, "")
                    )
                except Exception:
                    with order_inflight_lock:
                        order_inflight.discard(key)
                    raise
    except Exception as exc:
        print(f"Order input scan error: {exc}")


# ============================================
# WEBSOCKET CALLBACKS
# ============================================

def on_ticks(tick):
    global live_data, tick_count
    
    try:
        if isinstance(tick, str):
            tick = json.loads(tick)
        
        tick_count += 1
        
        key = f"{tick['e']}|{tick['tk']}"
        
        if key in live_data:
            d = live_data[key]
            ltp = safe_float(tick.get('lp', d.get('ltp', 0)))
            volume = safe_int(tick.get('v', d.get('volume', 0)))
            open_price = safe_float(tick.get('o', d.get('open', 0)))
            
            symbol = d['symbol']
            
            if d.get('first_tick', True):
                d['first_tick'] = False
                d['open'] = open_price if open_price > 0 else ltp
                d['high'] = ltp
                d['low'] = ltp
            
            if ltp > d.get('high', 0):
                d['high'] = ltp
            if ltp < d.get('low', 999999):
                d['low'] = ltp
            
            d['ltp'] = ltp
            d['volume'] = volume
            d['timestamp'] = datetime.now()
            d['close'] = ltp
            # Shoonya touchline 'h'/'l' fields are the exchange day high/low.
            _tick_high = safe_float(tick.get('h', 0))
            _tick_low = safe_float(tick.get('l', 0))
            if _tick_high > 0:
                d['day_high'] = _tick_high
            if _tick_low > 0:
                d['day_low'] = _tick_low
            
            hist = historical_data_cache.get(symbol)
            
            if hist:
                all_closes = hist['all_closes'].copy()
                all_closes.append(ltp)
                
                df = pd.DataFrame({'close': all_closes})
                
                delta = df['close'].diff()
                gain = delta.where(delta > 0, 0.0)
                loss = -delta.where(delta < 0, 0.0)
                alpha = 1.0 / Config.RSI_LENGTH
                avg_gain = gain.ewm(alpha=alpha, adjust=False, ignore_na=True).mean()
                avg_loss = loss.ewm(alpha=alpha, adjust=False, ignore_na=True).mean()
                rs = avg_gain / avg_loss
                rsi = 100 - (100 / (1 + rs))
                rsi = rsi.fillna(50)
                
                rsi_low = rsi.rolling(Config.STOCH_LENGTH).min()
                rsi_high = rsi.rolling(Config.STOCH_LENGTH).max()
                stoch = 100 * (rsi - rsi_low) / (rsi_high - rsi_low)
                stoch = stoch.fillna(50)
                sma_stoch = stoch.rolling(3).mean()
                
                sma = df['close'].rolling(Config.BB_LENGTH).mean()
                stdev = df['close'].rolling(Config.BB_LENGTH).std(ddof=0)
                bb_upper = sma + (Config.BB_STD * stdev)
                bb_lower = sma - (Config.BB_STD * stdev)
                
                latest_rsi = rsi.iloc[-1] if len(rsi) > 0 else 50
                latest_stoch = stoch.iloc[-1] if len(stoch) > 0 else 50
                latest_sma_stoch = sma_stoch.iloc[-1] if len(sma_stoch) > 0 else 50
                latest_bb_upper = bb_upper.iloc[-1] if len(bb_upper) > 0 else 0
                latest_bb_middle = sma.iloc[-1] if len(sma) > 0 else 0
                latest_bb_lower = bb_lower.iloc[-1] if len(bb_lower) > 0 else 0
                
                prev_bb_upper = bb_upper.iloc[-2] if len(bb_upper) >= 2 else latest_bb_upper
                prev_bb_lower = bb_lower.iloc[-2] if len(bb_lower) >= 2 else latest_bb_lower
                prev_close = all_closes[-2] if len(all_closes) >= 2 else ltp
                prev_sma_stoch = sma_stoch.iloc[-2] if len(sma_stoch) >= 2 else latest_sma_stoch
                
                d['rsi'] = round(latest_rsi, 2)
                d['stoch_rsi'] = round(latest_stoch, 2)
                d['sma_stoch'] = round(latest_sma_stoch, 2)
                d['bb_upper'] = round(latest_bb_upper, 2)
                d['bb_middle'] = round(latest_bb_middle, 2)
                d['bb_lower'] = round(latest_bb_lower, 2)
                d['prev_close'] = prev_close
                d['prev_bb_upper'] = prev_bb_upper
                d['prev_bb_lower'] = prev_bb_lower
                d['prev_sma_stoch'] = prev_sma_stoch
                
                buy_signal = False
                sell_signal = False
                signal = ""
                
                if (prev_close < prev_bb_lower and 
                    ltp > latest_bb_lower and 
                    prev_sma_stoch < Config.STOCH_LOWER):
                    buy_signal = True
                    signal = "BUY"
                
                elif (prev_close > prev_bb_upper and 
                      ltp < latest_bb_upper and 
                      prev_sma_stoch > Config.STOCH_UPPER):
                    sell_signal = True
                    signal = "SELL"
                
                d['buy'] = 1 if buy_signal else ''
                d['sell'] = 1 if sell_signal else ''
                d['signal'] = signal

                try:
                    d['nwe_buy'], d['nwe_sell'] = live_nwe_signal(hist, d, ltp)
                except Exception:
                    pass

                try:
                    # step line only moves on confirmed bars (Pine) -> constant during the day
                    d['pink'] = hist['yesterday'].get('pink', '')
                    d['mirror'] = live_mirror(hist, d, ltp)
                except Exception:
                    pass
                
    except Exception as e:
        pass

def on_open():
    global feed_opened
    feed_opened = True
    print("✅ WebSocket Connected")

def on_close():
    global feed_opened
    feed_opened = False
    print("❌ WebSocket Closed")

def on_ws_error(error):
    print(f"❌ WebSocket error / authentication response: {error}")

def on_order(order):
    print(f"📋 Order Update: {order}")
    if isinstance(order, dict):
        with order_update_lock:
            pending_order_updates.append(order)

def subscribe_symbols(tokens_list):
    if not tokens_list:
        return
    for i in range(0, len(tokens_list), 10):
        try:
            api.subscribe(tokens_list[i:i+10])
        except Exception as e:
            pass
        time.sleep(0.1)

# ============================================
# UPDATE EXCEL - FIXED COLUMN POSITIONS
# ============================================

def update_excel_bulk():
    """Write all live fields B:Q together; historical AC:AQ remains on its slower timer."""
    global last_excel_update, last_indicator_excel_update, last_hist_excel_update
    try:
        now = time.time()
        live_due = now - last_excel_update >= Config.EXCEL_UPDATE_INTERVAL
        hist_due = now - last_hist_excel_update >= Config.HIST_EXCEL_UPDATE_INTERVAL
        if not (live_due or hist_due):
            return

        ws = excel_name.sheets['symbols']
        symbols_data = ws.range(f"{Config.SYMBOL_COL}2:{Config.SYMBOL_COL}200").value or []
        live_rows, hist_rows, weekly_history_rows, weekly_live_rows = [], [], [], []

        for symbol_cell in symbols_data:
            if not symbol_cell:
                if live_due:
                    live_rows.append([''] * LIVE_COUNT)
                if hist_due:
                    hist_rows.append([''] * HIST_COUNT)
                    weekly_history_rows.append([''] * 13)
                    weekly_live_rows.append([''] * 14)
                continue

            try:
                symbol = normalized_symbol_key(symbol_cell)
            except ValueError:
                if live_due:
                    live_rows.append([''] * LIVE_COUNT)
                if hist_due:
                    hist_rows.append([''] * HIST_COUNT)
                    weekly_history_rows.append([''] * 13)
                    weekly_live_rows.append([''] * 14)
                continue

            token_key = symbol_tokens.get(symbol)
            data = live_data.get(token_key) if token_key else None
            hist = historical_data_cache.get(symbol)

            if live_due:
                if data:
                    live_rows.append([
                        data.get('ltp', 0) if data.get('ltp', 0) > 0 else '',
                        data.get('open', 0) if data.get('open', 0) > 0 else '',
                        data.get('high', 0) if data.get('high', 0) > 0 else '',
                        data.get('low', 0) if data.get('low', 0) > 0 else '',
                        data.get('close', 0) if data.get('close', 0) > 0 else '',
                        data.get('volume', 0) if data.get('volume', 0) > 0 else '',
                        data.get('nwe_buy', ''), data.get('nwe_sell', ''),
                        data.get('bb_upper', ''), data.get('bb_lower', ''),
                        data.get('pink', ''), data.get('mirror', ''),
                        data['timestamp'].strftime('%H:%M:%S') if data.get('timestamp') else ''
                    ])
                else:
                    live_rows.append([''] * LIVE_COUNT)

            if hist_due:
                if not hist:
                    yesterday = {}
                elif Config.AS_OF_DATE:
                    yesterday = hist.get('asof') or {}      # past day (blank if before the first bar)
                else:
                    yesterday = hist['yesterday']
                hist_rows.append([
                    yesterday.get('date', ''), yesterday.get('open', ''),
                    yesterday.get('high', ''), yesterday.get('low', ''),
                    yesterday.get('close', ''), yesterday.get('volume', ''),
                    yesterday.get('nwe_buy', ''), yesterday.get('nwe_sell', ''),
                    yesterday.get('bb_upper', ''), yesterday.get('bb_lower', ''),
                    yesterday.get('pink', ''), yesterday.get('mirror', '')
                ])
                # Full weekly historical block AL:AX and current/live block AY:BL.
                weekly_history_rows.append(
                    _weekly_history_row(symbol, hist.get('base') if hist else None,
                                        as_of=Config.AS_OF_DATE)
                )
                weekly_live_rows.append(
                    _weekly_live_row(symbol, hist.get('base'), data)
                    if hist and data else [''] * 14
                )

        if symbols_data:
            last_row = 1 + len(symbols_data)
            if live_due and live_rows:
                ws.range(f"{Config.LIVE_START}2:{LIVE_END}{last_row}").value = live_rows
                last_excel_update = now
                last_indicator_excel_update = now
            if hist_due and hist_rows:
                ws.range(f"{Config.HIST_START}2:{HIST_END}{last_row}").value = hist_rows
                # Fixed user workbook positions; never insert, delete, or shift columns.
                ws.range(f"AL2:AX{last_row}").value = weekly_history_rows
                ws.range(f"AY2:BL{last_row}").value = weekly_live_rows
                last_hist_excel_update = now
                # Separate broker-daily-candle weekly OHLCV output (WEEKLY_DATA sheet).
                weekly_data_engine.refresh_from_excel(excel_name, api, read_symbols_from_excel())

    except Exception as exc:
        print(f"Excel update error: {exc}")


# ============================================
# ONE-TIME WORKBOOK COLUMN MIGRATION
# ============================================


def migrate_legacy_layout(ws):
    """Preserve the previous R:X orders and Y:AM history when expanding orders to R:AB."""
    try:
        old_headers = ws.range("S1:X1").value
        if not old_headers or len(old_headers) < 6:
            return
        normalized = [str(v or "").strip().upper() for v in old_headers]
        legacy_order_layout = normalized == [
            "BUY TRIGGER", "BUY STATUS", "BUY ORDER NO",
            "SELL TRIGGER", "SELL STATUS", "SELL ORDER NO"
        ]
        if not legacy_order_layout:
            return

        old_orders = ws.range("R2:X200").value or []
        old_history = ws.range("Y2:AM200").value or []
        migrated_orders = []

        for row in old_orders:
            row = list(row) if isinstance(row, (list, tuple)) else [row]
            row += [""] * (7 - len(row))
            qty, old_buy, old_buy_status, old_buy_no, old_sell, old_sell_status, old_sell_no = row[:7]

            buy_value = str(old_buy or "").strip().upper()
            buy_status_value = str(old_buy_status or "").strip().upper()
            if buy_value in ("BUY", "DONE", "FAILED"):
                buy_trigger = "BUY" if buy_value == "BUY" else ""
                buy_done = buy_value if buy_value in ("DONE", "FAILED") else ""
                buy_type = "CNC" if (old_buy_no or old_buy_status) else ""
                buy_status = old_buy_status or ""
            elif buy_value in ("CNC", "MIS") and buy_status_value == "BUY":
                buy_trigger, buy_type, buy_done, buy_status = "BUY", buy_value, "", ""
            else:
                buy_trigger, buy_type, buy_done, buy_status = "", "", "", old_buy_status or ""

            sell_value = str(old_sell or "").strip().upper()
            sell_status_value = str(old_sell_status or "").strip().upper()
            if sell_value in ("SELL", "DONE", "FAILED"):
                sell_trigger = "SELL" if sell_value == "SELL" else ""
                sell_done = sell_value if sell_value in ("DONE", "FAILED") else ""
                sell_type = "CNC" if (old_sell_no or old_sell_status) else ""
                sell_status = old_sell_status or ""
            elif sell_value in ("CNC", "MIS") and sell_status_value == "SELL":
                sell_trigger, sell_type, sell_done, sell_status = "SELL", sell_value, "", ""
            else:
                sell_trigger, sell_type, sell_done, sell_status = "", "", "", old_sell_status or ""

            migrated_orders.append([
                qty, buy_trigger, buy_type, buy_done, buy_status, old_buy_no,
                sell_trigger, sell_type, sell_done, sell_status, old_sell_no
            ])

        # Shift the old 15-column history block Y:AM to its new location AC:AQ.
        if old_history:
            ws.range(f"{Config.HIST_START}2:{HIST_END}200").value = [list(r)[:HIST_COUNT] for r in old_history]
        # Write the transformed order block last because it now occupies Y:AB too.
        if migrated_orders:
            ws.range(ORDER_ALL).value = migrated_orders
        print("✅ Migrated existing order/history columns to the new layout.")
    except Exception as exc:
        print(f"⚠️ Workbook layout migration skipped: {exc}")


def consolidate_shared_order_type(ws):
    """Use T as the single CNC/MIS selector for both BUY and SELL; retain column Y."""
    try:
        t_col, y_col = ORDER_COLS[2], ORDER_COLS[7]   # shared type column + legacy SELL type column
        buy_types = ws.range(f"{t_col}2:{t_col}200").value or []
        sell_types = ws.range(f"{y_col}2:{y_col}200").value or []
        merged = []
        changed_t = False
        for idx in range(199):
            buy_value = buy_types[idx] if idx < len(buy_types) else None
            sell_value = sell_types[idx] if idx < len(sell_types) else None
            if buy_value in (None, "") and sell_value not in (None, ""):
                buy_value = sell_value
                changed_t = True
            merged.append([buy_value])
        if changed_t:
            ws.range(f"{t_col}2:{t_col}200").value = merged
        if any(value not in (None, "") for value in sell_types):
            ws.range(f"{y_col}2:{y_col}200").clear_contents()
    except Exception as exc:
        print(f"⚠️ Shared order-type migration skipped: {exc}")


# ============================================
# SETUP HEADERS - DYNAMIC
# ============================================

def migrate_spacer_columns():
    """Compatibility no-op: never insert, delete, or shift the user's workbook columns."""
    print("🔒 Workbook column migration disabled; existing column positions are preserved.")
    return True
def setup_excel_headers():
    """Restore blank headers A:BL and apply separate section colours without moving data."""
    try:
        ws = excel_name.sheets['symbols']

        # Exact existing workbook layout: Symbol | Live | Orders | Daily | Weekly history | Weekly live.
        headers = {
            "A": "SYMBOL",
            "B": "LTP", "C": "Open", "D": "High", "E": "Low", "F": "Close",
            "G": "Volume", "H": "NADARYA BUY", "I": "NADARYA SELL",
            "J": "BB Upper", "K": "BB Lower", "L": "upperpink",
            "M": "mirrorUpperBand", "N": "Last Update",
            "O": "Quantity", "P": "BUY", "Q": "ORDER TYPE (CNC/MIS)",
            "R": "BUY PRC", "S": "BUY Broker Status", "T": "BUY Order No",
            "U": "SELL", "V": "", "W": "SELL PRC",
            "X": "SELL Broker Status", "Y": "SELL Order No",
            "Z": "Date", "AA": "Open", "AB": "High", "AC": "Low",
            "AD": "Close", "AE": "Volume", "AF": "NADARYA BUY",
            "AG": "NADARYA SELL", "AH": "BB Upper", "AI": "BB Lower",
            "AJ": "upperpink", "AK": "mirrorUpperBand",
            "AL": "SYMBOL", "AM": "Week (Mon)", "AN": "Open", "AO": "High",
            "AP": "Low", "AQ": "Close", "AR": "Volume", "AS": "NADARYA BUY",
            "AT": "NADARYA SELL", "AU": "BB Upper", "AV": "BB Lower",
            "AW": "upperpink", "AX": "mirrorUpperBand",
            "AY": "SYMBOL", "AZ": "Week (Mon)", "BA": "LTP", "BB": "Open",
            "BC": "High", "BD": "Low", "BE": "Volume", "BF": "NADARYA BUY",
            "BG": "NADARYA SELL", "BH": "BB Upper", "BI": "BB Lower",
            "BJ": "upperpink", "BK": "mirrorUpperBand", "BL": "Last Update"
        }

        # Only fill blank header cells. Never overwrite user labels or any data rows.
        filled = 0
        for col, header in headers.items():
            cell = ws.range(f"{col}1")
            if cell.value is None or str(cell.value).strip() == "":
                if header:
                    cell.value = header
                    filled += 1

        # Distinct header colours for each data section.
        sections = [
            ("A1:A1", (217, 217, 217)),       # Symbol
            ("B1:N1", (189, 215, 238)),       # Live
            ("O1:Y1", (252, 228, 214)),       # Orders
            ("Z1:AK1", (226, 239, 218)),      # Daily history
            ("AL1:AX1", (217, 210, 233)),     # Weekly history
            ("AY1:BL1", (244, 204, 204)),     # Weekly live
        ]
        for address, fill in sections:
            section = ws.range(address)
            section.color = fill
            section.api.Font.Bold = True
            section.api.HorizontalAlignment = -4108
            section.api.VerticalAlignment = -4108
            section.api.WrapText = True

        print(f"🧾 Headers A:BL checked; filled {filled} blank header cells.")
        print("🎨 Header colours applied: Live=Blue, Orders=Orange, Daily=Green, Weekly History=Purple, Weekly Live=Pink.")
        print("🔒 Existing nonblank headers, data rows, and column positions preserved (no migration).")
        return True
    except Exception as exc:
        print(f"❌ Could not restore headers/colours: {exc}")
        return False

# ============================================
# READ SYMBOLS
# ============================================

def read_symbols_from_excel():
    try:
        ws = excel_name.sheets['symbols']
        symbols_data = ws.range(f"{Config.SYMBOL_COL}2:{Config.SYMBOL_COL}200").value
        symbols = [str(s).strip().upper() for s in symbols_data if s] if symbols_data else []
        cleaned, seen = [], set()
        for raw_symbol in symbols:
            try:
                key = normalized_symbol_key(raw_symbol)
            except ValueError as exc:
                print(f"Skipping symbol '{raw_symbol}': {exc}")
                continue
            if key and key not in seen:
                seen.add(key)
                cleaned.append(key)
        return cleaned
    except Exception:
        return []

# ============================================
# CHECK NEW SYMBOLS
# ============================================

def check_new_symbols():
    global last_symbol_check
    
    try:
        ws = excel_name.sheets['symbols']
        symbols_data = ws.range(f"{Config.SYMBOL_COL}2:{Config.SYMBOL_COL}100").value
        current = [str(s).strip().upper() for s in symbols_data if s] if symbols_data else []
        cleaned = []
        for raw_symbol in current:
            try:
                key = normalized_symbol_key(raw_symbol)
                if key:
                    cleaned.append(key)
            except ValueError as exc:
                print(f"Skipping symbol '{raw_symbol}': {exc}")
        new_symbols = [s for s in cleaned if s not in symbol_tokens]
        
        if new_symbols:
            print(f"🆕 Found {len(new_symbols)} new symbols")
            new_tokens = []
            for symbol in new_symbols:
                try:
                    exchange, trading_symbol = parse_symbol_input(symbol)
                    token = GetToken(exchange, trading_symbol)
                    if token:
                        tk = f"{exchange}|{token}"
                        symbol_tokens[symbol] = tk
                        token_symbols[tk] = symbol
                        hist_data = fetch_historical_data(trading_symbol) if exchange == "NSE" else None
                        
                        live_data[tk] = {
                            'symbol': symbol,
                            'first_tick': True,
                            'ltp': 0, 'volume': 0,
                            'open': 0, 'high': 0, 'low': 0, 'close': 0,
                            'rsi': 50, 'stoch_rsi': 50, 'sma_stoch': 50,
                            'nwe_buy': '', 'nwe_sell': '', 'pink': '', 'mirror': '',
                                        'bb_upper': 0, 'bb_middle': 0, 'bb_lower': 0,
                            'buy': '', 'sell': '', 'signal': '',
                            'prev_close': 0,
                            'prev_bb_upper': 0,
                            'prev_bb_lower': 0,
                            'prev_sma_stoch': 50,
                            'timestamp': None
                        }
                        new_tokens.append(tk)
                        
                        if hist_data:
                            historical_data_cache[symbol] = hist_data
                            _preload_live(live_data[tk], hist_data)
                        
                        print(f"   ✓ Added {symbol}")
                except Exception as e:
                    print(f"   Error adding {symbol}: {e}")
                time.sleep(0.05)
            
            if new_tokens and feed_opened:
                subscribe_symbols(new_tokens)
                print(f"✓ Subscribed to {len(new_tokens)} new symbols\n")
    except Exception as e:
        pass

# ============================================
# INDICATOR SETTINGS FROM EXCEL (LOGIN!M:O) + INDICATOR_HISTORY SHEET
# ============================================

_settings_last = None          # last applied settings dict
_last_settings_poll = 0
_history_sig = None
_history_rows_written = 0
_extra_base = {}               # on-demand candles for INDICATOR_HISTORY symbols not on the symbols sheet
HISTORY_HEADER_ROW, HISTORY_FIRST_ROW = 5, 6


def _login_ws():
    return excel_name.sheets['LOGIN']


def load_settings_at_startup():
    """Create the settings block (never overwrites values), read, validate, apply."""
    global _settings_last
    ws = _login_ws()
    indicator_settings.ensure_block(ws)
    values, warnings = indicator_settings.read_settings(ws)
    indicator_settings.apply_to(Config, values)
    _settings_last = values
    for w in warnings:
        print(f"⚠️ Setting {w}")
    msg = f"OK {datetime.now():%H:%M:%S} loaded"
    if warnings:
        msg += f" | {len(warnings)} warning(s): " + "; ".join(warnings)
    indicator_settings.write_status(ws, msg[:250])
    print(f"⚙️  Indicator settings read from sheet LOGIN column M:N "
          f"(AS_OF_DATE = {Config.AS_OF_DATE or 'latest'}, candles = {history_days()} days)")


def ensure_history_sheet():
    ws = _ensure_sheet(Config.HISTORY_SHEET)
    if ws.range("A1").value in (None, ""):
        ws.range("A1:A3").value = [["SYMBOL"], ["DAYS"], ["STATUS"]]
    if ws.range("B2").value in (None, ""):
        ws.range("B2").value = 30
    if ws.range("B1").value in (None, ""):
        try:
            first = excel_name.sheets['symbols'].range(f"{Config.SYMBOL_COL}2").value
            if first:
                ws.range("B1").value = str(first).strip()
        except Exception:
            pass
    ws.range("C1").value = "type a symbol (e.g. NSE:RELIANCE-EQ) and how many days; the table below updates by itself"
    return ws


def _set_history_status(ws, text):
    try:
        ws.range("B3").value = text
    except Exception:
        pass


def _history_base(symbol_key, trading_symbol):
    hist = historical_data_cache.get(symbol_key)
    if hist is not None and hist.get('base') is not None:
        return hist['base'], hist
    cached = _extra_base.get(symbol_key)
    if cached is not None and cached[1] == datetime.now().date():
        return cached[0], cached[0]
    df, _src = _download_df(trading_symbol)
    if df is None:
        return None, None
    base = df[['datetime', 'open', 'high', 'low', 'close', 'volume']].reset_index(drop=True)
    _extra_base[symbol_key] = (base, datetime.now().date())
    return base, base


def refresh_indicator_history(force=False):
    """Fill INDICATOR_HISTORY with the last N days of indicator values for the typed symbol."""
    global _history_sig, _history_rows_written
    try:
        ws = excel_name.sheets[Config.HISTORY_SHEET]
    except Exception:
        ws = ensure_history_sheet()
    top = ws.range("B1:B2").value
    raw_symbol, raw_days = top[0], top[1]
    if raw_symbol in (None, ""):
        sig = ("blank",)
        if sig != _history_sig:
            _set_history_status(ws, "type a symbol in B1 (e.g. NSE:RELIANCE-EQ)")
            _history_sig = sig
        return
    try:
        days = int(float(raw_days)) if raw_days not in (None, "") else 30
    except (TypeError, ValueError):
        days = 30
    days = max(1, min(days, Config.HISTORY_MAX_DAYS))

    try:
        key = normalized_symbol_key(str(raw_symbol).strip().upper())
        exchange, trading_symbol = parse_symbol_input(key)
    except Exception as exc:
        sig = ("bad", str(raw_symbol))
        if sig != _history_sig:
            _set_history_status(ws, f"bad symbol: {exc}")
            _history_sig = sig
        return
    if exchange != "NSE":
        sig = ("nse", key)
        if sig != _history_sig:
            _set_history_status(ws, "only NSE symbols have daily indicator history")
            _history_sig = sig
        return

    hist = historical_data_cache.get(key)
    params = indicator_params()
    sig = (key, days, tuple(sorted(params.items(), key=lambda kv: kv[0])), id(hist) if hist else None,
           datetime.now().date())
    if sig == _history_sig and not force:
        return
    _history_sig = sig
    _set_history_status(ws, f"computing {days} days for {key} ...")
    base, _ = _history_base(key, trading_symbol)
    if base is None or len(base) < 3:
        _set_history_status(ws, f"no candle data for {key}")
        return
    confirmed = base['datetime'].iloc[-1].date() != datetime.now().date()
    tbl = indicator_history.table(base, days, params, confirmed)
    values = []
    for row in tbl.itertuples(index=False):
        values.append([('' if (isinstance(v, float) and v != v) else (round(v, 2) if isinstance(v, float) else v))
                       for v in row])
    cols = len(indicator_history.TABLE_COLUMNS)
    last_col = get_column_letter(cols)
    ws.range(f"A{HISTORY_HEADER_ROW}:{last_col}{HISTORY_HEADER_ROW}").value = indicator_history.TABLE_COLUMNS
    if _history_rows_written > len(values):
        ws.range(f"A{HISTORY_FIRST_ROW + len(values)}:{last_col}{HISTORY_FIRST_ROW + _history_rows_written}").clear_contents()
    if values:
        ws.range(f"A{HISTORY_FIRST_ROW}:{last_col}{HISTORY_FIRST_ROW + len(values) - 1}").value = values
    _history_rows_written = len(values)
    _set_history_status(ws, f"OK {datetime.now():%H:%M:%S} | {key} | {len(values)} days (newest first)")


def reload_indicators(refetch):
    """Recompute indicators for every loaded symbol (refetch=True re-reads candles via the SQLite cache)."""
    global _history_sig
    _extra_base.clear()
    for symbol, tk in list(symbol_tokens.items()):
        try:
            exchange, trading_symbol = parse_symbol_input(symbol)
        except Exception:
            continue
        if exchange != "NSE":
            continue
        old = historical_data_cache.get(symbol)
        if refetch or old is None or old.get('base') is None:
            new = fetch_historical_data(trading_symbol)
        else:
            new = _build_hist(old['base'], trading_symbol, old.get('source', ''))
        if new:
            historical_data_cache[symbol] = new
            d = live_data.get(tk)
            if d is not None:
                _preload_live(d, new)
    _history_sig = None


def check_settings():
    """Poll LOGIN!M:N; when something changed apply it right away (no restart)."""
    global _settings_last
    ws = _login_ws()
    values, warnings = indicator_settings.read_settings(ws)
    if values == _settings_last:
        return
    changed = sorted(k for k in values if _settings_last is None or values[k] != _settings_last.get(k))
    indicator_settings.apply_to(Config, values)
    _settings_last = values
    refetch = any(k in indicator_settings.DATA_KEYS for k in changed)
    print(f"⚙️  Settings changed: {', '.join(changed)} -> {'reloading candles + ' if refetch else ''}recalculating")
    for w in warnings:
        print(f"⚠️ Setting {w}")
    t0 = time.time()
    try:
        reload_indicators(refetch)
        msg = f"OK {datetime.now():%H:%M:%S} applied {', '.join(changed)} ({time.time() - t0:.1f}s)"
    except Exception as exc:
        msg = f"ERROR {datetime.now():%H:%M:%S} {exc}"
        print(f"❌ Applying settings failed: {exc}")
    if warnings:
        msg += " | " + "; ".join(warnings)
    indicator_settings.write_status(ws, msg[:250])


def poll_settings_and_history():
    """Called from the main loop; does real work at most every SETTINGS_POLL_SECONDS."""
    global _last_settings_poll
    now = time.time()
    if now - _last_settings_poll < Config.SETTINGS_POLL_SECONDS:
        return
    _last_settings_poll = now
    try:
        check_settings()
    except Exception as exc:
        print(f"Settings check error: {exc}")
    try:
        refresh_indicator_history()
    except Exception as exc:
        print(f"INDICATOR_HISTORY error: {exc}")


# ============================================
# MAIN EXCEL LOOP
# ============================================

def start_excel_loop():
    global last_symbol_check, tick_count
    
    print("\n🚀 Trading System Running...")
    print(f"   📊 LIVE DATA (Columns {Config.LIVE_START}-{LIVE_END}) - {LIVE_COUNT} columns")
    print(f"   📋 ORDERS (Columns {Config.ORDER_START}-{ORDER_END}) - {ORDER_COUNT} columns")
    print(f"   📜 HISTORICAL DATA (Columns {Config.HIST_START}-{HIST_END}) - {HIST_COUNT} columns")
    print("   🟣 WEEKLY HISTORICAL (Columns AL:AX) - 13 columns")
    print("   🟣 WEEKLY LIVE (Columns AY:BL) - 14 columns")
    print("   ⚡ NADARYA BUY / NADARYA SELL in live + historical + weekly columns\n")
    
    last_save = time.time()
    last_status = time.time()
    
    while True:
        try:
            current = time.time()
            
            if current - last_symbol_check >= 5:
                check_new_symbols()
                last_symbol_check = current
            
            # Priority 1: publish the latest WebSocket prices and indicators first.
            # Order REST calls already run in worker threads; their Excel results follow.
            update_excel_bulk()
            poll_settings_and_history()

            process_order_updates()
            process_order_results()
            reconcile_order_statuses()
            check_order_signals()
            
            # Saving the workbook on every 30 loop passes caused frequent Excel stalls.
            # Persist live workbook changes on a slower interval instead.
            if current - last_save >= 30:
                try:
                    excel_name.save()
                except Exception:
                    pass
                last_save = current
            
            if current - last_status >= 10:
                active = sum(1 for d in live_data.values() if d.get('ltp', 0) > 0)
                print(f"📊 Active: {active}/{len(symbol_tokens)} | Ticks: {tick_count}")
                tick_count = 0
                last_status = current
            
            time.sleep(0.05)
            
        except Exception:
            time.sleep(0.1)

# ============================================
# MAIN
# ============================================

def main(auth_code_provider=None):
    global historical_data_cache
    
    print("\n" + "="*80)
    print("🚀 DAILY TIMEFRAME - TRADINGVIEW MATCH")
    print("="*80)
    
    print("\n[1/5] Setting up Excel...")
    if not migrate_spacer_columns():
        return
    setup_excel_headers()
    try:
        load_settings_at_startup()
        ensure_history_sheet()
    except Exception as exc:
        print(f"⚠️ Indicator settings sheet problem ({exc}); using built-in defaults")
    
    print("\n[2/5] Logging to Shoonya...")
    if not Shoonya_login(auth_code_provider=auth_code_provider):
        print("❌ Login failed!")
        return
    
    print("\n[3/5] Loading Shoonya instrument masters...")
    loaded_exchanges = instrument_master.load(SUPPORTED_EXCHANGES, force_refresh=True)
    print(f"Instrument masters ready: {', '.join(loaded_exchanges) if loaded_exchanges else 'none; searchscrip fallback enabled'}")

    print("\n[4/5] Reading symbols...")
    symbols = read_symbols_from_excel()
    
    if not symbols:
        default = ["NSE:RELIANCE-EQ", "NSE:TCS-EQ", "NSE:INFY-EQ"]
        for i, sym in enumerate(default, start=2):
            excel_name.sheets['symbols'].range(f"A{i}").value = sym
        excel_name.save()
        symbols = default
        print(f"✅ Added default symbols")
    
    print(f"📋 Processing {len(symbols)} symbols...")
    
    print(f"\n[5/5] Loading tokens and historical data...")
    _t_hist = time.time()
    for i, symbol in enumerate(symbols, 1):
        exchange, trading_symbol = parse_symbol_input(symbol)
        token = GetToken(exchange, trading_symbol)
        if token:
            tk = f"{exchange}|{token}"
            symbol_tokens[symbol] = tk
            token_symbols[tk] = symbol
            # Keep the original indicator/history calculations for NSE symbols.
            hist_data = fetch_historical_data(trading_symbol) if exchange == "NSE" else None
            
            live_data[tk] = {
                'symbol': symbol,
                'first_tick': True,
                'ltp': 0, 'volume': 0,
                'open': 0, 'high': 0, 'low': 0, 'close': 0,
                'rsi': 50, 'stoch_rsi': 50, 'sma_stoch': 50,
                'nwe_buy': '', 'nwe_sell': '', 'pink': '', 'mirror': '',
                'indicator_dirty': False,
                'bb_upper': 0, 'bb_middle': 0, 'bb_lower': 0,
                'buy': '', 'sell': '', 'signal': '',
                'prev_close': 0,
                'prev_bb_upper': 0,
                'prev_bb_lower': 0,
                'prev_sma_stoch': 50,
                'timestamp': None
            }
            
            if hist_data:
                historical_data_cache[symbol] = hist_data
                _preload_live(live_data[tk], hist_data)
                print(f"   [{i:2}/{len(symbols)}] ✓ {symbol}  [{hist_data.get('source', '')}]")
            else:
                status = "live token loaded; historical indicators are NSE-only" if exchange != "NSE" else "historical data unavailable"
                print(f"   [{i:2}/{len(symbols)}] ✓ {symbol} ({status})")
        else:
            print(f"   [{i:2}/{len(symbols)}] ✗ {symbol} (token not found)")
        time.sleep(0.03)
    
    print(f"\n✅ Initialized {len(symbol_tokens)} symbols")
    _elapsed = time.time() - _t_hist
    print(f"⏱ History + indicators loaded in {_elapsed:.1f}s "
          f"({_elapsed / max(len(symbols), 1):.2f}s per symbol). Nothing is written to Excel until this finishes.")
    
    if len(symbol_tokens) == 0:
        print("❌ No symbols!")
        return
    
    print("\nStarting WebSocket...")
    
    try:
        api.start_websocket(
            subscribe_callback=on_ticks,
            order_update_callback=on_order,
            socket_open_callback=on_open,
            socket_close_callback=on_close,
            socket_error_callback=on_ws_error
        )
        
        for _ in range(15):
            if feed_opened:
                break
            time.sleep(1)
        
        if feed_opened:
            if symbol_tokens:
                subscribe_symbols(list(symbol_tokens.values()))
            start_excel_loop()
        else:
            print("❌ WebSocket failed!")
            
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    main()
