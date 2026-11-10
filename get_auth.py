# get_auth.py
# Location: C:\Python315\Lib\site-packages\get_auth.py
# Fixes applied:
#   • line 19 workbook path — searches Desktop / OneDrive / Documents / CWD
#   • fills by Shoonya field IDs (lgnusrid / lgnpwd / lgnotp), not by index
#   • 3-second pause after each field
#   • Chrome closes after auth

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
import pyotp
import time
import xlwings as xw
import requests
from pathlib import Path
import os
import sys


INPUT_FILL_DELAY_SEC = 3.0
WORKBOOK_NAME        = "PythonTrader_Terminal_LMT.xlsm"


def _find_workbook():
    home = Path(os.path.expanduser("~"))
    candidates = [
        home / "Desktop" / WORKBOOK_NAME,
        home / "OneDrive" / "Desktop" / WORKBOOK_NAME,
        home / "OneDrive" / WORKBOOK_NAME,
        home / "Documents" / WORKBOOK_NAME,
        home / "Downloads" / WORKBOOK_NAME,
        home / WORKBOOK_NAME,
        Path.cwd() / WORKBOOK_NAME,
    ]
    if sys.argv and sys.argv[0]:
        candidates.append(Path(sys.argv[0]).resolve().parent / WORKBOOK_NAME)
    candidates.append(Path(__file__).resolve().parent / WORKBOOK_NAME)

    for c in candidates:
        try:
            if c.is_file():
                return c
        except (OSError, ValueError):
            continue

    raise FileNotFoundError(
        f"Cannot find {WORKBOOK_NAME}. Searched:\n  "
        + "\n  ".join(str(c) for c in candidates)
    )


def get_auth_code():
    """Function to get authentication code"""
    print("=" * 60)
    print("SHOONYA AUTHENTICATION")
    print("=" * 60)
    print(f"get_auth.py loaded from: {os.path.abspath(__file__)}")

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

    if not ws.range("A1").value:
        ws.range("A1:B11").value = [
            ["Field", "Value"], ["CLIENT_ID", ""], ["USER_ID", ""],
            ["PASSWORD", ""], ["TOTP_SECRET", ""], ["API_SECRET", ""],
            ["AUTH_CODE", ""], ["", ""], ["", ""], ["", ""], ["IP_ADDRESS", ""],
        ]

    CLIENT_ID   = str(ws.range("B2").value or "").strip()
    USER_ID     = str(ws.range("B3").value or "").strip()
    PASSWORD    = str(ws.range("B4").value or "")
    TOTP_SECRET = str(ws.range("B5").value or "").replace(" ", "").strip()

    try:
        ip = requests.get("https://api.ipify.org", timeout=5).text.strip()
        print(f"Your IP: {ip}")
        ws.range("B11").value = ip
        wb.save()
    except Exception:
        pass

    print(f"Client ID: {CLIENT_ID}")
    print(f"User ID: {USER_ID}")

    login_url = (
        "https://trade.shoonya.com/OAuthlogin/investor-entry-level/login"
        f"?api_key={CLIENT_ID}&route_to={USER_ID}"
    )

    options = Options()
    options.add_argument("--start-maximized")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    print("Launching Chrome via Selenium...")
    driver = webdriver.Chrome(
        service=Service(ChromeDriverManager().install()),
        options=options,
    )

    try:
        driver.get(login_url)
        time.sleep(3)

        def fill_by_id(element_id, value, label):
            el = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.ID, element_id))
            )
            el.clear()
            el.send_keys(value)
            print(f"  ✅ Filled {label} (id={element_id!r})")

        fill_by_id("lgnusrid", USER_ID, "User ID")
        print(f"     ⏳ pausing {INPUT_FILL_DELAY_SEC:.0f}s...")
        time.sleep(INPUT_FILL_DELAY_SEC)

        fill_by_id("lgnpwd", PASSWORD, "Password")
        print(f"     ⏳ pausing {INPUT_FILL_DELAY_SEC:.0f}s...")
        time.sleep(INPUT_FILL_DELAY_SEC)

        otp = pyotp.TOTP(TOTP_SECRET).now()
        print(f"  Computed TOTP: {otp}")
        fill_by_id("lgnotp", otp, "TOTP")
        print(f"     ⏳ pausing {INPUT_FILL_DELAY_SEC:.0f}s...")
        time.sleep(INPUT_FILL_DELAY_SEC)

        print("Clicking LOGIN...")
        clicked = False
        for b in driver.find_elements(By.TAG_NAME, "button"):
            try:
                if "LOGIN" in (b.text or "").upper():
                    driver.execute_script("arguments[0].click();", b)
                    clicked = True
                    print("  ✅ LOGIN clicked")
                    break
            except Exception:
                continue
        if not clicked:
            print("  ⚠️ LOGIN button not found")

        print("Waiting for OAuth redirect (up to 3 min)...")
        deadline = time.time() + 180
        while time.time() < deadline:
            url = driver.current_url
            if "code=" in url:
                for sep in ("#/?code=", "?code=", "&code="):
                    if sep in url:
                        code = url.split(sep, 1)[1].split("&")[0].split("#")[0]
                        ws.range("B7").value = code
                        wb.save()
                        print(f"\n✅ AUTH CODE saved to LOGIN!B7")
                        return code
            time.sleep(0.5)

        raise TimeoutError("No auth code received within 3 minutes")

    finally:
        try:
            driver.quit()
            print("Chrome closed.")
        except Exception:
            pass


if __name__ == "__main__":
    get_auth_code()
