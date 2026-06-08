# Shoonya REAL TIME MULTI SYMBOLS  HISTORICAL DATA WITH 
# TICK WITH Bollinger Bands Trading System

## Installation  and CODE

```bash
pip uninstall Shoonya-Multi-Symbol-Live-Historical-Data-with-Indicators -y
pip install git+https://github.com/ferozmd53/Shoonya-Multi-Symbol-Live-Historical-Data-with-Indicators.git

# run.py - Minimal CODE
from get_auth import get_auth_code
from bb_trader import main

get_auth_code()
main()
