# Shoonya StochRSI Trading System
# DISCLAIMER
# This software is for educational and research purposes only.
## Installation
```bash
pip uninstall Shoonya-Multi-Symbol-Live-Historical-Data-with-Indicators -y
pip install git+https://github.com/ferozmd53/Shoonya-Multi-Symbol-Live-Historical-Data-with-Indicators.git
pip install NorenRestApiOAuth
pip install NorenRestApiPy 

# run.py - Minimal code
from get_auth import get_auth_code
from Extreme_Reversal_Signal import main

if __name__ == "__main__":
    get_auth_code()
    main()

## Indicator settings from Excel (no code edit needed)

Sheet **LOGIN**, columns **M / N / O** (created automatically, your values are never overwritten):
`DAYS_TO_DOWNLOAD`, `USE_SQLITE_CACHE`, `PINK_FIRST_BAR_DATE`, `NWE_*`, `MIRROR_*`, `STEP_*`, `ATR_*`, `BB_*`, `AS_OF_DATE`, plus a `STATUS` row.
Change a value and it is applied within ~2 seconds (no restart); invalid values fall back to the default and are reported in `STATUS`.

* **AS_OF_DATE** (e.g. `2026-11-10`): the HISTORICAL block shows that day's candle and the indicator values exactly as they were that day. Blank = latest. The live block is not affected.
* **INDICATOR_HISTORY** sheet: type a symbol in `B1` and the number of days in `B2` - a table of the indicator values for each of those days (newest first) is filled in.
