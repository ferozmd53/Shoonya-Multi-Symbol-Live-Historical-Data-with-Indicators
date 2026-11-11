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
                """Exchange a fresh Shoonya OAuth code using the required SHA256 checksum."""
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
                    self._NorenApi__access_token = token
                    self._NorenApi__username     = uid
                    self._NorenApi__accountid    = actid
                except Exception:
                    pass
                self.access_token = token
                self.username     = uid
                self.accountid    = actid
                print(f"✅ injectOAuthHeader OK (uid={uid})")

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
    """Get order column headers"""
    return ['Quantity', 'BUY Trigger', 'BUY Status', 'SELL Trigger', 'SELL Status']

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
    HIST_START = 'W'  #   'W'       # Historical data starts here
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

# Get ALL columns in order: A, LIVE, HIST, ORDERS
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

def place_order(symbol, qty, buy_sell, price=0, product_type='C', excel_row=None):
    """Submit a validated limit order; broker updates determine its final status."""
    try:
        if symbol not in symbol_tokens:
            print(f"❌ Symbol {symbol} not found")
            return None

        side = str(buy_sell or "").strip().upper()
        if side not in ("BUY", "SELL"):
            print(f"❌ Invalid order side: {buy_sell!r}")
            return None

        try:
            qty = int(qty)
        except (TypeError, ValueError):
            print(f"❌ Invalid quantity for {symbol}: {qty!r}")
            return None
        if qty <= 0:
            print(f"❌ Quantity must be greater than zero for {symbol}")
            return None

        tk = symbol_tokens[symbol]
        exchange, tradingsymbol = parse_symbol_input(symbol)
        print(f"📊 Placing {side} order: {qty} shares of {symbol}")

        try:
            price = float(price or 0)
        except (TypeError, ValueError):
            print(f"❌ Invalid limit price for {symbol}: {price!r}")
            return None

        if price <= 0:
            current_price = safe_float(live_data.get(tk, {}).get('ltp', 0))
            if current_price <= 0:
                print(f"❌ No valid live LTP for {symbol}; order not sent")
                return None
            price = current_price + 5 if side == "BUY" else current_price - 5

        if price <= 0:
            print(f"❌ Calculated limit price is invalid for {symbol}: {price}")
            return None

        login_sheet = excel_name.sheets['LOGIN']
        algo_id = login_sheet.range('B2').value
        algo_id = str(algo_id).strip() if algo_id else ''
        if not algo_id:
            print("❌ algo_id not found in Excel LOGIN!B2")
            return None

        # Shoonya expects B or S, not the words BUY or SELL.
        order_params = {
            'buy_or_sell': 'B' if side == 'BUY' else 'S',
            'product_type': product_type,
            'exchange': exchange,
            'tradingsymbol': tradingsymbol,
            'quantity': qty,
            'discloseqty': 0,
            'price_type': 'LMT',
            'price': str(round(price, 2)),
            'trigger_price': None,
            'retention': 'DAY',
            'amo': None,
            'remarks': 'Python_Auto_Trade',
            'bookloss_price': 0.0,
            'bookprofit_price': 0.0,
            'trail_price': 0.0,
            'algo_id': algo_id
        }

        result = api.place_order(**order_params)
        print(f"📋 API Response: {result}")

        if result and result.get('norenordno'):
            order_no = str(result['norenordno'])
            order_history.append({
                'order_no': order_no,
                'symbol': symbol,
                'qty': qty,
                'type': side,
                'price': price,
                'status': 'SUBMITTED',
                'excel_row': excel_row,
                'time': datetime.now().strftime('%H:%M:%S')
            })
            print(f"📨 Order submitted: {side} {qty} {symbol} | Order No: {order_no}")
            return order_no

        print(f"❌ Broker did not return an order number; order not confirmed: {result}")
        return None

    except Exception as e:
        print(f"❌ Error placing order: {e}")
        return None

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
            if isinstance(item, dict):
                parsed_data.append(item)
            elif isinstance(item, str):
                try:
                    parsed_data.append(json.loads(item))
                except (TypeError, ValueError, json.JSONDecodeError):
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

def check_order_signals():
    try:
        ws = excel_name.sheets['symbols']
        symbols_data = ws.range(f"{Config.SYMBOL_COL}2:{Config.SYMBOL_COL}200").value
        if not symbols_data:
            return

        order_cols = ORDER_COLS
        if len(order_cols) < 5:
            return
        qty_col, buy_trigger_col, buy_status_col, sell_trigger_col, sell_status_col = order_cols

        for idx, symbol_cell in enumerate(symbols_data):
            if not symbol_cell:
                continue

            row_num = idx + 2
            try:
                symbol = normalized_symbol_key(symbol_cell)
            except ValueError as exc:
                print(f"Skipping symbol in row {row_num}: {exc}")
                continue
            if symbol not in symbol_tokens:
                continue

            qty_cell = ws.range(f"{qty_col}{row_num}").value
            try:
                qty_value = float(qty_cell)
                if not qty_value.is_integer() or qty_value <= 0:
                    raise ValueError("quantity must be a positive whole number")
                qty = int(qty_value)
            except (TypeError, ValueError):
                print(f"⚠️ Skipping {symbol}: enter a valid positive whole-number quantity in {qty_col}{row_num}")
                continue

            buy_signal = ws.range(f"{buy_trigger_col}{row_num}").value
            buy_status = ws.range(f"{buy_status_col}{row_num}").value
            if str(buy_signal or "").strip().upper() == "BUY" and not buy_status:
                print(f"\n🔔 BUY Signal detected for {symbol} (Qty: {qty})")
                order_no = place_order(symbol, qty, "BUY", price=0, product_type='C', excel_row=row_num)
                if order_no:
                    ws.range(f"{buy_status_col}{row_num}").value = f"SUBMITTED: {order_no}"
                    ws.range(f"{buy_trigger_col}{row_num}").value = "DONE"
                else:
                    ws.range(f"{buy_status_col}{row_num}").value = "FAILED - see console"
                    ws.range(f"{buy_trigger_col}{row_num}").value = "FAILED"

            sell_signal = ws.range(f"{sell_trigger_col}{row_num}").value
            sell_status = ws.range(f"{sell_status_col}{row_num}").value
            if str(sell_signal or "").strip().upper() == "SELL" and not sell_status:
                print(f"\n🔔 SELL Signal detected for {symbol} (Qty: {qty})")
                order_no = place_order(symbol, qty, "SELL", price=0, product_type='C', excel_row=row_num)
                if order_no:
                    ws.range(f"{sell_status_col}{row_num}").value = f"SUBMITTED: {order_no}"
                    ws.range(f"{sell_trigger_col}{row_num}").value = "DONE"
                else:
                    ws.range(f"{sell_status_col}{row_num}").value = "FAILED - see console"
                    ws.range(f"{sell_trigger_col}{row_num}").value = "FAILED"

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
        print(f"❌ Tick callback error: {e}")

def on_open(*args):
    global feed_opened
    feed_opened = True
    print("✅ WebSocket Connected")

def on_close(*args):
    global feed_opened
    feed_opened = False
    details = " | ".join(str(x) for x in args if x is not None)
    print("❌ WebSocket Closed" + (f" | {details}" if details else ""))

def on_order(order):
    """Apply broker order updates to the matching Excel order-status cell."""
    try:
        if isinstance(order, str):
            order = json.loads(order)
        if not isinstance(order, dict):
            print(f"⚠️ Ignoring unrecognized order update: {type(order).__name__}")
            return

        order_no = str(order.get('norenordno') or order.get('order_no') or '').strip()
        if not order_no:
            print(f"⚠️ Order update has no order number: {order}")
            return

        tracked = next((item for item in reversed(order_history)
                        if str(item.get('order_no')) == order_no), None)
        status = str(order.get('status') or order.get('stat') or 'UNKNOWN').strip().upper()
        reason = str(order.get('rejreason') or order.get('reason') or '').strip()

        if tracked:
            tracked['status'] = status
            row_num = tracked.get('excel_row')
            if row_num:
                status_col = ORDER_COLS[2] if tracked.get('type') == 'BUY' else ORDER_COLS[4]
                status_text = f"{status}: {order_no}"
                if reason:
                    status_text += f" | {reason}"
                try:
                    excel_name.sheets['symbols'].range(f"{status_col}{row_num}").value = status_text
                except Exception as excel_error:
                    print(f"⚠️ Could not write broker status to Excel: {excel_error}")

        print(f"📋 Broker order update: {order_no} | {status}" +
              (f" | {reason}" if reason else ""))
    except Exception as e:
        print(f"❌ Order-update handler error: {e}")

def subscribe_symbols(tokens_list):
    if not tokens_list:
        return
    for i in range(0, len(tokens_list), 10):
        try:
            api.subscribe(tokens_list[i:i+10])
        except Exception as e:
            print(f"❌ WebSocket subscribe failed for batch {i // 10 + 1}: {e}")
        time.sleep(0.1)

# ============================================
# UPDATE EXCEL - FIXED COLUMN POSITIONS
# ============================================

def update_excel_bulk():
    global last_excel_update
    
    try:
        current_time = time.time()
        if current_time - last_excel_update < Config.EXCEL_UPDATE_INTERVAL:
            return
        last_excel_update = current_time
        
        ws = excel_name.sheets['symbols']
        symbols_data = ws.range(f"{Config.SYMBOL_COL}2:{Config.SYMBOL_COL}200").value
        if not symbols_data:
            return
        
        # Get order columns dynamically
        order_cols = ORDER_COLS
        if len(order_cols) >= 5:
            qty_col = order_cols[0]
            buy_trigger_col = order_cols[1]
            buy_status_col = order_cols[2]
            sell_trigger_col = order_cols[3]
            sell_status_col = order_cols[4]
        else:
            return
        
        # Save existing order data
        existing_orders = {}
        for idx, symbol_cell in enumerate(symbols_data):
            if not symbol_cell:
                continue
            row_num = idx + 2
            try:
                symbol = normalized_symbol_key(symbol_cell)
            except ValueError:
                continue
            qty = ws.range(f"{qty_col}{row_num}").value
            buy_trigger = ws.range(f"{buy_trigger_col}{row_num}").value
            buy_status = ws.range(f"{buy_status_col}{row_num}").value
            sell_trigger = ws.range(f"{sell_trigger_col}{row_num}").value
            sell_status = ws.range(f"{sell_status_col}{row_num}").value
            
            existing_orders[symbol] = {
                'qty': qty if qty else '',
                'buy_trigger': buy_trigger if buy_trigger else '',
                'buy_status': buy_status if buy_status else '',
                'sell_trigger': sell_trigger if sell_trigger else '',
                'sell_status': sell_status if sell_status else ''
            }
        
        rows = []
        
        for symbol_cell in symbols_data:
            if not symbol_cell:
                rows.append([''] * len(ALL_COLS))
                continue
            
            try:
                symbol = normalized_symbol_key(symbol_cell)
            except ValueError:
                rows.append([''] * len(ALL_COLS))
                continue
            tk = symbol_tokens.get(symbol)
            hist = historical_data_cache.get(symbol)
            hist_yesterday = hist['yesterday'] if hist else {}
            order_data = existing_orders.get(symbol, {})
            
            if tk and tk in live_data:
                d = live_data[tk]
                ltp = d.get('ltp', 0)
                
                # Build row in correct order: A, LIVE, HIST, ORDERS
                row = []
                
                # Column A: Symbol
                row.append(symbol)
                
                # LIVE DATA (Columns B-Q)
                row.extend([
                    ltp if ltp > 0 else '',
                    d.get('open', 0) if d.get('open', 0) > 0 else '',
                    d.get('high', 0) if d.get('high', 0) > 0 else '',
                    d.get('low', 0) if d.get('low', 0) > 0 else '',
                    d.get('close', 0) if d.get('close', 0) > 0 else '',
                    d.get('volume', 0) if d.get('volume', 0) > 0 else '',
                    d.get('rsi', ''),
                    d.get('stoch_rsi', ''),
                    d.get('sma_stoch', ''),
                    d.get('bb_upper', ''),
                    d.get('bb_middle', ''),
                    d.get('bb_lower', ''),
                    d.get('buy', ''),
                    d.get('sell', ''),
                    d.get('signal', ''),
                    d['timestamp'].strftime('%H:%M:%S') if d.get('timestamp') else ''
                ])
                
                # HISTORICAL DATA (Columns W-AK)
                row.extend([
                    hist_yesterday.get('date', ''),
                    hist_yesterday.get('open', ''),
                    hist_yesterday.get('high', ''),
                    hist_yesterday.get('low', ''),
                    hist_yesterday.get('close', ''),
                    hist_yesterday.get('volume', ''),
                    hist_yesterday.get('rsi', ''),
                    hist_yesterday.get('stoch_rsi', ''),
                    hist_yesterday.get('sma_stoch', ''),
                    hist_yesterday.get('bb_upper', ''),
                    hist_yesterday.get('bb_middle', ''),
                    hist_yesterday.get('bb_lower', ''),
                    hist_yesterday.get('buy', ''),
                    hist_yesterday.get('sell', ''),
                    hist_yesterday.get('signal', '')
                ])
                
                # ORDERS (Columns R-V) - PLACE ORDER COLUMNS BEFORE HISTORICAL
                # Wait! The order should be: Symbol, LIVE, ORDERS, HISTORICAL
                # But your config says: LIVE(B-Q), ORDERS(R-V), HISTORICAL(W-AK)
                # So the correct order is: Symbol, LIVE, ORDERS, HISTORICAL
                
                # Let me fix this - the row should be built in this exact order:
                # 1. Symbol (A)
                # 2. LIVE (B-Q) 
                # 3. ORDERS (R-V)
                # 4. HISTORICAL (W-AK)
                
                # I need to rebuild the row properly
                row_correct = []
                
                # 1. Symbol
                row_correct.append(symbol)
                
                # 2. LIVE DATA (B-Q)
                row_correct.extend([
                    ltp if ltp > 0 else '',
                    d.get('open', 0) if d.get('open', 0) > 0 else '',
                    d.get('high', 0) if d.get('high', 0) > 0 else '',
                    d.get('low', 0) if d.get('low', 0) > 0 else '',
                    d.get('close', 0) if d.get('close', 0) > 0 else '',
                    d.get('volume', 0) if d.get('volume', 0) > 0 else '',
                    d.get('rsi', ''),
                    d.get('stoch_rsi', ''),
                    d.get('sma_stoch', ''),
                    d.get('bb_upper', ''),
                    d.get('bb_middle', ''),
                    d.get('bb_lower', ''),
                    d.get('buy', ''),
                    d.get('sell', ''),
                    d.get('signal', ''),
                    d['timestamp'].strftime('%H:%M:%S') if d.get('timestamp') else ''
                ])
                
                # 3. ORDERS (R-V)
                row_correct.extend([
                    order_data.get('qty', ''),
                    order_data.get('buy_trigger', ''),
                    order_data.get('buy_status', ''),
                    order_data.get('sell_trigger', ''),
                    order_data.get('sell_status', '')
                ])
                
                # 4. HISTORICAL DATA (W-AK)
                row_correct.extend([
                    hist_yesterday.get('date', ''),
                    hist_yesterday.get('open', ''),
                    hist_yesterday.get('high', ''),
                    hist_yesterday.get('low', ''),
                    hist_yesterday.get('close', ''),
                    hist_yesterday.get('volume', ''),
                    hist_yesterday.get('rsi', ''),
                    hist_yesterday.get('stoch_rsi', ''),
                    hist_yesterday.get('sma_stoch', ''),
                    hist_yesterday.get('bb_upper', ''),
                    hist_yesterday.get('bb_middle', ''),
                    hist_yesterday.get('bb_lower', ''),
                    hist_yesterday.get('buy', ''),
                    hist_yesterday.get('sell', ''),
                    hist_yesterday.get('signal', '')
                ])
                
                rows.append(row_correct)
            else:
                # Preserve order inputs for symbols that cannot be resolved to a token.
                missing_row = [''] * len(ALL_COLS)
                missing_row[17:22] = [
                    order_data.get('qty', ''),
                    order_data.get('buy_trigger', ''),
                    order_data.get('buy_status', ''),
                    order_data.get('sell_trigger', ''),
                    order_data.get('sell_status', '')
                ]
                rows.append(missing_row)
        
        if rows:
            data_rows = []
            for row in rows:
                data_rows.append(row[1:])  # Skip column A
            
            if data_rows:
                start_col = 'B'
                end_col = ALL_COLS[-1]
                ws.range(f"{start_col}2:{end_col}{2 + len(data_rows) - 1}").value = data_rows
            
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

    # Populate historical columns immediately, without waiting for WebSocket.
    print("\n📥 Writing initial historical data to Excel...")
    try:
        update_excel_bulk()
        excel_name.save()
        print("✅ Initial Excel data write completed and workbook saved")
    except Exception as excel_error:
        print(f"❌ Initial Excel write failed: {excel_error}")
    
    if len(symbol_tokens) == 0:
        print("❌ No symbols!")
        return
    
    print("\nStarting WebSocket...")
    
    try:
        api.start_websocket(
            subscribe_callback=on_ticks,
            order_update_callback=on_order,
            socket_open_callback=on_open,
            socket_close_callback=on_close
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
