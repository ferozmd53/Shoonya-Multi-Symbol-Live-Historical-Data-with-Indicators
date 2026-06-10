
# Shoonya REAL TIME TICK with MULTI SYMBOLS  HISTORICAL DATA 

# ====================================================================
# DISCLAIMER
# ====================================================================
# This software is for educational and research purposes only.
## Installation  and CODE

```bash  C M D
pip uninstall Shoonya-Multi-Symbol-Live-Historical-Data-with-Indicators -y
pip install git+https://github.com/ferozmd53/Shoonya-Multi-Symbol-Live-Historical-Data-with-Indicators.git

# run.py - Minimal CODE
#excel file download
from get_auth import get_auth_code
from Extreme_Reversal_Signal import main

get_auth_code()
main()


