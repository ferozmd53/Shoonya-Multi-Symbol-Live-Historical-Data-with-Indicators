# Shoonya Multi-Symbol Live / Historical Data with Indicators

## Single workbook
The project now uses **PythonTrader_Terminal_LMT.xlsm** as its only workbook.
The separate `symbols.xlsx` has been removed. Both scripts use the terminal
workbook and create missing `LOGIN` and `symbols` worksheets without deleting
other worksheets.

Put credentials in `LOGIN`: B2 CLIENT_ID/API key, B3 USER_ID, B4 PASSWORD,
B5 TOTP_SECRET, B6 API_SECRET, B7 AUTH_CODE. If credentials were stored in the
old `symbols.xlsx`, copy them into these cells in the terminal workbook before
running the scripts.

## Exchange instrument masters
The program downloads/caches Shoonya instrument masters for **NSE, BSE, NFO,
BFO, CDS and MCX** in `Instrument/`. Tokens are resolved by exact trading-symbol
match, with Shoonya `searchscrip` as fallback.

Enter symbols in column A of the `symbols` worksheet. Plain symbols default to
NSE equity; other segments use `EXCHANGE:TRADINGSYMBOL`:
- `RELIANCE-EQ` or `NSE:RELIANCE-EQ`
- `NFO:NIFTY26NOVFUT`
- `BFO:SENSEX...`
- `CDS:USDINR...`
- `MCX:GOLD...`
- `BSE:500325`

Use the exact trading symbol from the Shoonya master, particularly for futures
and options. Original NSE historical RSI, StochRSI, Bollinger Band and signal
calculations are preserved. Other exchanges are token-resolved and subscribed
for live quote updates.

## Run
Install `requirements.txt`; run `get_auth.py` when a new OAuth code is needed,
then run `Extreme_Reversal_Signal.py`.
