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
last_order_reconcile = 0
tick_count = 0
last_symbol_check = 0
last_excel_update = 0

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
    """Order layout: R Qty, S BUY Trigger, T BUY Status, U BUY Order No,
    V SELL Trigger, W SELL Status, X SELL Order No."""
    return [
        'Quantity', 'BUY Trigger', 'BUY Status', 'BUY Order No',
        'SELL Trigger', 'SELL Status', 'SELL Order No'
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
    EXCEL_UPDATE_INTERVAL = 0.1
    LOAD_DAYS = 500
    KEEP_DAYS = 500
    
    # ==========================================
    # COLUMN CONFIGURATION - CHANGE ANY START COLUMN
    # ==========================================
    
    SYMBOL_COL = 'A'           # Symbols column
    
    LIVE_START = 'B'           # Live data starts here
    HIST_START = 'Y'           # Historical data starts after order columns R-X
    ORDER_START = 'R'   #    'R'      # Order data starts here

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

def place_order(symbol, qty, buy_sell, price=0, product_type='C'):
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

        login_sheet = excel_name.sheets['LOGIN']
        algo_id_value = login_sheet.range('B2').value
        algo_id = str(algo_id_value).strip() if algo_id_value else ''
        if not algo_id:
            print("❌ Algo ID / Client ID is blank in LOGIN!B2")
            return None

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
                'status': normalize_order_status(result.get('status') or 'PENDING'),
                'time': datetime.now().strftime('%H:%M:%S')
            }
            order_history.append(order_info)
            print(f"✅ Broker accepted {buy_sell} order. Order No: {order_info['order_no']}")
            return result

        print(f"❌ Broker did not accept the order: {result}")
        return result if isinstance(result, dict) else None

    except Exception as e:
        print(f"❌ Error placing order: {e}")
        return {"stat": "Not_Ok", "emsg": str(e)}


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

def Shoonya_login():
    global api
    try:     
        api = ShoonyaApiPy()
        
        try:
            login_sheet = excel_name.sheets['LOGIN']
            userid = login_sheet.range('B3').value
            secret = login_sheet.range('B6').value
            auth = login_sheet.range('B7').value
            
            if not userid or not secret or not auth:
                return 0
                
            userid = str(userid).strip()
            secret = str(secret).strip()
            auth = str(auth).strip()
            
        except Exception as e:
            return 0
        
        cred = {'client_id': f'{userid}_U', 'secret': secret, 'uid': userid}
        result = api.getAccessToken(auth, secret, cred['client_id'], userid)
        
        if result:
            acc_tok, usrid, ref_tok, actid = result
            api.injectOAuthHeader(acc_tok, userid, actid)
            print("✅ Login Successful!")
            return 1
        else:
            print("❌ Login failed")
            
    except Exception as e:
        print(f"Login error: {e}")
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
    status = normalize_order_status(order.get('status') or order.get('order_status') or 'PENDING')
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
    """Return worksheet row and status/order-number columns for a known order ID."""
    target = normalize_order_no(order_no)
    if not target:
        return None
    ws = excel_name.sheets['symbols']
    symbols_data = ws.range(f"{Config.SYMBOL_COL}2:{Config.SYMBOL_COL}200").value or []
    # R:X = Qty, BUY trigger, BUY status, BUY order no, SELL trigger, SELL status, SELL order no.
    for idx, symbol_cell in enumerate(symbols_data):
        if not symbol_cell:
            continue
        row_num = idx + 2
        buy_no = normalize_order_no(ws.range(f"{ORDER_COLS[3]}{row_num}").value)
        if buy_no == target:
            return row_num, ORDER_COLS[2], ORDER_COLS[3]
        sell_no = normalize_order_no(ws.range(f"{ORDER_COLS[6]}{row_num}").value)
        if sell_no == target:
            return row_num, ORDER_COLS[5], ORDER_COLS[6]
    return None


def apply_order_update(order):
    if not isinstance(order, dict):
        return
    order_no = normalize_order_no(order.get('norenordno') or order.get('order_no'))
    if not order_no:
        return
    cells = find_order_cells(order_no)
    if not cells:
        return
    row_num, status_col, order_col = cells
    ws = excel_name.sheets['symbols']
    new_status = order_status_text(order)
    old_status = str(ws.range(f"{status_col}{row_num}").value or '').upper()
    old_base = normalize_order_status(old_status.split('|')[0].strip()) if old_status else ''
    new_base = normalize_order_status(order.get('status') or order.get('order_status') or 'PENDING')
    # Do not let delayed OPEN/PENDING messages replace final broker states.
    final_states = ('COMPLETE', 'REJECTED', 'CANCELED')
    if old_base in final_states and new_base not in final_states:
        return
    ws.range(f"{order_col}{row_num}").value = order_no
    ws.range(f"{status_col}{row_num}").value = new_status


def process_order_updates():
    global pending_order_updates
    updates = pending_order_updates[:]
    pending_order_updates = []
    for update in updates:
        try:
            apply_order_update(update)
        except Exception as exc:
            print(f"Order update write error: {exc}")


def reconcile_order_statuses():
    """Refresh broker status for all tracked BUY/SELL order numbers."""
    global last_order_reconcile
    now = time.time()
    if now - last_order_reconcile < 2:
        return
    last_order_reconcile = now
    try:
        orders = api.get_order_book()
        if not isinstance(orders, list):
            return
        tracked = {}
        ws = excel_name.sheets['symbols']
        symbols_data = ws.range(f"{Config.SYMBOL_COL}2:{Config.SYMBOL_COL}200").value or []
        for idx, symbol_cell in enumerate(symbols_data):
            if not symbol_cell:
                continue
            row_num = idx + 2
            for order_col, status_col in (
                (ORDER_COLS[3], ORDER_COLS[2]),
                (ORDER_COLS[6], ORDER_COLS[5])
            ):
                order_no = normalize_order_no(ws.range(f"{order_col}{row_num}").value)
                if order_no:
                    tracked[order_no] = (row_num, status_col, order_col)
        for broker_order in orders:
            if not isinstance(broker_order, dict):
                continue
            order_no = normalize_order_no(broker_order.get('norenordno'))
            target = tracked.get(order_no)
            if not target:
                continue
            row_num, status_col, order_col = target
            new_status = order_status_text(broker_order)
            old_status = str(ws.range(f"{status_col}{row_num}").value or '').upper()
            old_base = normalize_order_status(old_status.split('|')[0].strip()) if old_status else ''
            new_base = normalize_order_status(broker_order.get('status') or 'PENDING')
            if old_base in ('COMPLETE', 'REJECTED', 'CANCELED') and new_base not in ('COMPLETE', 'REJECTED', 'CANCELED'):
                continue
            ws.range(f"{order_col}{row_num}").value = order_no
            ws.range(f"{status_col}{row_num}").value = new_status
    except Exception as exc:
        print(f"Order status reconciliation error: {exc}")


def check_order_signals():
    try:
        ws = excel_name.sheets['symbols']
        symbols_data = ws.range(f"{Config.SYMBOL_COL}2:{Config.SYMBOL_COL}200").value
        if not symbols_data:
            return

        qty_col, buy_trigger_col, buy_status_col, buy_order_col, sell_trigger_col, sell_status_col, sell_order_col = ORDER_COLS

        for idx, symbol_cell in enumerate(symbols_data):
            if not symbol_cell:
                continue
            row_num = idx + 2
            try:
                symbol = normalized_symbol_key(symbol_cell)
            except ValueError:
                continue
            if symbol not in symbol_tokens:
                continue

            qty_cell = ws.range(f"{qty_col}{row_num}").value
            try:
                qty = max(1, int(float(qty_cell))) if qty_cell not in ('', None) else 1
            except Exception:
                qty = 1

            # BUY: trigger S, status T, broker order number U.
            buy_signal = str(ws.range(f"{buy_trigger_col}{row_num}").value or '').strip().upper()
            buy_status = ws.range(f"{buy_status_col}{row_num}").value
            buy_order_no = normalize_order_no(ws.range(f"{buy_order_col}{row_num}").value)
            if buy_signal == 'BUY' and not buy_status and not buy_order_no:
                print(f"\n🔔 BUY Signal detected for {symbol} (Qty: {qty})")
                result = place_order(symbol, qty, 'BUY', price=0, product_type='C')
                if isinstance(result, dict) and result.get('norenordno'):
                    order_no = normalize_order_no(result['norenordno'])
                    ws.range(f"{buy_order_col}{row_num}").value = order_no
                    ws.range(f"{buy_status_col}{row_num}").value = normalize_order_status(result.get('status') or 'PENDING')
                    ws.range(f"{buy_trigger_col}{row_num}").value = 'DONE'
                else:
                    message = (result or {}).get('emsg') or (result or {}).get('message') or str(result or 'No broker response')
                    ws.range(f"{buy_status_col}{row_num}").value = f"REJECTED | {message}"
                    ws.range(f"{buy_trigger_col}{row_num}").value = 'FAILED'

            # SELL: trigger V, status W, broker order number X.
            sell_signal = str(ws.range(f"{sell_trigger_col}{row_num}").value or '').strip().upper()
            sell_status = ws.range(f"{sell_status_col}{row_num}").value
            sell_order_no = normalize_order_no(ws.range(f"{sell_order_col}{row_num}").value)
            if sell_signal == 'SELL' and not sell_status and not sell_order_no:
                print(f"\n🔔 SELL Signal detected for {symbol} (Qty: {qty})")
                result = place_order(symbol, qty, 'SELL', price=0, product_type='C')
                if isinstance(result, dict) and result.get('norenordno'):
                    order_no = normalize_order_no(result['norenordno'])
                    ws.range(f"{sell_order_col}{row_num}").value = order_no
                    ws.range(f"{sell_status_col}{row_num}").value = normalize_order_status(result.get('status') or 'PENDING')
                    ws.range(f"{sell_trigger_col}{row_num}").value = 'DONE'
                else:
                    message = (result or {}).get('emsg') or (result or {}).get('message') or str(result or 'No broker response')
                    ws.range(f"{sell_status_col}{row_num}").value = f"REJECTED | {message}"
                    ws.range(f"{sell_trigger_col}{row_num}").value = 'FAILED'
    except Exception as e:
        print(f"Order signal error: {e}")


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
    """Refresh live and historical data only; never overwrite the order panel R:X."""
    global last_excel_update
    try:
        current_time = time.time()
        if current_time - last_excel_update < Config.EXCEL_UPDATE_INTERVAL:
            return
        last_excel_update = current_time

        ws = excel_name.sheets['symbols']
        symbols_data = ws.range(f"{Config.SYMBOL_COL}2:{Config.SYMBOL_COL}200").value or []
        live_rows = []
        hist_rows = []

        for symbol_cell in symbols_data:
            if not symbol_cell:
                live_rows.append([''] * LIVE_COUNT)
                hist_rows.append([''] * HIST_COUNT)
                continue
            try:
                symbol = normalized_symbol_key(symbol_cell)
            except ValueError:
                live_rows.append([''] * LIVE_COUNT)
                hist_rows.append([''] * HIST_COUNT)
                continue

            tk = symbol_tokens.get(symbol)
            hist = historical_data_cache.get(symbol)
            hist_yesterday = hist['yesterday'] if hist else {}
            if tk and tk in live_data:
                d = live_data[tk]
                live_rows.append([
                    d.get('ltp', 0) if d.get('ltp', 0) > 0 else '',
                    d.get('open', 0) if d.get('open', 0) > 0 else '',
                    d.get('high', 0) if d.get('high', 0) > 0 else '',
                    d.get('low', 0) if d.get('low', 0) > 0 else '',
                    d.get('close', 0) if d.get('close', 0) > 0 else '',
                    d.get('volume', 0) if d.get('volume', 0) > 0 else '',
                    d.get('rsi', ''), d.get('stoch_rsi', ''),
                    d.get('sma_stoch', ''), d.get('bb_upper', ''),
                    d.get('bb_middle', ''), d.get('bb_lower', ''),
                    d.get('buy', ''), d.get('sell', ''), d.get('signal', ''),
                    d['timestamp'].strftime('%H:%M:%S') if d.get('timestamp') else ''
                ])
            else:
                live_rows.append([''] * LIVE_COUNT)

            hist_rows.append([
                hist_yesterday.get('date', ''), hist_yesterday.get('open', ''),
                hist_yesterday.get('high', ''), hist_yesterday.get('low', ''),
                hist_yesterday.get('close', ''), hist_yesterday.get('volume', ''),
                hist_yesterday.get('rsi', ''), hist_yesterday.get('stoch_rsi', ''),
                hist_yesterday.get('sma_stoch', ''), hist_yesterday.get('bb_upper', ''),
                hist_yesterday.get('bb_middle', ''), hist_yesterday.get('bb_lower', ''),
                hist_yesterday.get('buy', ''), hist_yesterday.get('sell', ''),
                hist_yesterday.get('signal', '')
            ])

        if live_rows:
            last_row = 1 + len(live_rows)
            ws.range(f"{Config.LIVE_START}2:{LIVE_END}{last_row}").value = live_rows
            ws.range(f"{Config.HIST_START}2:{HIST_END}{last_row}").value = hist_rows

    except Exception as e:
        print(f"Excel update error: {e}")


# ============================================
# SETUP HEADERS - DYNAMIC
# ============================================

def setup_excel_headers():
    try:
        ws = excel_name.sheets['symbols']
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
        ws.range('T:T').column_width = 25
        ws.range('U:U').column_width = 20
        ws.range('W:W').column_width = 25
        ws.range('X:X').column_width = 20

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
            reconcile_order_statuses()
            check_order_signals()

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

def main():
    global historical_data_cache
    
    print("\n" + "="*80)
    print("🚀 DAILY TIMEFRAME - TRADINGVIEW MATCH")
    print("="*80)
    
    print("\n[1/5] Setting up Excel...")
    setup_excel_headers()
    
    print("\n[2/5] Logging to Shoonya...")
    if not Shoonya_login():
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
