"""Weekly OHLCV output sheet for the Shoonya Excel terminal.

Uses timestamped Shoonya daily candles and aggregates Monday-Sunday trading weeks.
The latest available broker daily candle determines the active week. Excel output
is written to WEEKLY_DATA with one row per symbol.
"""
import json
import time
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor

import pandas as pd


class WeeklyDataEngine:
    HEADERS = [
        "Symbol", "Exchange", "Week Start", "Week End",
        "Open", "High", "Low", "Close", "Volume", "Last Update"
    ]

    def __init__(self, history_days=90, refresh_seconds=60):
        self.history_days = int(history_days)
        self.refresh_seconds = int(refresh_seconds)
        self.history = {}
        self.pending = {}
        self.retry_at = {}
        self.executor = ThreadPoolExecutor(
            max_workers=3, thread_name_prefix="ShoonyaWeeklyHistory"
        )
        self.sheet_ready = False
        self.last_sheet_write = 0.0

    @staticmethod
    def _parse_history(raw):
        rows = []
        for item in raw or []:
            if isinstance(item, str):
                try:
                    item = json.loads(item)
                except Exception:
                    continue
            if isinstance(item, dict):
                rows.append(item)
        if not rows:
            return None

        frame = pd.DataFrame(rows)
        frame.rename(columns={
            "time": "datetime", "into": "open", "inth": "high",
            "intl": "low", "intc": "close", "intv": "volume",
            "ssboe": "epoch"
        }, inplace=True)

        if "datetime" not in frame and "epoch" in frame:
            epoch = pd.to_numeric(frame["epoch"], errors="coerce")
            frame["datetime"] = (
                pd.to_datetime(epoch, unit="s", utc=True, errors="coerce")
                .dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
            )
        elif "datetime" in frame:
            parsed = pd.to_datetime(frame["datetime"], dayfirst=True, errors="coerce")
            if "epoch" in frame:
                epoch = pd.to_numeric(frame["epoch"], errors="coerce")
                epoch_ist = (
                    pd.to_datetime(epoch, unit="s", utc=True, errors="coerce")
                    .dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
                )
                parsed = parsed.fillna(epoch_ist)
            frame["datetime"] = parsed

        required = ("datetime", "open", "high", "low", "close")
        if any(col not in frame.columns for col in required):
            return None
        if "volume" not in frame:
            frame["volume"] = 0

        for col in ("open", "high", "low", "close", "volume"):
            frame[col] = pd.to_numeric(frame[col], errors="coerce")
        frame = frame.dropna(subset=["datetime", "open", "high", "low", "close"])
        frame = frame.sort_values("datetime")
        frame = frame.drop_duplicates("datetime", keep="last").reset_index(drop=True)
        return frame if not frame.empty else None

    def _fetch_history(self, api, exchange, tradingsymbol, key):
        end_date = datetime.now().replace(microsecond=0)
        start_date = end_date - timedelta(days=self.history_days)
        try:
            raw = api.get_daily_price_series(
                exchange=exchange,
                tradingsymbol=tradingsymbol,
                startdate=str(int(start_date.timestamp())),
                enddate=str(int(end_date.timestamp()))
            )
            frame = self._parse_history(raw)
            if frame is None or frame.empty:
                raise RuntimeError("Shoonya returned no usable daily candles")
            self.history[key] = frame
            self.retry_at[key] = time.time() + self.refresh_seconds
            print(
                f"Weekly OHLCV refreshed for {key}: {len(frame)} daily candles "
                f"({frame['datetime'].iloc[0].date()} to {frame['datetime'].iloc[-1].date()})"
            )
            return frame
        except Exception as exc:
            self.retry_at[key] = time.time() + min(self.refresh_seconds, 300)
            print(f"Weekly OHLCV history failed for {key}: {exc}")
            return None

    def _get_history(self, api, exchange, tradingsymbol, key):
        future = self.pending.get(key)
        if future is not None and future.done():
            self.pending.pop(key, None)
            try:
                future.result()
            except Exception as exc:
                print(f"Weekly OHLCV worker error for {key}: {exc}")

        if key not in self.pending and time.time() >= self.retry_at.get(key, 0):
            self.pending[key] = self.executor.submit(
                self._fetch_history, api, exchange, tradingsymbol, key
            )
        return self.history.get(key)

    @staticmethod
    def _weekly_row(symbol, exchange, frame):
        if frame is None or frame.empty:
            return [symbol, exchange] + [None] * 8

        daily = frame.copy()
        daily["datetime"] = pd.to_datetime(daily["datetime"], errors="coerce")
        daily = daily.dropna(subset=["datetime"]).sort_values("datetime")
        daily = daily.drop_duplicates("datetime", keep="last")
        if daily.empty:
            return [symbol, exchange] + [None] * 8

        # W-SUN means Monday through Sunday; the label is the Monday date.
        daily["week"] = daily["datetime"].dt.to_period("W-SUN")
        grouped = daily.groupby("week", sort=True)
        weekly = pd.DataFrame({
            "open": grouped["open"].first(),
            "high": grouped["high"].max(),
            "low": grouped["low"].min(),
            "close": grouped["close"].last(),
            "volume": grouped["volume"].sum(),
        })
        latest_period = daily["week"].iloc[-1]
        candle = weekly.loc[latest_period]
        week_start = latest_period.start_time
        week_end = week_start + timedelta(days=4)
        last_update = daily["datetime"].iloc[-1]

        return [
            symbol, exchange,
            week_start.strftime("%d/%m/%Y"),
            week_end.strftime("%d/%m/%Y"),
            round(float(candle["open"]), 4),
            round(float(candle["high"]), 4),
            round(float(candle["low"]), 4),
            round(float(candle["close"]), 4),
            int(candle["volume"]) if pd.notna(candle["volume"]) else 0,
            last_update.strftime("%d/%m/%Y %H:%M"),
        ]

    def refresh_from_excel(self, workbook, api, symbols):
        """Schedule history refreshes without blocking live price/order processing."""
        if api is None:
            return
        try:
            try:
                sheet = workbook.sheets["WEEKLY_DATA"]
            except Exception:
                sheet = workbook.sheets.add("WEEKLY_DATA")
                self.sheet_ready = False

            if not self.sheet_ready:
                sheet.range("A1:J1").value = [self.HEADERS]
                sheet.range("A1:J1").api.Font.Bold = True
                self.sheet_ready = True

            rows = []
            for raw_symbol in symbols or []:
                symbol = str(raw_symbol or "").strip().upper()
                if not symbol:
                    continue
                if ":" in symbol:
                    exchange, tradingsymbol = symbol.split(":", 1)
                elif "|" in symbol:
                    exchange, tradingsymbol = symbol.split("|", 1)
                else:
                    exchange, tradingsymbol = "NSE", symbol
                exchange = exchange.strip().upper()
                tradingsymbol = tradingsymbol.strip().upper()
                key = f"{exchange}:{tradingsymbol}"
                frame = self._get_history(api, exchange, tradingsymbol, key)
                rows.append(self._weekly_row(symbol, exchange, frame))

            if rows:
                sheet.range(f"A2:J{len(rows) + 1}").value = rows
            old_last = getattr(self, "_last_rows_written", 0)
            if old_last > len(rows):
                sheet.range(f"A{len(rows) + 2}:J{old_last + 1}").clear_contents()
            self._last_rows_written = len(rows)
        except Exception as exc:
            print(f"Weekly Excel output update failed: {exc}")
