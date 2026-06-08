# get_auth.py - Run this first to get auth code

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
import pyotp
import time
import xlwings as xw

print("="*60)
print("SHOONYA AUTHENTICATION")
print("="*60)

# Open Excel
wb = xw.Book("symbols.xlsx")
ws = wb.sheets["LOGIN"]

# Read credentials from Excel
CLIENT_ID = ws.range("B2").value
USER_ID = ws.range("B3").value
PASSWORD = ws.range("B4").value
TOTP_SECRET = ws.range("B5").value
SECRET_CODE = ws.range("B6").value

print(f"Client ID: {CLIENT_ID}")
print(f"User ID: {USER_ID}")
print("Getting auth code...")

# Login URL
LOGIN_URL = f"https://trade.shoonya.com/OAuthlogin/investor-entry-level/login?api_key={CLIENT_ID}&route_to={USER_ID}"

# Open browser
driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()))
driver.maximize_window()
driver.get(LOGIN_URL)
time.sleep(3)

# Fill credentials
inputs = [x for x in driver.find_elements(By.TAG_NAME, "input") if x.is_displayed()]
inputs[0].send_keys(USER_ID)
inputs[1].send_keys(PASSWORD)
otp = pyotp.TOTP(TOTP_SECRET).now()
inputs[2].send_keys(otp)
time.sleep(1)

# Click login
for b in driver.find_elements(By.TAG_NAME, "button"):
    if "LOGIN" in b.text.upper():
        driver.execute_script("arguments[0].click();", b)
        print("Login clicked")
        break

# Get auth code
while True:
    url = driver.current_url
    if "#/?code=" in url:
        code = url.split("code=")[1]
        ws.range("B7").value = code
        wb.save()
        print(f"\n✅ AUTH CODE: {code}")
        print("✅ Saved to Excel (B7)")
        break
    time.sleep(0.2)

driver.quit()
print("\n✅ Authentication complete! You can now run bb_trader.py")
