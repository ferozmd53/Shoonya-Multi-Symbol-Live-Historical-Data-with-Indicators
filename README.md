# Shoonya Multi-Symbol Live / Historical Data with Indicators

Python application for Shoonya market data, multi-symbol Excel monitoring, StochRSI and Bollinger Band indicators, and optional Excel-triggered orders.

> **Risk notice:** This software can submit live orders to a brokerage account. Review the code, verify the Excel workbook and quantity cells, and test in a non-live environment before enabling live order triggers. You are responsible for all orders submitted from your account.

## Requirements

- Windows
- Python 3.8 or newer
- Microsoft Excel desktop (required by xlwings)
- A valid Shoonya account and API access

## Easy install (Windows)

**One command installs the application and its Python dependencies:**

```powershell
py -m pip install --upgrade "git+https://github.com/ferozmd53/Shoonya-Multi-Symbol-Live-Historical-Data-with-Indicators.git"
```

After installation, start it from Command Prompt or PowerShell with:

```powershell
shoonya-trader
```

If Windows says `shoonya-trader` is not recognized, use the Python module launcher instead:

```powershell
py -m run
```

To update to the latest version later:

```powershell
py -m pip install --upgrade --force-reinstall "git+https://github.com/ferozmd53/Shoonya-Multi-Symbol-Live-Historical-Data-with-Indicators.git"
```

### Requirements before first run

- Windows 10/11, Python 3.8 or newer, and Git installed and available in PATH (pip uses Git to install directly from GitHub).
- Microsoft Excel desktop installed; this application uses `xlwings`.
- Google Chrome installed for the Shoonya browser login flow.
- Your own private `PythonTrader_Terminal_LMT.xlsm` workbook. The installer does not download or create a workbook containing personal login details.

If Git is not installed, install Git for Windows first, restart the terminal, and retry the install command.

### Manual developer install

If you prefer to work from a cloned source folder:

```powershell
py -m pip install -r requirements.txt
py -m pip install -e .
```

## Local workbook

Keep your own `PythonTrader_Terminal_LMT.xlsm` beside `run.py` (or in one of the folders searched by the application). The workbook contains your local symbol list and login configuration.

**Do not upload your personal trading workbook, passwords, TOTP secret, API secret, OAuth code, access token, or other credentials to a public repository.** The `.gitignore` excludes future additions of `.xlsx`, `.xlsm`, `.env`, and `access_token.txt` files. This does not automatically remove a workbook that was already committed in Git history.

## Run

```powershell
py run.py
```

The launcher obtains the Shoonya OAuth code using `get_auth.py`, then starts the live application in `Extreme_Reversal_Signal.py`.

## Excel order controls

The `symbols` sheet contains the symbol, live data, order controls, and historical data. Enter a valid positive quantity before entering a BUY or SELL trigger. A blank or invalid quantity is skipped; it is never silently converted to quantity 1.

An order is first shown as `SUBMITTED: <order number>`. The WebSocket order-update callback then replaces that with the broker-reported status (for example, OPEN, COMPLETE, or REJECTED). A submitted order is **not** the same as a completed/filled order.

When no explicit limit price is passed, the current live LTP is required. The existing limit-price offset is retained: LTP + 5 for BUY and LTP - 5 for SELL. If there is no valid LTP or the resulting price is invalid, the application does not send the order.

## Safety and troubleshooting

- Keep the two safety/duplicate files in the repository unchanged.
- Keep your local workbook and credentials private.
- Review broker order status in Shoonya as well as the Excel status cells.
- Check the console when a symbol, live LTP, or quantity is invalid.
- Instrument-master CSV files are cached locally in the `Instrument` folder and are not intended for Git.
- Indicator formula and signal conditions are intentionally retained in this maintenance update.
