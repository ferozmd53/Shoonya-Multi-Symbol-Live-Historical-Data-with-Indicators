# get_auth.py - Chrome DevTools Protocol login (no ChromeDriver executable)
import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

import pyotp
import requests
import websocket
import xlwings as xw


def _find_workbook():
    filename = "PythonTrader_Terminal_LMT.xlsm"
    candidates = [
        Path.home() / "Desktop" / filename,
        Path.home() / "OneDrive" / "Desktop" / filename,
        Path.cwd() / filename,
        Path(__file__).resolve().parent / filename,
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(
        f"Cannot find {filename}. Checked: " + ", ".join(str(p) for p in candidates)
    )


def _find_chrome():
    paths = [
        os.path.join(os.environ.get("PROGRAMFILES", r"C:\Program Files"), "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"), "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Google", "Chrome", "Application", "chrome.exe"),
    ]
    for path in paths:
        if path and os.path.isfile(path):
            return path
    found = shutil.which("chrome.exe") or shutil.which("chrome")
    if found:
        return found
    raise FileNotFoundError("Google Chrome chrome.exe was not found.")


def _free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _http_json(url):
    with urllib.request.urlopen(url, timeout=2) as response:
        return json.loads(response.read().decode("utf-8"))


def _cdp_call(ws, command_id, method, params=None):
    ws.send(json.dumps({
        "id": command_id,
        "method": method,
        "params": params or {}
    }))
    while True:
        message = json.loads(ws.recv())
        if message.get("id") == command_id:
            if "error" in message:
                raise RuntimeError(message["error"])
            return message.get("result", {})


def _evaluate(ws, command_id, expression):
    result = _cdp_call(ws, command_id, "Runtime.evaluate", {
        "expression": expression,
        "returnByValue": True,
        "awaitPromise": True
    })
    return result.get("result", {}).get("value")


def get_auth_code():
    print("=" * 60)
    print("SHOONYA AUTHENTICATION")
    print("=" * 60)

    workbook_path = _find_workbook()
    print(f"📘 Excel workbook: {workbook_path}")
    wb = xw.Book(str(workbook_path))
    try:
        ws = wb.sheets["LOGIN"]
    except Exception:
        ws = wb.sheets.add("LOGIN")
        ws.range("A1:B11").value = [
            ["Field", "Value"], ["CLIENT_ID", ""], ["USER_ID", ""],
            ["PASSWORD", ""], ["TOTP_SECRET", ""], ["API_SECRET", ""],
            ["AUTH_CODE", ""], ["", ""], ["", ""], ["", ""], ["IP_ADDRESS", ""],
        ]

    CLIENT_ID = str(ws.range("B2").value or "").strip()
    USER_ID = str(ws.range("B3").value or "").strip()
    PASSWORD = str(ws.range("B4").value or "")
    TOTP_SECRET = str(ws.range("B5").value or "").replace(" ", "").strip()
    if not all([CLIENT_ID, USER_ID, PASSWORD, TOTP_SECRET]):
        raise ValueError("LOGIN sheet B2:B5 must contain Client ID, User ID, Password and TOTP Secret.")

    try:
        ip = requests.get("https://api.ipify.org", timeout=5).text.strip()
        print(f"Your IP: {ip}")
        ws.range("B11").value = ip
        wb.save()
    except Exception as exc:
        print(f"IP lookup skipped: {exc}")

    print(f"Client ID: {CLIENT_ID}")
    print(f"User ID: {USER_ID}")

    login_url = (
        "https://trade.shoonya.com/OAuthlogin/investor-entry-level/login"
        f"?api_key={CLIENT_ID}&route_to={USER_ID}"
    )
    chrome = _find_chrome()
    port = _free_port()
    profile_dir = tempfile.mkdtemp(prefix="shoonya_chrome_")
    command = [
        chrome,
        f"--remote-debugging-port={port}",
        "--remote-debugging-address=127.0.0.1",
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--new-window",
        login_url,
    ]
    print("Starting Chrome using its built-in debugging interface (no ChromeDriver).")
    chrome_process = subprocess.Popen(command)

    ws = None
    try:
        version = None
        for _ in range(40):
            if chrome_process.poll() is not None:
                raise RuntimeError("Chrome closed before its debugging interface started.")
            try:
                version = _http_json(f"http://127.0.0.1:{port}/json/version")
                break
            except Exception:
                time.sleep(0.5)
        if not version:
            raise RuntimeError("Chrome debugging interface did not start. Check whether Windows allows chrome.exe.")

        targets = _http_json(f"http://127.0.0.1:{port}/json")
        page = next((item for item in targets if item.get("type") == "page"), None)
        if not page:
            raise RuntimeError("Could not find the Shoonya login tab.")

        ws = websocket.create_connection(
            page["webSocketDebuggerUrl"], timeout=10, suppress_origin=True
        )
        _cdp_call(ws, 1, "Page.enable")
        _cdp_call(ws, 2, "Runtime.enable")
        time.sleep(3)

        # Fill the first three visible, enabled login fields in page order.
        fill_js = """
        (() => {
          const visible = e => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
          const fields = [...document.querySelectorAll('input')]
            .filter(e => visible(e) && !e.disabled &&
              !['hidden','submit','button','checkbox','radio'].includes((e.type || '').toLowerCase()));
          return fields.map(e => ({
            type: e.type || '',
            name: e.name || '',
            id: e.id || '',
            placeholder: e.placeholder || '',
            value: e.value || ''
          }));
        })()
        """
        field_info = _evaluate(ws, 3, fill_js) or []
        print(f"Visible login fields detected: {len(field_info)}")
        if len(field_info) < 3:
            raise RuntimeError(
                "Shoonya page did not show three visible login fields. "
                "Chrome is open; inspect the page and report what fields are visible."
            )

        values = [USER_ID, PASSWORD, pyotp.TOTP(TOTP_SECRET).now()]
        fill_values_js = """
        (values) => {
          const visible = e => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
          const fields = [...document.querySelectorAll('input')]
            .filter(e => visible(e) && !e.disabled &&
              !['hidden','submit','button','checkbox','radio'].includes((e.type || '').toLowerCase()));
          const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
          values.forEach((value, i) => {
            if (fields[i]) {
              fields[i].focus();
              setter.call(fields[i], value);
              fields[i].dispatchEvent(new Event('input', {bubbles:true}));
              fields[i].dispatchEvent(new Event('change', {bubbles:true}));
              fields[i].blur();
            }
          });
          return fields.slice(0, values.length).map(e => e.value);
        }
        """
        _cdp_call(ws, 4, "Runtime.callFunctionOn", {}) if False else None
        expression = (
            "(" + fill_values_js + ")(" + json.dumps(values) + ")"
        )
        filled = _evaluate(ws, 5, expression)
        if not filled or filled[:3] != values:
            raise RuntimeError("Could not confirm all three login fields were filled.")

        print("User ID, password and current TOTP filled.")
        click_js = """
        (() => {
          const els = [...document.querySelectorAll('button,[role="button"],input[type="submit"]')];
          const target = els.find(e => {
            const t = ((e.innerText || e.value || e.getAttribute('aria-label') || '')).trim().toUpperCase();
            return t === 'LOGIN' || t.includes('LOGIN');
          });
          if (target) { target.click(); return 'LOGIN clicked'; }
          return 'LOGIN button not found';
        })()
        """
        click_result = _evaluate(ws, 6, click_js)
        print(click_result)

        print("Waiting up to 3 minutes for Shoonya OAuth redirect...")
        deadline = time.time() + 180
        while time.time() < deadline:
            try:
                current_url = _evaluate(ws, 7, "location.href") or ""
                match = re.search(r"[#&?]/?(?:\?|&)??code=([^&]+)", current_url)
                if not match:
                    match = re.search(r"code=([^&]+)", current_url)
                if match:
                    code = match.group(1)
                    ws.range("B7").value = code
                    wb.save()
                    print("\n✅ AUTH CODE saved to LOGIN!B7")
                    return code
                title = _evaluate(ws, 8, "document.title")
                time.sleep(0.5)
            except Exception:
                time.sleep(0.5)

        raise TimeoutError(
            "No OAuth code received within 3 minutes. Check the Chrome window for "
            "Shoonya validation, OTP, or confirmation messages."
        )
    finally:
        if ws:
            try:
                ws.close()
            except Exception:
                pass
        # Leave Chrome open so the user can inspect any Shoonya message.
        print("Chrome has been left open for inspection.")


if __name__ == "__main__":
    get_auth_code()
