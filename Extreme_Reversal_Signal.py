# Extreme_Reversal_Signal.py - DAILY TIMEFRAME MATCH TRADINGVIEW

from NorenRestApiPy.NorenApi import NorenApi
import time
import datetime
from datetime import datetime, timedelta
import numpy as np
import json
import xlwings as xw
import pandas as pd
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
tick_count = 0
last_symbol_check = 0
last_excel_update = 0
today_price = {}  # Store today's price for daily close calculation
last_updated_date = {}

# ============================================
# CONFIGURATION
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
# FETCH DAILY HISTORICAL DATA
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
        
        # Calculate indicators using TradingView formulas
        df['RSI'] = tradingview_rsi(df['close'], Config.RSI_LENGTH)
        df['STOCH_RSI'] = tradingview_stochrsi(df['RSI'], Config.STOCH_LENGTH)
        df['SMA_STOCH'] = df['STOCH_RSI'].rolling(3).mean()
        
        bb_middle, bb_upper, bb_lower = tradingview_bb(df['close'], Config.BB_LENGTH, Config.BB_STD)
        df['BB_MIDDLE'] = bb_middle
        df['BB_UPPER'] = bb_upper
        df['BB_LOWER'] = bb_lower
        
        # Get yesterday's data (last row)
        yesterday = df.iloc[-1]
        
        # Previous day data for signals
        day_before = df.iloc[-2] if len(df) >= 2 else yesterday
        
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
        print(f"Error: {e}")
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
                print("❌ Missing credentials!")
                return 0
                
            userid = str(userid).strip()
            secret = str(secret).strip()
            auth = str(auth).strip()
            
        except Exception as e:
            print(f"❌ Error reading LOGIN sheet: {e}")
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
        print(f"Token error: {e}")
    return None

# ============================================
# WEBSOCKET CALLBACKS - DAILY TIMEFRAME
# ============================================

def on_ticks(tick):
    global live_data, tick_count, today_price, last_updated_date
    
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
            today = datetime.now().date()
            
            if d.get('first_tick', True):
                d['first_tick'] = False
                d['open'] = open_price if open_price > 0 else ltp
                d['high'] = ltp
                d['low'] = ltp
                print(f"\n✓ First tick for {symbol}: LTP={ltp}")
            
            # Update high/low for the day
            if ltp > d.get('high', 0):
                d['high'] = ltp
            if ltp < d.get('low', 999999):
                d['low'] = ltp
            
            d['ltp'] = ltp
            d['volume'] = volume
            d['timestamp'] = datetime.now()
            
            # ---------- DAILY TIMEFRAME LOGIC ----------
            # Only update daily close at the end of the day, 
            # OR use LTP as today's close for real-time daily chart
            
            # Store today's price for daily close
            today_price[symbol] = ltp
            
            # For real-time daily chart, use LTP as the closing price
            # This matches TradingView's daily chart during market hours
            d['close'] = ltp
            
            # Get daily indicators based on today's close
            hist = historical_data_cache.get(symbol)
            
            if hist:
                # Get yesterday's values
                yesterday = hist['yesterday']
                
                # Create a daily series with yesterday's close and today's LTP
                # This is how TradingView calculates daily indicators in real-time
                import pandas as pd
                
                # Use historical closes + today's LTP as the daily close
                all_closes = hist['all_closes'].copy()
                all_closes.append(ltp)
                
                # Calculate indicators on this series
                df = pd.DataFrame({'close': all_closes})
                
                # RSI
                delta = df['close'].diff()
                gain = delta.where(delta > 0, 0.0)
                loss = -delta.where(delta < 0, 0.0)
                alpha = 1.0 / Config.RSI_LENGTH
                avg_gain = gain.ewm(alpha=alpha, adjust=False, ignore_na=True).mean()
                avg_loss = loss.ewm(alpha=alpha, adjust=False, ignore_na=True).mean()
                rs = avg_gain / avg_loss
                rsi = 100 - (100 / (1 + rs))
                rsi = rsi.fillna(50)
                
                # StochRSI
                rsi_series = rsi
                rsi_low = rsi_series.rolling(Config.STOCH_LENGTH).min()
                rsi_high = rsi_series.rolling(Config.STOCH_LENGTH).max()
                stoch = 100 * (rsi_series - rsi_low) / (rsi_high - rsi_low)
                stoch = stoch.fillna(50)
                sma_stoch = stoch.rolling(3).mean()
                
                # Bollinger Bands
                sma = df['close'].rolling(Config.BB_LENGTH).mean()
                stdev = df['close'].rolling(Config.BB_LENGTH).std(ddof=0)
                bb_upper = sma + (Config.BB_STD * stdev)
                bb_lower = sma - (Config.BB_STD * stdev)
                
                # Get latest values
                latest_rsi = rsi.iloc[-1] if len(rsi) > 0 else 50
                latest_stoch = stoch.iloc[-1] if len(stoch) > 0 else 50
                latest_sma_stoch = sma_stoch.iloc[-1] if len(sma_stoch) > 0 else 50
                latest_bb_upper = bb_upper.iloc[-1] if len(bb_upper) > 0 else 0
                latest_bb_middle = sma.iloc[-1] if len(sma) > 0 else 0
                latest_bb_lower = bb_lower.iloc[-1] if len(bb_lower) > 0 else 0
                
                # Previous values for signals
                prev_bb_upper = bb_upper.iloc[-2] if len(bb_upper) >= 2 else latest_bb_upper
                prev_bb_lower = bb_lower.iloc[-2] if len(bb_lower) >= 2 else latest_bb_lower
                prev_close = all_closes[-2] if len(all_closes) >= 2 else ltp
                prev_sma_stoch = sma_stoch.iloc[-2] if len(sma_stoch) >= 2 else latest_sma_stoch
                
                # Update live data with daily indicator values
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
                
                # Generate signals
                buy_signal = False
                sell_signal = False
                signal = ""
                
                # BUY: Price crosses above lower band + StochRSI oversold
                if (prev_close < prev_bb_lower and 
                    ltp > latest_bb_lower and 
                    prev_sma_stoch < Config.STOCH_LOWER):
                    buy_signal = True
                    signal = "BUY"
                    print(f"🔵 DAILY BUY {symbol} | Price:{ltp:.2f} | BB Lower:{latest_bb_lower:.2f}")
                
                # SELL: Price crosses below upper band + StochRSI overbought
                elif (prev_close > prev_bb_upper and 
                      ltp < latest_bb_upper and 
                      prev_sma_stoch > Config.STOCH_UPPER):
                    sell_signal = True
                    signal = "SELL"
                    print(f"🔴 DAILY SELL {symbol} | Price:{ltp:.2f} | BB Upper:{latest_bb_upper:.2f}")
                
                d['buy'] = 1 if buy_signal else ''
                d['sell'] = 1 if sell_signal else ''
                d['signal'] = signal
                
                # Print debug every 100 ticks
                if tick_count % 100 == 0:
                    print(f"📊 {symbol}: RSI={latest_rsi:.1f}, Stoch={latest_stoch:.1f}, BB={latest_bb_lower:.1f}-{latest_bb_upper:.1f}")
                
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
    pass

def subscribe_symbols(tokens_list):
    if not tokens_list:
        return
    for i in range(0, len(tokens_list), 10):
        try:
            api.subscribe(tokens_list[i:i+10])
            print(f"✓ Subscribed to {len(tokens_list[i:i+10])} symbols")
        except Exception as e:
            print(f"Subscribe error: {e}")
        time.sleep(0.1)

# ============================================
# CHECK NEW SYMBOLS
# ============================================

def check_new_symbols():
    global last_symbol_check
    
    try:
        ws = excel_name.sheets['symbols']
        symbols_data = ws.range("A2:A100").value
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
            print(f"\n🆕 Found {len(new_symbols)} new symbols")
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
                            # Set initial indicator values from historical
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
# UPDATE EXCEL
# ============================================

def update_excel_bulk():
    global last_excel_update
    
    try:
        current_time = time.time()
        if current_time - last_excel_update < Config.EXCEL_UPDATE_INTERVAL:
            return
        last_excel_update = current_time
        
        ws = excel_name.sheets['symbols']
        symbols_data = ws.range("A2:A200").value
        if not symbols_data:
            return
        
        rows = []
        
        for symbol_cell in symbols_data:
            if not symbol_cell:
                rows.append([''] * 32)
                continue
            
            symbol = str(symbol_cell).strip().upper()
            if symbol.startswith('NSE:'):
                symbol = symbol[4:]
            if not symbol.endswith('-EQ'):
                symbol = f"{symbol}-EQ"
            
            tk = symbol_tokens.get(symbol)
            hist = historical_data_cache.get(symbol)
            hist_yesterday = hist['yesterday'] if hist else {}
            
            if tk and tk in live_data:
                d = live_data[tk]
                ltp = d.get('ltp', 0)
                
                rows.append([
                    symbol,
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
                    d['timestamp'].strftime('%H:%M:%S') if d.get('timestamp') else '',
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
            else:
                rows.append([''] * 32)
        
        try:
            if rows:
                ws.range(f"A2:AF{2 + len(rows) - 1}").value = rows
        except Exception:
            pass
            
    except Exception:
        pass

# ============================================
# SETUP HEADERS
# ============================================

def setup_excel_headers():
    try:
        ws = excel_name.sheets['symbols']
        ws.range("1:1").clear_contents()
        
        headers = [
            'Symbol', 'LTP', 'Open', 'High', 'Low', 'Close', 'Volume',
            'RSI', 'StochRSI', 'SMA Stoch', 'BB Upper', 'BB Middle', 'BB Lower',
            'BUY', 'SELL', 'Signal', 'Last Update',
            'Date', 'Open', 'High', 'Low', 'Close', 'Volume',
            'RSI', 'StochRSI', 'SMA Stoch', 'BB Upper', 'BB Middle', 'BB Lower',
            'BUY', 'SELL', 'Signal'
        ]
        
        for col_idx, header in enumerate(headers, start=1):
            cell = ws.range((1, col_idx))
            cell.value = header
            
            if header in ['BUY', 'SELL', 'Signal']:
                cell.color = (255, 100, 100)
            elif col_idx >= 18:
                cell.color = (146, 96, 54)
            else:
                cell.color = (54, 96, 146)
            cell.font.color = (255, 255, 255)
            cell.font.bold = True
        
        ws.range('A:AF').column_width = 12
        ws.range('A:A').column_width = 20

        return True
    except Exception as e:
        print(f"Error: {e}")
        return False

# ============================================
# READ SYMBOLS
# ============================================

def read_symbols_from_excel():
    try:
        ws = excel_name.sheets['symbols']
        symbols_data = ws.range("A2:A200").value
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
# MAIN EXCEL LOOP
# ============================================

def start_excel_loop():
    global last_symbol_check, tick_count
    
    print("✓ Starting REAL-TIME DAILY TIMEFRAME update loop...")
    print(f"📊 Indicators calculated using DAILY closes (matches TradingView)")
    print("   ⏰ Real-time updates during market hours\n")
    
    update_count = 0
    last_status = time.time()
    
    while True:
        try:
            current = time.time()
            
            if current - last_symbol_check >= 5:
                check_new_symbols()
                last_symbol_check = current
            
            update_excel_bulk()
            
            update_count += 1
            if update_count % 30 == 0:
                try:
                    excel_name.save()
                except:
                    pass
            
            if current - last_status >= 10:
                active = sum(1 for d in live_data.values() if d.get('ltp', 0) > 0)
                print(f"📈 Active: {active}/{len(symbol_tokens)} | Ticks: {tick_count}")
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
    print("🚀 DAILY TIMEFRAME - MATCH TRADINGVIEW")
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
        print(f"   [{i:2}/{len(symbols)}] {symbol}...", end=" ")
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
                # Set initial indicator values from historical
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
                print(f"✓ ({len(hist_data['all_closes'])} days)")
            else:
                print(f"⚠️ No data")
        else:
            print(f"✗ FAILED")
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
            print("✅ WebSocket connected!")
            if symbol_tokens:
                subscribe_symbols(list(symbol_tokens.values()))
            print("\n🚀 Running DAILY TIMEFRAME indicators...")
            print("   ✅ Indicators calculated using daily closes")
            print("   ✅ Matches TradingView daily chart")
            print("   ✅ Real-time updates during market hours\n")
            start_excel_loop()
        else:
            print("❌ WebSocket failed!")
            
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    main()
