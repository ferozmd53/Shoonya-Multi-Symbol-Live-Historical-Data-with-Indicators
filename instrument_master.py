"""Shoonya official instrument-master loader for NSE, BSE, NFO, BFO, CDS and MCX."""
from io import BytesIO
from pathlib import Path
import pandas as pd
import requests

SUPPORTED_EXCHANGES = ("NSE", "BSE", "NFO", "BFO", "CDS", "MCX")
INDEX_TOKENS = {"NSE": {"NIFTY":"26000","BANKNIFTY":"26009","FINNIFTY":"26037","MIDCPNIFTY":"26074","INDIAVIX":"26017"},
                "BSE": {"SENSEX":"1","SENSEX50":"47","BANKEX":"12"}}

class InstrumentMaster:
    def __init__(self, cache_dir=None, timeout=30):
        self.cache_dir = Path(cache_dir) if cache_dir else Path(__file__).resolve().with_name("Instrument")
        self.timeout = timeout
        self.frames = {}

    @staticmethod
    def _column(frame, *names):
        cols = {str(c).strip().lower(): c for c in frame.columns}
        return next((cols[n.lower()] for n in names if n.lower() in cols), None)

    def load(self, exchanges=SUPPORTED_EXCHANGES, force_refresh=False):
        """Load each exchange independently; use a local CSV cache if download fails."""
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        loaded = []
        for exchange in exchanges:
            exchange = str(exchange).upper().strip()
            if exchange not in SUPPORTED_EXCHANGES:
                continue
            cache = self.cache_dir / f"{exchange}_symbols.csv"
            frame = None
            if not force_refresh and cache.exists():
                try:
                    frame = pd.read_csv(cache, low_memory=False)
                except Exception as exc:
                    print(f"{exchange} cache read failed: {exc}")
            if force_refresh or frame is None or frame.empty:
                try:
                    response = requests.get(f"https://api.shoonya.com/{exchange}_symbols.txt.zip", timeout=self.timeout)
                    response.raise_for_status()
                    frame = pd.read_csv(BytesIO(response.content), compression="zip", low_memory=False)
                    if frame.empty:
                        raise ValueError("empty instrument master")
                    frame.to_csv(cache, index=False)
                    print(f"{exchange}: downloaded {len(frame):,} instruments")
                except Exception as exc:
                    print(f"{exchange} download failed: {exc}")
                    if cache.exists():
                        try:
                            frame = pd.read_csv(cache, low_memory=False)
                        except Exception:
                            frame = None
                    if frame is None or frame.empty:
                        continue
            self.frames[exchange] = frame
            loaded.append(exchange)
            print(f"{exchange}: {len(frame):,} instruments ready")
        return loaded

    def get_instrument(self, exchange, trading_symbol):
        """Return an exact trading-symbol row from the loaded master."""
        exchange = str(exchange).upper().strip()
        symbol = str(trading_symbol).upper().strip()
        if exchange not in SUPPORTED_EXCHANGES or not symbol:
            return None
        index_token = INDEX_TOKENS.get(exchange, {}).get(symbol)
        if index_token:
            return {"Exchange": exchange, "Token": index_token, "TradingSymbol": symbol}
        frame = self.frames.get(exchange)
        if frame is None or frame.empty:
            return None
        col = self._column(frame, "TradingSymbol", "tsym", "tradingsymbol")
        if col is None:
            return None
        rows = frame[frame[col].astype(str).str.upper() == symbol]
        return None if rows.empty else rows.iloc[0].to_dict()

    def get_token(self, exchange, trading_symbol, api=None):
        """Resolve exact master token; use Shoonya searchscrip as a fallback."""
        row = self.get_instrument(exchange, trading_symbol)
        if row:
            token_col = self._column(pd.DataFrame([row]), "Token", "token")
            if token_col and row.get(token_col) is not None:
                return str(row[token_col]).strip()
        if api is not None:
            try:
                result = api.searchscrip(exchange=str(exchange).upper().strip(), searchtext=str(trading_symbol).upper().strip())
                wanted = str(trading_symbol).upper().strip()
                for item in (result or {}).get("values") or []:
                    if str(item.get("tsym", "")).upper().strip() == wanted and item.get("token"):
                        return str(item["token"])
            except Exception as exc:
                print(f"Token lookup failed for {exchange}:{trading_symbol}: {exc}")
        return None
