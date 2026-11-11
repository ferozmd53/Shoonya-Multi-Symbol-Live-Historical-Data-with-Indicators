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
                url = "https://api.shoonya.com/NorenWClientAPI/GenAcsTok"
                # Shoonya OAuth requires SHA256(client_id + secret_key + auth_code).
                checksum = _hashlib.sha256(
                    (client_id + secret_key + auth_code).encode("utf-8")
                ).hexdigest()
                inner = {
                    "uid": uid,
                    "code": auth_code,
                    "checksum": checksum,
                }

                # ── CRITICAL: send 'jData=<raw-json>' as a STRING, not a dict.
                #    A dict would be URL-encoded by requests (e.g. %7B%22uid%22...),
                #    which Shoonya rejects with "jData is not valid json object".
                payload = "jData=" + _json.dumps(inner)

                try:
                    r = _req.post(
                        url,
                        data=payload,
                        headers={"Content-Type": "text/plain"},
                        timeout=30,
                    )
                    r.raise_for_status()
                    ret = r.json()
                except Exception as e:
                    print(f"❌ getAccessToken HTTP error: {e}")
                    return None

                if ret.get("stat") == "Ok":
                    token = ret.get("access_token") or ret.get("susertoken")
                    response_uid = ret.get("uid") or uid
                    actid = ret.get("actid") or response_uid
                    if not token:
                        print(f"❌ getAccessToken response has no token: {ret}")
                        return None

                    # NorenRestApiPy uses the private susertoken for REST/WebSocket calls.
                    self._NorenApi__susertoken = token
                    self._NorenApi__access_token = token
                    self._NorenApi__username = response_uid
                    self._NorenApi__accountid = actid

                    self.access_token = token
                    self.username = response_uid
                    self.accountid = actid
                    print(f"✅ getAccessToken OK (uid={response_uid})")
                    return (token, response_uid, ret.get("refresh_token"), actid)

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
warnings.filterwarnings('ignore')

# ============================================
# GLOBAL VARIABLES
# ============================================

# Locate the workbook beside run.py / in the working folder first.
# The installed package may live in Python\\Lib\\site-packages, so do not
# assume the workbook is stored beside this module.
WORKBOOK_NAME = "PythonTrader_Terminal_LMT.xlsm"
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
indicator_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ShoonyaIndicators")
indicator_inflight = set()
indicator_inflight_lock = threading.Lock()
order_inflight = set()
order_inflight_lock = threading.Lock()
order_reconcile_future = None
last_order_reconcile = 0
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
        'RSI', 'StochRSI', 'SMA Stoch',
        'BB Upper', 'BB Middle', 'BB Lower',
        'BUY', 'SELL', 'Signal', 'Last Update'
    ]

def get_hist_headers():
    """Get historical column headers"""
    return [
        'Date', 'Open', 'High', 'Low', 'Close', 'Volume',
        'RSI', 'StochRSI', 'SMA Stoch',
        'BB Upper', 'BB Middle', 'BB Lower',
        'BUY', 'SELL', 'Signal'
    ]

def get_order_headers():
    """R:AB order panel with BUY/SELL limit price (PRC), broker status and order number."""
    return [
        'Quantity',
        'BUY', 'BUY Type', 'BUY PRC', 'BUY Broker Status', 'BUY Order No',
        'SELL', 'SELL Type', 'SELL PRC', 'SELL Broker Status', 'SELL Order No'
    ]

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
    EXCEL_UPDATE_INTERVAL = 0.1          # Raw LTP/OHLCV refresh
    INDICATOR_EXCEL_UPDATE_INTERVAL = 0.5
    HIST_EXCEL_UPDATE_INTERVAL = 5.0
    LOAD_DAYS = 500
    KEEP_DAYS = 500
    
    # ==========================================
    # COLUMN CONFIGURATION - CHANGE ANY START COLUMN
    # ==========================================
    
    SYMBOL_COL = 'A'           # Symbols column
    
    LIVE_START = 'B'           # Live data starts here
    HIST_START = 'AC'          # Historical data starts after order columns R:AB
    ORDER_START = 'R'          # Order data starts here

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
                    if buy_sell.upper() == 'BUY':
                        price = current_price + 5
                    else:
                        price = current_price - 5
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

def fetch_historical_data(symbol):
    try:
        end_date = datetime.now()
        start_date = end_date - timedelta(days=Config.LOAD_DAYS)
        
        start_epoch = int(start_date.timestamp())
        end_epoch = int(end_date.timestamp())
        
        ret = api.get_daily_price_series(
            exchange="NSE", tradingsymbol=symbol,
            startdate=str(start_epoch), enddate=str(end_epoch)
        )
        
        if not ret:
            return None
        
        parsed_data = []
        for item in ret:
            if isinstance(item, str):
                try:
                    parsed_data.append(json.loads(item))
                except:
                    continue
        
        if not parsed_data:
            return None
        
        df = pd.DataFrame(parsed_data)
        df.rename(columns={
            'time': 'datetime', 'into': 'open', 'inth': 'high',
            'intl': 'low', 'intc': 'close', 'intv': 'volume'
        }, inplace=True)
        
        df['datetime'] = pd.to_datetime(df['datetime'], format='%d-%b-%Y')
        for col in ['open', 'high', 'low', 'close', 'volume']:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')
        
        df = df.sort_values('datetime')
        df = df.dropna().reset_index(drop=True)
        
        if len(df) < Config.BB_LENGTH:
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
            'buy': buy_signal,
            'sell': sell_signal,
            'signal': signal_text,
            'prev_close': day_before['close'],
            'prev_bb_upper': day_before['BB_UPPER'],
            'prev_bb_lower': day_before['BB_LOWER'],
            'prev_sma_stoch': day_before['SMA_STOCH']
        }
        
        return {
            'df': df,
            'all_closes': df['close'].tolist(),
            'all_highs': df['high'].tolist(),
            'all_lows': df['low'].tolist(),
            'yesterday': yesterday_data
        }
        
    except Exception as e:
        print(f"Error fetching data: {e}")
        return None

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
        result = api.getAccessToken(auth, secret, client_id, userid)
        if not result and callable(auth_code_provider):
            print("⚠️ Saved AUTH code was rejected. Requesting one fresh OAuth code and retrying.")
            login_sheet.range('B7').value = ""
            auth = obtain_fresh_code()
            if auth:
                api = ShoonyaApiPy()
                result = api.getAccessToken(auth, secret, client_id, userid)
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
    global pending_order_updates
    with order_update_lock:
        updates = pending_order_updates[:]
        pending_order_updates.clear()
    for update in updates:
        try:
            apply_order_update(update)
        except Exception as exc:
            print(f"Order update write error: {exc}")


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
    """Write only broker-returned order numbers and broker statuses to Excel."""
    ws = excel_name.sheets['symbols']
    while True:
        try:
            item = order_result_queue.get_nowait()
        except queue.Empty:
            break
        key = item["key"]
        try:
            result = item["result"]
            if not isinstance(result, dict):
                print(f"❌ {item['side']} {key[0]}: no broker response; no broker status written.")
                continue

            if result.get("norenordno"):
                order_no = normalize_order_no(result["norenordno"])
                ws.range(f"{item['order_col']}{item['row']}").value = order_no
                submitted_prc = result.get("_submitted_prc") or result.get("prc")
                if submitted_prc not in ('', None):
                    prc_col = ORDER_COLS[3 if item['side'] == 'BUY' else 8]
                    ws.range(f"{prc_col}{item['row']}").value = submitted_prc
                broker_status = result.get("status") or result.get("order_status")
                if broker_status:
                    ws.range(f"{item['status_col']}{item['row']}").value = normalize_order_status(broker_status)
                print(f"📋 Broker response for {item['side']} {key[0]}: "
                      f"{broker_status or 'Order number received'} | {order_no}")
            elif result.get("local_error"):
                print(f"⚠️ Local submission error for {item['side']} {key[0]}: "
                      f"{result.get('emsg', 'Unknown error')}")
            elif str(result.get("stat", "")).lower() in ("not_ok", "not ok") and result.get("emsg"):
                # Shoonya's explicit Not_Ok response is a broker rejection.
                ws.range(f"{item['status_col']}{item['row']}").value = (
                    "REJECTED | " + str(result["emsg"])
                )
                print(f"📋 Broker rejected {item['side']} {key[0]}: {result['emsg']}")
            else:
                print(f"⚠️ No broker order number/status for {item['side']} {key[0]}: {result}")
        except Exception as exc:
            print(f"Excel broker-result write error for {key}: {exc}")
        finally:
            with order_inflight_lock:
                order_inflight.discard(key)

def reconcile_order_statuses():
    """Fetch broker order book asynchronously; apply only broker-reported statuses."""
    global last_order_reconcile, order_reconcile_future
    now = time.time()
    if order_reconcile_future is not None and order_reconcile_future.done():
        try:
            orders = order_reconcile_future.result()
            if isinstance(orders, list):
                ws = excel_name.sheets['symbols']
                symbols_data = ws.range(f"{Config.SYMBOL_COL}2:{Config.SYMBOL_COL}200").value or []
                panel = ws.range("R2:AB200").value or []
                tracked = {}
                for idx, row in enumerate(panel):
                    row = list(row) if isinstance(row, (list, tuple)) else [row]
                    row += [""] * (11 - len(row))
                    row_num = idx + 2
                    for order_index, status_index in ((5, 4), (10, 9)):
                        order_no = normalize_order_no(row[order_index])
                        if order_no:
                            tracked[order_no] = (row_num, ORDER_COLS[status_index], ORDER_COLS[order_index],
                                                 str(row[status_index] or ""))
                for broker_order in orders:
                    if not isinstance(broker_order, dict):
                        continue
                    order_no = normalize_order_no(broker_order.get('norenordno'))
                    target = tracked.get(order_no)
                    if not target:
                        continue
                    row_num, status_col, order_col, old_status = target
                    prc_col = ORDER_COLS[3] if status_col == ORDER_COLS[4] else ORDER_COLS[8]
                    broker_prc = broker_order.get('prc') or broker_order.get('price')
                    if broker_prc not in ('', None):
                        ws.range(f"{prc_col}{row_num}").value = broker_prc
                    broker_status = broker_order.get('status') or broker_order.get('order_status')
                    if not broker_status:
                        continue
                    new_status = order_status_text(broker_order)
                    old_base = normalize_order_status(old_status.split('|')[0].strip()) if old_status else ''
                    new_base = normalize_order_status(broker_status)
                    final_states = ('COMPLETE', 'REJECTED', 'CANCELED')
                    if old_base in final_states and new_base not in final_states:
                        continue
                    if new_status != old_status:
                        ws.range(f"{status_col}{row_num}").value = new_status
        except Exception as exc:
            print(f"Broker order-book read/apply error: {exc}")
        finally:
            order_reconcile_future = None

    if order_reconcile_future is None and now - last_order_reconcile >= 2:
        last_order_reconcile = now
        order_reconcile_future = order_executor.submit(api.get_order_book)

def check_order_signals():
    """Bulk-scan manual inputs; submit independently per symbol without Excel-side flags."""
    try:
        ws = excel_name.sheets['symbols']
        symbols_data = ws.range(f"{Config.SYMBOL_COL}2:{Config.SYMBOL_COL}200").value or []
        panel = ws.range("R2:AB200").value or []
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
                ("SELL", 6, 7, 9, 10)
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

def _calculate_live_indicators(symbol, ltp):
    """Run the existing RSI/Stoch RSI/Bollinger calculations away from the tick callback."""
    hist = historical_data_cache.get(symbol)
    if not hist:
        return None
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
    rsi = (100 - (100 / (1 + rs))).fillna(50)
    rsi_low = rsi.rolling(Config.STOCH_LENGTH).min()
    rsi_high = rsi.rolling(Config.STOCH_LENGTH).max()
    stoch = (100 * (rsi - rsi_low) / (rsi_high - rsi_low)).fillna(50)
    sma_stoch = stoch.rolling(3).mean()
    sma = df['close'].rolling(Config.BB_LENGTH).mean()
    stdev = df['close'].rolling(Config.BB_LENGTH).std(ddof=0)
    bb_upper = sma + (Config.BB_STD * stdev)
    bb_lower = sma - (Config.BB_STD * stdev)
    latest_rsi = rsi.iloc[-1] if len(rsi) else 50
    latest_stoch = stoch.iloc[-1] if len(stoch) else 50
    latest_sma_stoch = sma_stoch.iloc[-1] if len(sma_stoch) else 50
    latest_bb_upper = bb_upper.iloc[-1] if len(bb_upper) else 0
    latest_bb_middle = sma.iloc[-1] if len(sma) else 0
    latest_bb_lower = bb_lower.iloc[-1] if len(bb_lower) else 0
    prev_bb_upper = bb_upper.iloc[-2] if len(bb_upper) >= 2 else latest_bb_upper
    prev_bb_lower = bb_lower.iloc[-2] if len(bb_lower) >= 2 else latest_bb_lower
    prev_close = all_closes[-2] if len(all_closes) >= 2 else ltp
    prev_sma_stoch = sma_stoch.iloc[-2] if len(sma_stoch) >= 2 else latest_sma_stoch
    buy_signal = prev_close < prev_bb_lower and ltp > latest_bb_lower and prev_sma_stoch < Config.STOCH_LOWER
    sell_signal = prev_close > prev_bb_upper and ltp < latest_bb_upper and prev_sma_stoch > Config.STOCH_UPPER
    return {
        'rsi': round(latest_rsi, 2), 'stoch_rsi': round(latest_stoch, 2),
        'sma_stoch': round(latest_sma_stoch, 2), 'bb_upper': round(latest_bb_upper, 2),
        'bb_middle': round(latest_bb_middle, 2), 'bb_lower': round(latest_bb_lower, 2),
        'prev_close': prev_close, 'prev_bb_upper': prev_bb_upper,
        'prev_bb_lower': prev_bb_lower, 'prev_sma_stoch': prev_sma_stoch,
        'buy': 1 if buy_signal else '', 'sell': 1 if sell_signal else '',
        'signal': 'BUY' if buy_signal else ('SELL' if sell_signal else '')
    }


def _indicator_worker_done(future, token_key):
    try:
        values = future.result()
        if values and token_key in live_data:
            live_data[token_key].update(values)
    except Exception as exc:
        print(f"⚠️ Indicator calculation failed for {token_key}: {exc}")
    finally:
        with indicator_inflight_lock:
            indicator_inflight.discard(token_key)


def process_indicator_updates():
    """Schedule indicator calculations separately so WebSocket LTP/OHLCV stays lightweight."""
    for token_key, data in list(live_data.items()):
        if not data.get('indicator_dirty') or data.get('ltp', 0) <= 0:
            continue
        symbol = data.get('symbol')
        if not symbol or symbol not in historical_data_cache:
            data['indicator_dirty'] = False
            continue
        with indicator_inflight_lock:
            if token_key in indicator_inflight:
                continue
            indicator_inflight.add(token_key)
            data['indicator_dirty'] = False
        try:
            future = indicator_executor.submit(_calculate_live_indicators, symbol, data.get('ltp', 0))
            future.add_done_callback(lambda f, key=token_key: _indicator_worker_done(f, key))
        except Exception as exc:
            with indicator_inflight_lock:
                indicator_inflight.discard(token_key)
            data['indicator_dirty'] = True
            print(f"⚠️ Could not schedule indicators for {token_key}: {exc}")


def on_ticks(tick):
    """Update raw market fields immediately; indicator work runs in separate workers."""
    global live_data, tick_count
    try:
        if isinstance(tick, str):
            tick = json.loads(tick)
        tick_count += 1
        key = f"{tick['e']}|{tick['tk']}"
        if key not in live_data:
            return
        data = live_data[key]
        ltp = safe_float(tick.get('lp', data.get('ltp', 0)))
        volume = safe_int(tick.get('v', data.get('volume', 0)))
        open_price = safe_float(tick.get('o', data.get('open', 0)))
        if data.get('first_tick', True):
            data['first_tick'] = False
            data['open'] = open_price if open_price > 0 else ltp
            data['high'] = ltp
            data['low'] = ltp
        if ltp > data.get('high', 0):
            data['high'] = ltp
        if ltp < data.get('low', 999999):
            data['low'] = ltp
        data['ltp'] = ltp
        data['volume'] = volume
        data['timestamp'] = datetime.now()
        data['close'] = ltp
        data['indicator_dirty'] = True
    except Exception as exc:
        print(f"⚠️ WebSocket tick update error: {type(exc).__name__}: {exc}")


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
    """Refresh raw B:G frequently; refresh indicators H:Q and history AC:AQ separately."""
    global last_excel_update, last_indicator_excel_update, last_hist_excel_update
    try:
        now = time.time()
        raw_due = now - last_excel_update >= Config.EXCEL_UPDATE_INTERVAL
        indicator_due = now - last_indicator_excel_update >= Config.INDICATOR_EXCEL_UPDATE_INTERVAL
        hist_due = now - last_hist_excel_update >= Config.HIST_EXCEL_UPDATE_INTERVAL
        if not (raw_due or indicator_due or hist_due):
            return
        ws = excel_name.sheets['symbols']
        symbols_data = ws.range(f"{Config.SYMBOL_COL}2:{Config.SYMBOL_COL}200").value or []
        raw_rows, indicator_rows, hist_rows = [], [], []
        for symbol_cell in symbols_data:
            if not symbol_cell:
                if raw_due: raw_rows.append([''] * 6)
                if indicator_due: indicator_rows.append([''] * 10)
                if hist_due: hist_rows.append([''] * HIST_COUNT)
                continue
            try:
                symbol = normalized_symbol_key(symbol_cell)
            except ValueError:
                if raw_due: raw_rows.append([''] * 6)
                if indicator_due: indicator_rows.append([''] * 10)
                if hist_due: hist_rows.append([''] * HIST_COUNT)
                continue
            token_key = symbol_tokens.get(symbol)
            data = live_data.get(token_key) if token_key else None
            hist = historical_data_cache.get(symbol)
            if raw_due:
                raw_rows.append([
                    data.get('ltp', 0) if data and data.get('ltp', 0) > 0 else '',
                    data.get('open', 0) if data and data.get('open', 0) > 0 else '',
                    data.get('high', 0) if data and data.get('high', 0) > 0 else '',
                    data.get('low', 0) if data and data.get('low', 0) > 0 else '',
                    data.get('close', 0) if data and data.get('close', 0) > 0 else '',
                    data.get('volume', 0) if data and data.get('volume', 0) > 0 else ''
                ])
            if indicator_due:
                if data:
                    indicator_rows.append([
                        data.get('rsi', ''), data.get('stoch_rsi', ''),
                        data.get('sma_stoch', ''), data.get('bb_upper', ''),
                        data.get('bb_middle', ''), data.get('bb_lower', ''),
                        data.get('buy', ''), data.get('sell', ''),
                        data.get('signal', ''),
                        data['timestamp'].strftime('%H:%M:%S') if data.get('timestamp') else ''
                    ])
                else:
                    indicator_rows.append([''] * 10)
            if hist_due:
                yesterday = hist['yesterday'] if hist else {}
                hist_rows.append([
                    yesterday.get('date', ''), yesterday.get('open', ''),
                    yesterday.get('high', ''), yesterday.get('low', ''),
                    yesterday.get('close', ''), yesterday.get('volume', ''),
                    yesterday.get('rsi', ''), yesterday.get('stoch_rsi', ''),
                    yesterday.get('sma_stoch', ''), yesterday.get('bb_upper', ''),
                    yesterday.get('bb_middle', ''), yesterday.get('bb_lower', ''),
                    yesterday.get('buy', ''), yesterday.get('sell', ''),
                    yesterday.get('signal', '')
                ])
        if symbols_data:
            last_row = 1 + len(symbols_data)
            if raw_due and raw_rows:
                raw_start = Config.LIVE_START
                raw_end = get_column_letter(get_column_number(raw_start) + 5)
                ws.range(f"{raw_start}2:{raw_end}{last_row}").value = raw_rows
                last_excel_update = now
            if indicator_due and indicator_rows:
                indicator_start = get_column_letter(get_column_number(Config.LIVE_START) + 6)
                ws.range(f"{indicator_start}2:{LIVE_END}{last_row}").value = indicator_rows
                last_indicator_excel_update = now
            if hist_due and hist_rows:
                ws.range(f"{Config.HIST_START}2:{HIST_END}{last_row}").value = hist_rows
                last_hist_excel_update = now
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
            ws.range("AC2:AQ200").value = old_history
        # Write the transformed order block last because it now occupies Y:AB too.
        if migrated_orders:
            ws.range("R2:AB200").value = migrated_orders
        print("✅ Migrated existing order/history columns to the new layout.")
    except Exception as exc:
        print(f"⚠️ Workbook layout migration skipped: {exc}")


# ============================================
# SETUP HEADERS - DYNAMIC
# ============================================

def setup_excel_headers():
    try:
        ws = excel_name.sheets['symbols']
        migrate_legacy_layout(ws)
        # U and Z were local DONE flags in the previous version; they are obsolete.
        ws.range("U2:U200").clear_contents()
        ws.range("Z2:Z200").clear_contents()
        ws.range("1:1").clear_contents()
        
        # Build headers in correct order: Symbol, LIVE, ORDERS, HISTORICAL
        headers = ['Symbol']
        headers.extend(get_live_headers())
        headers.extend(get_order_headers())
        headers.extend(get_hist_headers())
        
        # Write headers
        for col_idx, header in enumerate(headers, start=1):
            cell = ws.range((1, col_idx))
            cell.value = header
            
            # Color coding based on section
            if header in ['BUY', 'SELL', 'Signal']:
                cell.color = (255, 100, 100)
            elif col_idx > len(get_live_headers()) + 1 and col_idx <= len(get_live_headers()) + len(get_order_headers()) + 1:
                # Order section
                cell.color = (0, 100, 0)
                cell.font.color = (255, 255, 255)
            elif col_idx > len(get_live_headers()) + len(get_order_headers()) + 1:
                # Historical section
                cell.color = (146, 96, 54)
            else:
                # Live section
                cell.color = (54, 96, 146)
            cell.font.color = (255, 255, 255)
            cell.font.bold = True
        
        # Set column widths
        for col in ALL_COLS:
            ws.range(f'{col}:{col}').column_width = 12
        ws.range('A:A').column_width = 20
        for col in ('V', 'AA'):
            ws.range(f'{col}:{col}').column_width = 34
        for col in ('W', 'AB'):
            ws.range(f'{col}:{col}').column_width = 20
        for col in ('T', 'Y', 'U', 'Z'):
            ws.range(f'{col}:{col}').column_width = 12

        return True
    except Exception:
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
                            'indicator_dirty': False,
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
                            live_data[tk]['rsi'] = hist_data['yesterday']['rsi']
                            live_data[tk]['stoch_rsi'] = hist_data['yesterday']['stoch_rsi']
                            live_data[tk]['sma_stoch'] = hist_data['yesterday']['sma_stoch']
                            live_data[tk]['bb_upper'] = hist_data['yesterday']['bb_upper']
                            live_data[tk]['bb_middle'] = hist_data['yesterday']['bb_middle']
                            live_data[tk]['bb_lower'] = hist_data['yesterday']['bb_lower']
                            live_data[tk]['prev_close'] = hist_data['yesterday']['prev_close']
                            live_data[tk]['prev_bb_upper'] = hist_data['yesterday']['prev_bb_upper']
                            live_data[tk]['prev_bb_lower'] = hist_data['yesterday']['prev_bb_lower']
                            live_data[tk]['prev_sma_stoch'] = hist_data['yesterday']['prev_sma_stoch']
                        
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
# MAIN EXCEL LOOP
# ============================================

def start_excel_loop():
    global last_symbol_check, tick_count
    
    print("\n🚀 Trading System Running...")
    print(f"   📊 LIVE DATA (Columns {Config.LIVE_START}-{LIVE_END}) - {LIVE_COUNT} columns")
    print(f"   📋 ORDERS (Columns {Config.ORDER_START}-{ORDER_END}) - {ORDER_COUNT} columns")
    print(f"   📜 HISTORICAL DATA (Columns {Config.HIST_START}-{HIST_END}) - {HIST_COUNT} columns")
    print(f"   ⚡ Auto signals in live columns BUY/SELL/Signal\n")
    
    update_count = 0
    last_status = time.time()
    
    while True:
        try:
            current = time.time()
            
            if current - last_symbol_check >= 5:
                check_new_symbols()
                last_symbol_check = current
            
            process_order_updates()
            process_order_results()
            reconcile_order_statuses()
            check_order_signals()
            process_indicator_updates()

            update_excel_bulk()
            
            update_count += 1
            if update_count % 30 == 0:
                try:
                    excel_name.save()
                except:
                    pass
            
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
    setup_excel_headers()
    
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
                live_data[tk]['rsi'] = hist_data['yesterday']['rsi']
                live_data[tk]['stoch_rsi'] = hist_data['yesterday']['stoch_rsi']
                live_data[tk]['sma_stoch'] = hist_data['yesterday']['sma_stoch']
                live_data[tk]['bb_upper'] = hist_data['yesterday']['bb_upper']
                live_data[tk]['bb_middle'] = hist_data['yesterday']['bb_middle']
                live_data[tk]['bb_lower'] = hist_data['yesterday']['bb_lower']
                live_data[tk]['prev_close'] = hist_data['yesterday']['prev_close']
                live_data[tk]['prev_bb_upper'] = hist_data['yesterday']['prev_bb_upper']
                live_data[tk]['prev_bb_lower'] = hist_data['yesterday']['prev_bb_lower']
                live_data[tk]['prev_sma_stoch'] = hist_data['yesterday']['prev_sma_stoch']
                print(f"   [{i:2}/{len(symbols)}] ✓ {symbol}")
            else:
                status = "live token loaded; historical indicators are NSE-only" if exchange != "NSE" else "historical data unavailable"
                print(f"   [{i:2}/{len(symbols)}] ✓ {symbol} ({status})")
        else:
            print(f"   [{i:2}/{len(symbols)}] ✗ {symbol} (token not found)")
        time.sleep(0.03)
    
    print(f"\n✅ Initialized {len(symbol_tokens)} symbols")
    
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
