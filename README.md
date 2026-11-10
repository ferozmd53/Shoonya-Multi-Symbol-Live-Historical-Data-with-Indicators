# Shoonya Multi-Symbol Live & Historical Trading System

Educational and research software for Shoonya API users. Trading involves financial risk; verify every order and test in a safe environment before using live funds.

## Installation

Use the OAuth SDK build (do not install the legacy `NorenRestApiPy` package separately):

```bash
python -m pip uninstall NorenRestApiPy -y
python -m pip install --upgrade git+https://github.com/ferozmd53/Shoonya-Multi-Symbol-Live-Historical-Data-with-Indicators.git
```

The package installs `NorenRestApiOAuth`, Excel/Python dependencies, and `websocket-client`.

## Workbook

Keep `PythonTrader_Terminal_LMT.xlsm` on your Desktop or beside `run.py`. Enter your own Shoonya credentials locally in the workbook. **Never commit the workbook, passwords, TOTP secret, API secret, auth code, access token, refresh token, or browser cookies to GitHub.**

## Run

Create or use your local `run.py`:

```python
from get_auth import get_auth_code
from Extreme_Reversal_Signal import main

if __name__ == "__main__":
    get_auth_code()
    main()
```

Authentication opens Chrome directly through its DevTools interface and does not require ChromeDriver.
