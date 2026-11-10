# Shoonya Multi-Symbol Live & Historical Trading System

Educational and research software for Shoonya API users. Trading involves
financial risk; verify every order and test in a safe environment before
using live funds.

## Installation

### Option A — Python 3.13 or older

```bash
pip install --upgrade --force-reinstall --no-cache-dir --no-deps git+https://github.com/ferozmd53/Shoonya-Multi-Symbol-Live-Historical-Data-with-Indicators.git
pip install git+https://github.com/ferozmd53/Shoonya-Multi-Symbol-Live-Historical-Data-with-Indicators.git
```

### Option B — Python 3.14 / 3.15 (recommended)

`NorenRestApiOAuth` depends on `PyYAML`, which has **no wheel for Python
3.15**. Installing it triggers a source build that requires the Microsoft
C++ Build Tools. **You do not need it** — `Extreme_Reversal_Signal.py`
installs a runtime shim that adds `getAccessToken()` to `NorenRestApiPy`
automatically.

```bash
pip install --no-cache-dir --no-deps NorenRestApiPy xlwings numpy pandas openpyxl requests pyotp websocket-client
pip install --no-cache-dir --no-deps --no-build-isolation git+https://github.com/ferozmd53/Shoonya-Multi-Symbol-Live-Historical-Data-with-Indicators.git
```

## Workbook

Place `PythonTrader_Terminal_LMT.xlsm` on your **Desktop** (or beside
`run.py`). Enter your Shoonya credentials in the `LOGIN` sheet:

| Cell | Field |
|------|-------|
| B2 | CLIENT_ID |
| B3 | USER_ID |
| B4 | PASSWORD |
| B5 | TOTP_SECRET |
| B6 | API_SECRET |
| B7 | AUTH_CODE (auto-filled) |
| B11 | IP_ADDRESS (auto-filled) |

Never commit the workbook, passwords, TOTP secret, API secret, auth code,
access token, refresh token, or browser cookies to GitHub.

## Run

**`run.py` is not part of this repo — create it locally on your Desktop.**

Save this as `run.py` next to your `PythonTrader_Terminal_LMT.xlsm`:

```python
from get_auth import get_auth_code
from Extreme_Reversal_Signal import main

if __name__ == "__main__":
    get_auth_code()
    main()
```

Then run:

```bash
python run.py
```

Authentication opens Chrome directly through its DevTools interface and
does **not** require ChromeDriver. Fields are filled by their known IDs
(`lgnusrid`, `lgnpwd`, `lgnotp`) with a 3-second pause between each one.
