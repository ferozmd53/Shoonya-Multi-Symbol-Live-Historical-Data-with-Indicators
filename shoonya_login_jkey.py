"""
Shoonya: auto login + save web jKey to Excel, using your ALREADY OPEN Chrome.
No Selenium, no new browser.  One script.

Needs, in the same folder:  symbols.xlsx  with a sheet named LOGIN
    B3 = User ID    B4 = Password    B5 = TOTP secret key
The jKey is saved to  LOGIN!B20  (label in A20).

Install once (Command Prompt):
    pip install pyotp pyautogui pywinauto xlwings

FIRST TIME ONLY: this script creates a small Chrome extension folder next to it
(shoonya_jkey_extension). Load it once:
    chrome://extensions -> turn on Developer mode -> Load unpacked -> pick that folder
Chrome does not allow any script to read its network traffic without an extension,
so this one-time step cannot be skipped.
"""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse

import pyautogui
import pyotp
import xlwings as xw
from pywinauto import Desktop

# ============================================================
# SETTINGS
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
WORKBOOK = BASE_DIR / "symbols.xlsx"
EXT_DIR = BASE_DIR / "shoonya_jkey_extension"

SHEET_NAME = "LOGIN"
USER_CELL = "B3"
PASSWORD_CELL = "B4"
TOTP_CELL = "B5"
LABEL_CELL = "A20"
JKEY_CELL = "B20"

# Names Chrome shows for the Shoonya login controls
USER_LABEL = "User ID"
PASSWORD_LABEL = "Password"
TOTP_LABEL = "OTP/TOTP"
LOGIN_LABEL = "Login"

LOCAL_HOST = "127.0.0.1"
LOCAL_PORT = 8765
JKEY_WAIT_SECONDS = 75

pyautogui.FAILSAFE = True   # throw the mouse to the top-left screen corner to abort

# ============================================================
# CHROME EXTENSION FILES (written automatically)
# ============================================================

MANIFEST = r"""{
  "manifest_version": 3,
  "name": "Shoonya JKEY Capture",
  "version": "1.1",
  "description": "Sends the Shoonya web jKey to a local Python receiver.",
  "permissions": ["webRequest"],
  "host_permissions": ["https://*/*", "http://127.0.0.1:8765/*"],
  "background": { "service_worker": "background.js" }
}
"""

BACKGROUND_JS = r"""let lastSent = "";

function bodyToText(rb) {
  if (!rb) return "";
  if (rb.raw) {
    return rb.raw.map(p => (p.bytes ? new TextDecoder().decode(p.bytes) : "")).join("");
  }
  if (rb.formData) {
    return Object.keys(rb.formData).map(k => k + "=" + (rb.formData[k][0] || "")).join("&");
  }
  return "";
}

chrome.webRequest.onBeforeRequest.addListener(
  details => {
    if (details.method !== "POST") return;
    const text = bodyToText(details.requestBody);
    const m = text.match(/jKey=([^&\s"]+)/i) || text.match(/"jKey"\s*:\s*"([^"]+)"/i);
    if (!m) return;

    let jkey = m[1];
    try { jkey = decodeURIComponent(jkey); } catch (e) {}
    if (!jkey || jkey === lastSent) return;
    lastSent = jkey;

    fetch("http://127.0.0.1:8765/jkey", {
      method: "POST",
      headers: { "Content-Type": "text/plain" },
      body: JSON.stringify({ jkey: jkey, request_url: details.url })
    })
      .then(r => console.log("jKey sent, status", r.status))
      .catch(e => console.log("jKey send failed:", String(e)));
  },
  { urls: ["*://*/NorenWClientWeb/*"] },
  ["requestBody"]
);
console.log("Shoonya JKEY Capture loaded");
"""


def write_extension_files():
    EXT_DIR.mkdir(exist_ok=True)
    for name, text in (("manifest.json", MANIFEST), ("background.js", BACKGROUND_JS)):
        path = EXT_DIR / name
        if not path.exists() or path.read_text(encoding="utf-8") != text:
            path.write_text(text, encoding="utf-8")


# ============================================================
# EXCEL
# ============================================================

def read_credentials():
    if not WORKBOOK.exists():
        raise FileNotFoundError(f"symbols.xlsx not found next to this script: {WORKBOOK}")

    wb = xw.Book(str(WORKBOOK))
    ws = wb.sheets[SHEET_NAME]

    user = str(ws.range(USER_CELL).value or "").strip()
    password = str(ws.range(PASSWORD_CELL).value or "").strip()
    secret = str(ws.range(TOTP_CELL).value or "").replace(" ", "").strip()

    if not user or not password or not secret:
        raise ValueError("Please fill LOGIN!B3, B4 and B5 in symbols.xlsx")

    return user, password, secret


def save_jkey(jkey):
    wb = xw.Book(str(WORKBOOK))
    ws = wb.sheets[SHEET_NAME]
    ws.range(LABEL_CELL).value = "WEB_JKEY"
    ws.range(JKEY_CELL).value = jkey
    wb.save()
    print(f"jKey saved to {WORKBOOK.name} -> {SHEET_NAME}!{JKEY_CELL}")


def make_totp(secret):
    totp = pyotp.TOTP(secret)
    remaining = totp.interval - (time.time() % totp.interval)
    if remaining < 4:                     # avoid a code that is about to expire
        time.sleep(remaining + 0.5)
    return totp.now()


# ============================================================
# LOCAL RECEIVER (gets the jKey from the extension)
# ============================================================

received = {"jkey": None}
jkey_event = threading.Event()


class Handler(BaseHTTPRequestHandler):
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        # open http://127.0.0.1:8765/ping in Chrome to test that the receiver is running
        self.send_response(200)
        self._cors()
        self.end_headers()
        self.wfile.write(b"receiver OK")

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            jkey = str(payload.get("jkey") or "").strip()
            url = str(payload.get("request_url") or "")

            if urlparse(self.path).path != "/jkey" or "NorenWClientWeb" not in url or len(jkey) < 12:
                self.send_response(400)
                self._cors()
                self.end_headers()
                return

            # Only remember it here. Excel is written by the main thread
            # (Excel automation does not work reliably from this background thread).
            received["jkey"] = jkey
            jkey_event.set()

            self.send_response(200)
            self._cors()
            self.end_headers()
            self.wfile.write(b"OK")
        except Exception as exc:
            print("Receiver error:", exc)
            self.send_response(500)
            self._cors()
            self.end_headers()

    def log_message(self, fmt, *args):
        return   # never print request contents


def start_receiver():
    server = HTTPServer((LOCAL_HOST, LOCAL_PORT), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"Receiver listening on http://{LOCAL_HOST}:{LOCAL_PORT}")
    return server


# ============================================================
# YOUR EXISTING CHROME (pywinauto)
# ============================================================

def get_url(win):
    for e in win.descendants(control_type="Edit"):
        try:
            v = e.get_value()
        except Exception:
            continue
        name = e.window_text().lower()
        if v and ("address" in name or "." in v or "/" in v):
            return v
    return ""


def find_shoonya_tab():
    for win in Desktop(backend="uia").windows(class_name="Chrome_WidgetWin_1"):
        try:
            for tab in win.descendants(control_type="TabItem"):
                if "SHOONYA" in (tab.window_text() or "").upper():
                    return win, tab
        except Exception:
            pass
    return None, None


def activate_shoonya():
    win, tab = find_shoonya_tab()
    if not tab:
        raise RuntimeError("SHOONYA tab not found. Keep the Shoonya tab open in Chrome.")

    win.set_focus()
    tab.click_input()
    time.sleep(1.5)

    url = get_url(win).lower()
    if not tab.is_selected() or "shoonya" not in url:
        raise RuntimeError(f"Shoonya tab is not in front (URL read: {url!r}). Nothing typed.")
    return win


# ============================================================
# FLUTTER LOGIN FIELDS (found by name)
# ============================================================

def find_by_name(win, control_type, name):
    for e in win.descendants(control_type=control_type):
        try:
            if (e.window_text() or "").strip() == name:
                return e
        except Exception:
            pass
    return None


def enable_accessibility(win):
    for e in win.descendants(control_type="Button"):
        try:
            if "enable accessibility" in (e.window_text() or "").lower():
                try:
                    e.invoke()
                except Exception:
                    e.click_input()
                return
        except Exception:
            pass


def get_login_fields(win, timeout):
    """Returns the 4 login controls, or None if the login form is not showing."""
    deadline = time.time() + timeout
    last_try = 0

    while time.time() < deadline:
        fields = {
            "user": find_by_name(win, "Edit", USER_LABEL),
            "password": find_by_name(win, "Edit", PASSWORD_LABEL),
            "totp": find_by_name(win, "Edit", TOTP_LABEL),
            "login": find_by_name(win, "Button", LOGIN_LABEL),
        }
        if all(fields.values()):
            return fields

        if time.time() - last_try > 4:
            enable_accessibility(win)
            last_try = time.time()
        time.sleep(1)

    return None


def type_into(element, text):
    element.click_input()
    time.sleep(0.5)
    pyautogui.hotkey("ctrl", "a")
    pyautogui.press("backspace")
    pyautogui.write(text, interval=0.04)


def do_login(win, user, password, secret):
    fields = get_login_fields(win, timeout=12)

    if not fields:
        print("Login form not showing: Shoonya looks already logged in.")
        return

    print("Login form detected. Filling it...")
    type_into(fields["user"], user)
    type_into(fields["password"], password)
    type_into(fields["totp"], make_totp(secret))   # fresh code at the last moment

    time.sleep(0.4)
    fields["login"].click_input()
    print("Login clicked.")
    time.sleep(8)

    if get_login_fields(win, timeout=3):
        raise RuntimeError("Login form is still showing. Check the page for an error message.")
    print("Login form is gone: login looks successful.")


# ============================================================
# MAIN
# ============================================================

def main():
    print("=" * 58)
    print("SHOONYA AUTO LOGIN + WEB JKEY TO EXCEL")
    print("=" * 58)
    print("Do not touch the mouse or keyboard while this runs.\n")

    write_extension_files()
    print(f"Extension folder: {EXT_DIR}")

    user, password, secret = read_credentials()
    print("Excel credentials loaded (password and TOTP hidden).")

    server = start_receiver()
    try:
        win = activate_shoonya()
        print("Existing Shoonya tab is active.")

        do_login(win, user, password, secret)

        # A refresh makes the page send fresh requests that carry the jKey
        print("Refreshing the Shoonya tab to trigger requests...")
        win.set_focus()
        time.sleep(0.5)
        pyautogui.hotkey("ctrl", "r")

        print("Waiting for the jKey from the extension...")
        if not jkey_event.wait(JKEY_WAIT_SECONDS):
            print("\njKey NOT received. Check these:")
            print(f" 1. chrome://extensions -> 'Shoonya JKEY Capture' is loaded ({EXT_DIR}) and ON")
            print("    (first time: Developer mode -> Load unpacked -> choose that folder).")
            print(" 2. After loading it, refresh the Shoonya tab and run this script again.")
            print(" 3. On chrome://extensions click 'service worker' on the extension to see its messages.")
            return

        save_jkey(received["jkey"])
        print("DONE.")

    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("\nERROR:", error)
