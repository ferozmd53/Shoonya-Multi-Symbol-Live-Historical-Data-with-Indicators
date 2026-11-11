# Shoonya Multi-Symbol Live / Historical Data with Indicators

Python application for Shoonya market data, multi-symbol Excel monitoring, StochRSI and Bollinger Band indicators, and optional Excel-triggered orders.

> **Risk notice:** This software can submit live orders to a brokerage account. Review the code, verify the Excel workbook and quantity cells, and test in a non-live environment before enabling live order triggers. You are responsible for all orders submitted from your account.

## Requirements

- Windows
- Python 3.8 or newer
- Microsoft Excel desktop (required by xlwings)
- A valid Shoonya account and API access

## Install

Clone this repository, open a terminal in the project folder, then run:

```powershell
py -m pip install --upgrade pip
py -m pip install -r requirements.txt
py -m pip install -e .
```

The requirements file installs the Shoonya API client, Excel integration, pandas/numpy, HTTP, TOTP, and WebSocket dependencies. The package setup also installs `instrument_master.py` as an importable module.

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
