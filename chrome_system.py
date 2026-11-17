import time
from pathlib import Path

import pyautogui
import pyotp
import xlwings as xw
from pywinauto import Desktop

# ============================================================
# SETTINGS
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
WORKBOOK = BASE_DIR / "symbols.xlsx"

SHEET_NAME = "LOGIN"
USER_CELL = "B3"
PASSWORD_CELL = "B4"
TOTP_CELL = "B5"

# Names Chrome exposes for the Shoonya (Flutter) login page
USER_LABEL = "User ID"
PASSWORD_LABEL = "Password"
TOTP_LABEL = "OTP/TOTP"
LOGIN_LABEL = "Login"

pyautogui.FAILSAFE = True   # throw the mouse to the top-left screen corner to abort


# ============================================================
# EXCEL
# ============================================================

def read_credentials():
    if not WORKBOOK.exists():
        raise FileNotFoundError(f"Workbook not found: {WORKBOOK}")

    wb = xw.Book(str(WORKBOOK))
    ws = wb.sheets[SHEET_NAME]

    user = str(ws.range(USER_CELL).value or "").strip()
    password = str(ws.range(PASSWORD_CELL).value or "").strip()
    secret = str(ws.range(TOTP_CELL).value or "").replace(" ", "").strip()

    if not user or not password or not secret:
        raise ValueError("Check LOGIN!B3, B4 and B5 in symbols.xlsx")

    return user, password, secret


def make_totp(secret):
    totp = pyotp.TOTP(secret)
    remaining = totp.interval - (time.time() % totp.interval)
    if remaining < 4:
        time.sleep(remaining + 0.5)
    return totp.now()


# ============================================================
# CHROME (pywinauto)
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


def list_chrome():
    for win in Desktop(backend="uia").windows(class_name="Chrome_WidgetWin_1"):
        tabs = [t.window_text() for t in win.descendants(control_type="TabItem")]
        if not tabs:
            continue
        print("WINDOW:", win.window_text())
        print("Active tab URL:", get_url(win))
        for i, t in enumerate(tabs, 1):
            print(f"  {i}. {t}")


def find_shoonya_tab():
    for win in Desktop(backend="uia").windows(class_name="Chrome_WidgetWin_1"):
        for tab in win.descendants(control_type="TabItem"):
            if "SHOONYA" in tab.window_text().upper():
                return win, tab
    return None, None


def bring_shoonya_to_front():
    win, tab = find_shoonya_tab()
    if not tab:
        raise RuntimeError("SHOONYA tab not found in open Chrome.")

    win.set_focus()
    tab.click_input()
    time.sleep(1.5)

    # Safety: never type unless the Shoonya tab is really the active one.
    url = get_url(win).lower()
    if not tab.is_selected() or "shoonya" not in url:
        raise RuntimeError(
            f"Shoonya tab is not in front (URL read: {url!r}). Nothing typed."
        )
    return win


# ============================================================
# FLUTTER FIELD DETECTION
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
    # Flutter hides an "Enable accessibility" button; pressing it exposes the real fields.
    for e in win.descendants(control_type="Button"):
        try:
            if "enable accessibility" in (e.window_text() or "").lower():
                try:
                    e.invoke()
                except Exception:
                    e.click_input()
                print("Flutter accessibility enabled.")
                return True
        except Exception:
            pass
    return False


def wait_for_fields(win, timeout=15):
    deadline = time.time() + timeout
    tried_enable = False

    while time.time() < deadline:
        fields = {
            "user": find_by_name(win, "Edit", USER_LABEL),
            "password": find_by_name(win, "Edit", PASSWORD_LABEL),
            "totp": find_by_name(win, "Edit", TOTP_LABEL),
            "login": find_by_name(win, "Button", LOGIN_LABEL),
        }
        if all(fields.values()):
            return fields

        if not tried_enable:
            enable_accessibility(win)
            tried_enable = True

        time.sleep(1)

    missing = [k for k, v in fields.items() if not v]
    raise RuntimeError(f"Login fields not detected: {missing}. Is the Shoonya login page showing?")


# ============================================================
# LOGIN
# ============================================================

def type_into(element, text):
    element.click_input()        # real mouse click on the detected field
    time.sleep(0.5)
    pyautogui.hotkey("ctrl", "a")
    pyautogui.press("backspace")
    pyautogui.write(text, interval=0.04)


def do_login():
    user, password, secret = read_credentials()
    print("Credentials loaded from Excel (password and TOTP hidden).")

    win = bring_shoonya_to_front()
    print("Shoonya tab is active:", get_url(win))

    fields = wait_for_fields(win)
    print("Fields detected: User ID, Password, OTP/TOTP, Login button")

    type_into(fields["user"], user)
    type_into(fields["password"], password)
    type_into(fields["totp"], make_totp(secret))   # fresh code at the last moment

    time.sleep(0.4)
    fields["login"].click_input()
    print("Login clicked.")

    time.sleep(5)
    if find_by_name(win, "Edit", USER_LABEL):
        print("Login form is still showing: login may have failed. Check the page.")
    else:
        print("Login form is gone: login appears to have gone through.")


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":
    try:
        list_chrome()
        do_login()
    except Exception as e:
        print("ERROR:", e)
