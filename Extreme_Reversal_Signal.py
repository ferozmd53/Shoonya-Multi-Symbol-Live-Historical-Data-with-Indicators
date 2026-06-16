# Extreme_Reversal_Signal.py - FIXED DATA POSITIONS

from NorenRestApiPy.NorenApi import NorenApi
import time
import datetime
from datetime import datetime, timedelta
import numpy as np
import json
import xlwings as xw
import pandas as pd
import string
import warnings
warnings.filterwarnings('ignore')

# ============================================
# GLOBAL VARIABLES
# ============================================

excel_name = xw.Book('symbols.xlsx')
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
ALL_COLS = [Config.SYMBOL_COL] + LIVE_COLS + HIST_COLS + ORDER_COLS

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
    try:
        if symbol not in symbol_tokens:
            print(f"❌ Symbol {symbol} not found")
            return None
            
        tk = symbol_tokens[symbol]
        exchange = tk.split('|')[0]
        
        tradingsymbol = symbol
        
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
        algo_id = login_sheet.range('B2').value
        algo_id = str(algo_id).strip() if algo_id else ''
        
        if not algo_id:
            print("❌ algo_id not found in Excel B2 (CLIENT_ID)")
            return None
        
        order_params = {
            'buy_or_sell': buy_or_sell,
            'product_type': product_type,
            'exchange': exchange,
            'tradingsymbol': tradingsymbol,
            'quantity': qty,
            'discloseqty': 0,
            'price_type': 'LMT',
            'price': str(price),
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
        
        if result and result.get('norenordno'):
            order_info = {
                'order_no': result['norenordno'],
                'symbol': symbol,
                'qty': qty,
                'type': buy_sell,
                'price': price,
                'status': result.get('status', 'PENDING'),
                'time': datetime.now().strftime('%H:%M:%S')
            }
            order_history.append(order_info)
            print(f"✅ Order placed: {buy_sell} {qty} of {symbol}")
            print(f"   Order No: {result['norenordno']}")
            return result['norenordno']
        else:
            print(f"❌ Order failed: {result}")
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

def GetToken(exchange, tradingsymbol):
    try:
        search = tradingsymbol.replace('-EQ', '').strip()
        result = api.searchscrip(exchange=exchange, searchtext=search)
        if result and result.get('values'):
            for item in result['values']:
                tsym = item.get('tsym', '').upper()
                if tsym in [tradingsymbol.upper(), search.upper()]:
                    return item.get('token')
            return result['values'][0].get('token')
    except Exception as e:
        return None

# ============================================
# CHECK ORDER SIGNALS FROM EXCEL
# ============================================

def check_order_signals():
    try:
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
        
        for idx, symbol_cell in enumerate(symbols_data):
            if not symbol_cell:
                continue
            
            row_num = idx + 2
            symbol = str(symbol_cell).strip().upper()
            if symbol.startswith('NSE:'):
                symbol = symbol[4:]
            if not symbol.endswith('-EQ'):
                symbol = f"{symbol}-EQ"
            
            if symbol not in symbol_tokens:
                continue
            
            qty_cell = ws.range(f"{qty_col}{row_num}").value
            qty = int(float(qty_cell)) if qty_cell and str(qty_cell).replace('.', '').isdigit() else 1
            
            # Check BUY
            buy_signal = ws.range(f"{buy_trigger_col}{row_num}").value
            buy_status = ws.range(f"{buy_status_col}{row_num}").value
            
            if buy_signal:
                buy_signal = str(buy_signal).strip().upper()
            
            if buy_signal == "BUY" and not buy_status:
                print(f"\n🔔 BUY Signal detected for {symbol} (Qty: {qty})")
                order_no = place_order(symbol, qty, "BUY", price=0, product_type='C')
                if order_no:
                    ws.range(f"{buy_status_col}{row_num}").value = f"Bought: {order_no}"
                    ws.range(f"{buy_trigger_col}{row_num}").value = "DONE"
                else:
                    ws.range(f"{buy_status_col}{row_num}").value = "Failed"
                    ws.range(f"{buy_trigger_col}{row_num}").value = "FAILED"
            
            # Check SELL
            sell_signal = ws.range(f"{sell_trigger_col}{row_num}").value
            sell_status = ws.range(f"{sell_status_col}{row_num}").value
            
            if sell_signal:
                sell_signal = str(sell_signal).strip().upper()
            
            if sell_signal == "SELL" and not sell_status:
                print(f"\n🔔 SELL Signal detected for {symbol} (Qty: {qty})")
                order_no = place_order(symbol, qty, "SELL", price=0, product_type='C')
                if order_no:
                    ws.range(f"{sell_status_col}{row_num}").value = f"Sold: {order_no}"
                    ws.range(f"{sell_trigger_col}{row_num}").value = "DONE"
                else:
                    ws.range(f"{sell_status_col}{row_num}").value = "Failed"
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
        pass

def on_open():
    global feed_opened
    feed_opened = True
    print("✅ WebSocket Connected")

def on_close():
    global feed_opened
    feed_opened = False
    print("❌ WebSocket Closed")

def on_order(order):
    print(f"📋 Order Update: {order}")

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
            symbol = str(symbol_cell).strip().upper()
            if symbol.startswith('NSE:'):
                symbol = symbol[4:]
            if not symbol.endswith('-EQ'):
                symbol = f"{symbol}-EQ"
            
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
            
            symbol = str(symbol_cell).strip().upper()
            if symbol.startswith('NSE:'):
                symbol = symbol[4:]
            if not symbol.endswith('-EQ'):
                symbol = f"{symbol}-EQ"
            
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
                rows.append([''] * len(ALL_COLS))
        
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
        
        cleaned = []
        seen = set()
        for s in symbols:
            s_str = s.upper()
            if s_str.startswith('NSE:'):
                s_str = s_str[4:]
            if not s_str.endswith('-EQ'):
                s_str = f"{s_str}-EQ"
            if s_str not in seen:
                seen.add(s_str)
                cleaned.append(s_str)
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
        for s in current:
            s_str = s.upper()
            if s_str.startswith('NSE:'):
                s_str = s_str[4:]
            if not s_str.endswith('-EQ'):
                s_str = f"{s_str}-EQ"
            cleaned.append(s_str)
        
        new_symbols = [s for s in cleaned if s not in symbol_tokens]
        
        if new_symbols:
            print(f"🆕 Found {len(new_symbols)} new symbols")
            new_tokens = []
            for symbol in new_symbols:
                try:
                    token = GetToken("NSE", symbol)
                    if token:
                        tk = f"NSE|{token}"
                        symbol_tokens[symbol] = tk
                        token_symbols[token] = symbol
                        
                        hist_data = fetch_historical_data(symbol)
                        
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
    
    print("\n[1/4] Setting up Excel...")
    setup_excel_headers()
    
    print("\n[2/4] Logging to Shoonya...")
    if not Shoonya_login():
        print("❌ Login failed!")
        return
    
    print("\n[3/4] Reading symbols...")
    symbols = read_symbols_from_excel()
    
    if not symbols:
        default = ["RELIANCE-EQ", "TCS-EQ", "INFY-EQ"]
        for i, sym in enumerate(default, start=2):
            excel_name.sheets['symbols'].range(f"A{i}").value = sym
        excel_name.save()
        symbols = default
        print(f"✅ Added default symbols")
    
    print(f"📋 Processing {len(symbols)} symbols...")
    
    print(f"\n[4/4] Loading historical data...")
    for i, symbol in enumerate(symbols, 1):
        token = GetToken("NSE", symbol)
        if token:
            tk = f"NSE|{token}"
            symbol_tokens[symbol] = tk
            token_symbols[token] = symbol
            
            hist_data = fetch_historical_data(symbol)
            
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
                print(f"   [{i:2}/{len(symbols)}] ✗ {symbol}")
        else:
            print(f"   [{i:2}/{len(symbols)}] ✗ {symbol}")
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
