
import json
import time
import re
from pathlib import Path
from urllib.parse import parse_qs, unquote_plus

import pyotp
import xlwings as xw

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager


# ============================================================
# SETTINGS
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
WORKBOOK = BASE_DIR / "symbols.xlsx"

SHEET_NAME = "LOGIN"
USER_CELL = "B3"
PASSWORD_CELL = "B4"
TOTP_CELL = "B5"

OUTPUT_LABEL_CELL = "A20"
OUTPUT_KEY_CELL = "B20"

SHOONYA_URL = "https://trade.shoonya.com/#/"


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

    return wb, ws, user, password, secret


def make_totp(secret):
    totp = pyotp.TOTP(secret)
    remaining = totp.interval - (time.time() % totp.interval)

    if remaining < 4:
        time.sleep(remaining + 0.5)

    return totp.now()


# ============================================================
# SHADOW DOM
# ============================================================

DEEP_QUERY_JS = """
const selector = arguments[0];
const results = [];

function walk(root) {
    try {
        root.querySelectorAll(selector).forEach(el => results.push(el));

        root.querySelectorAll('*').forEach(el => {
            if (el.shadowRoot) walk(el.shadowRoot);
        });
    } catch (e) {}
}

walk(document);
return results;
"""


def deep_find(driver, selector):
    try:
        return driver.execute_script(DEEP_QUERY_JS, selector) or []
    except Exception:
        return []


def get_box(driver, element):
    try:
        return driver.execute_script("""
            const r = arguments[0].getBoundingClientRect();
            return {
                x: r.x,
                y: r.y,
                width: r.width,
                height: r.height
            };
        """, element)
    except Exception:
        return None


def wait_for_flutter(driver):
    print("Waiting for Shoonya Flutter page...")

    deadline = time.time() + 60

    while time.time() < deadline:
        try:
            found = driver.execute_script("""
                return !!document.querySelector('flt-glass-pane');
            """)

            if found:
                print("Flutter page detected.")
                return True
        except Exception:
            pass

        time.sleep(1)

    return False


def enable_accessibility(driver):
    print("Enabling Flutter accessibility...")

    try:
        result = driver.execute_script("""
            function findPlaceholder(root) {
                const p = root.querySelector(
                    'flt-semantics-placeholder'
                );

                if (p) return p;

                for (const el of root.querySelectorAll('*')) {
                    if (el.shadowRoot) {
                        const found = findPlaceholder(el.shadowRoot);
                        if (found) return found;
                    }
                }

                return null;
            }

            const p = findPlaceholder(document);

            if (!p) return false;

            p.click();
            return true;
        """)

        print("Accessibility activation:", result)

    except Exception as e:
        print("Accessibility activation error:", str(e))

    time.sleep(3)


# ============================================================
# ACCESSIBLE FIELD DETAILS
# ============================================================

def get_accessible_fields(driver):
    elements = deep_find(driver, "input, textarea")
    fields = []

    for element in elements:
        box = get_box(driver, element)

        if not box:
            continue

        if box["width"] < 20 or box["height"] < 8:
            continue

        try:
            field_type = (
                element.get_attribute("type") or "text"
            ).lower()

            if field_type in (
                "hidden", "checkbox", "radio", "submit", "button"
            ):
                continue

            fields.append((element, box, field_type))

        except Exception:
            pass

    return fields


def print_field_details(fields):
    print("\n========== FLUTTER FIELD DETAILS ==========")

    ordered = sorted(fields, key=lambda item: item[1]["y"])

    for i, (field, box, field_type) in enumerate(ordered):
        try:
            details = {
                "type": field.get_attribute("type"),
                "aria-label": field.get_attribute("aria-label"),
                "placeholder": field.get_attribute("placeholder"),
                "name": field.get_attribute("name"),
                "role": field.get_attribute("role"),
                "autocomplete": field.get_attribute("autocomplete"),
                "y": round(box["y"], 1),
                "x": round(box["x"], 1),
                "width": round(box["width"], 1),
                "height": round(box["height"], 1)
            }

            print(f"FIELD {i + 1}: {details}")

        except Exception as e:
            print(f"FIELD {i + 1}: {e}")

    print("===========================================")


def identify_fields(fields):
    # Do not assume that a Flutter password field has
    # type=password. Use actual accessible labels instead.
    result = {
        "user": None,
        "password": None,
        "otp": None
    }

    for field, box, field_type in fields:
        try:
            label = " ".join([
                field.get_attribute("aria-label") or "",
                field.get_attribute("placeholder") or "",
                field.get_attribute("name") or "",
                field.get_attribute("autocomplete") or ""
            ]).lower()

            if any(word in label for word in (
                "user id", "userid", "username", "client id"
            )):
                result["user"] = (field, box, field_type)

            elif any(word in label for word in (
                "password", "passwd"
            )):
                result["password"] = (field, box, field_type)

            elif any(word in label for word in (
                "totp", "otp", "authenticator", "two factor"
            )):
                result["otp"] = (field, box, field_type)

        except Exception:
            pass

    if all(result.values()):
        return result

    print("\nField labels did not identify all three fields.")
    print_field_details(fields)

    # Deliberately do not guess based only on vertical order.
    return None


# ============================================================
# FIELD FILLING
# ============================================================

def read_value(element):
    try:
        return element.get_attribute("value") or ""
    except Exception:
        return ""


def fill_field(item, value, label, mask=False):
    element, box, field_type = item

    try:
        element.click()
        time.sleep(0.3)

        element.send_keys(Keys.CONTROL, "a")
        element.send_keys(Keys.BACKSPACE)
        element.send_keys(value)

        time.sleep(0.4)

        actual = read_value(element)

        if actual != value:
            # Flutter may expose a transformed/masked value through Selenium.
            # For password/TOTP fields, accept only when the field contains
            # the expected number of characters; never print the secret.
            if mask and len(actual) == len(value) and len(actual) > 0:
                print(
                    label + ": entered (character count verified; "
                    "browser read-back differs)"
                )
                return True

            print(
                label + ": verification failed "
                f"(expected {len(value)} characters, "
                f"read {len(actual)})."
            )
            return False

        display = "*" * len(value) if mask else value
        print(label + ": verified (" + display + ")")

        return True

    except Exception as e:
        print(label + ": error:", str(e))
        return False


def fill_login(fields, user, password, secret):
    # Fill User ID first.
    if not fill_field(fields["user"], user, "USER ID"):
        return False

    # Fill Password second.
    if not fill_field(
        fields["password"], password, "PASSWORD", mask=True
    ):
        return False

    # Generate TOTP only when the first two fields are ready.
    # make_totp() also avoids using a code that is about to expire.
    otp = make_totp(secret)

    # Enter the fresh TOTP last.
    if not fill_field(fields["otp"], otp, "TOTP", mask=True):
        return False

    return True


# ============================================================
# LOGIN BUTTON
# ============================================================

def find_login_button(driver):
    elements = deep_find(
        driver,
        "flt-semantics, button, [role='button']"
    )

    for element in elements:
        try:
            label = " ".join([
                element.get_attribute("aria-label") or "",
                element.get_attribute("title") or "",
                element.text or ""
            ]).lower()

            if "login" in label or "log in" in label:
                box = get_box(driver, element)

                if box and box["width"] > 5 and box["height"] > 5:
                    return element

        except Exception:
            pass

    return None


def click_login(driver):
    button = find_login_button(driver)

    if not button:
        print("Login button not identified.")
        return False

    try:
        button.click()
        print("Login button clicked.")
        return True
    except Exception as e:
        print("Login click failed:", str(e))
        return False


# ============================================================
# JKEY EXTRACTION
# ============================================================

def extract_jkey(data):
    if not data:
        return None

    raw = unquote_plus(str(data))

    try:
        parsed = parse_qs(raw)

        for key, values in parsed.items():
            if key.lower() == "jkey" and values and values[0]:
                return values[0]

    except Exception:
        pass

    try:
        obj = json.loads(raw)

        def search(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    if key.lower() == "jkey" and item:
                        return str(item)

                    found = search(item)
                    if found:
                        return found

            elif isinstance(value, list):
                for item in value:
                    found = search(item)
                    if found:
                        return found

            elif isinstance(value, str):
                try:
                    return search(json.loads(value))
                except Exception:
                    return None

            return None

        return search(obj)

    except Exception:
        pass

    match = re.search(
        r'["&?]jKey["=:\s]+([^&"\s,}]+)',
        raw,
        re.IGNORECASE
    )

    return match.group(1) if match else None


def check_network_logs(driver):
    try:
        entries = driver.get_log("performance")
    except Exception:
        return None

    for entry in entries:
        try:
            message = json.loads(entry["message"])["message"]
            method = message.get("method")
            params = message.get("params", {})

            if method == "Network.requestWillBeSent":
                request = params.get("request", {})
                url = request.get("url", "")

                if "NorenWClientWeb" not in url:
                    continue

                key = extract_jkey(request.get("postData", ""))

                if key:
                    return key

                request_id = params.get("requestId")

                if request_id:
                    try:
                        result = driver.execute_cdp_cmd(
                            "Network.getRequestPostData",
                            {"requestId": request_id}
                        )

                        key = extract_jkey(
                            result.get("postData", "")
                        )

                        if key:
                            return key

                    except Exception:
                        pass

        except Exception:
            pass

    return None


def wait_for_jkey(driver):
    print("Waiting for NorenWClientWeb request...")

    deadline = time.time() + 60

    while time.time() < deadline:
        key = check_network_logs(driver)

        if key:
            return key

        time.sleep(0.5)

    return None


# ============================================================
# MAIN
# ============================================================

def main():
    driver = None
    keep_open = False

    print("=" * 55)
    print("SHOONYA WEB JKEY CAPTURE")
    print("=" * 55)

    try:
        wb, ws, user, password, secret = read_credentials()

        print("Excel credentials loaded.")
        print("User ID:", user)
        print("Password: loaded (hidden)")
        print("TOTP secret: loaded (hidden)")

        options = webdriver.ChromeOptions()
        options.add_argument("--start-maximized")
        options.add_argument("--disable-notifications")

        options.set_capability(
            "goog:loggingPrefs",
            {"performance": "ALL"}
        )

        print("\nOpening Chrome...")

        driver = webdriver.Chrome(
            service=Service(ChromeDriverManager().install()),
            options=options
        )

        driver.execute_cdp_cmd("Network.enable", {})

        driver.get(SHOONYA_URL)

        if not wait_for_flutter(driver):
            keep_open = True
            print("Flutter page did not load.")
            return

        enable_accessibility(driver)

        fields = get_accessible_fields(driver)

        if len(fields) < 3:
            keep_open = True
            print("Fewer than three accessible fields found.")
            print_field_details(fields)
            return

        roles = identify_fields(fields)

        if not roles:
            keep_open = True
            print("\nNo credentials entered.")
            print("Need the field details to identify Shoonya's fields.")
            return

        if not fill_login(roles, user, password, secret):
            keep_open = True
            print("Login cancelled because verification failed.")
            return

        # Clear old network entries before login.
        try:
            driver.get_log("performance")
        except Exception:
            pass

        if not click_login(driver):
            keep_open = True
            return

        key = wait_for_jkey(driver)

        if not key:
            keep_open = True
            print("No web jKey captured. Browser left open.")
            return

        ws.range(OUTPUT_LABEL_CELL).value = "WEB_JKEY"
        ws.range(OUTPUT_KEY_CELL).value = key
        wb.save()

        print("\nWEB JKEY SAVED SUCCESSFULLY.")
        print("Excel destination: LOGIN!B20")
        print("Key value hidden.")

    except Exception as e:
        keep_open = True
        print("\nERROR:", repr(e))

    finally:
        if driver and not keep_open:
            try:
                driver.quit()
            except Exception:
                pass

        if keep_open:
            input("\nBrowser left open. Press ENTER to close...")


if __name__ == "__main__":
    main()
