from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
import pyotp
import time
import xlwings as xw
wb = xw.Book("symbols.xlsx")
ws = wb.sheets["LOGIN"]

try:
    import requests
    response = requests.get('https://api.ipify.org', timeout=5)
    print(f"\nYour current IP address is: {response.text}")
    ws.range("c3").value =f"\nYour current IP address is: {response.text}"
except:
    pass


# ───── CONFIG ─────

CLIENT_ID   = "FA_U" # Replace abc with your Client _id 
USER_ID     = "FA3" # Replace abc  with your User_id
PASSWORD    = "Fer@" # Replace abc with your trading Account password
TOTP_SECRET = "I326PXX5323" # Replace abc with 32 base string 
#LOGIN_URL   = f"https://trade.shoonya.com/OAuthlogin/investor-entry-level/login?api_key={CLIENT_ID}&route_to= abc" # replace abc with userid
SECRET_CODE = "MKoooJy1kdZQDnmMfCe3Xfmi1PlQI" # Replace abc with your secret_code
#TOKEN_URL   = "https://trade.shoonya.com/NorenWClientAPI/GenAcsTok"

LOGIN_URL = (
    f"https://trade.shoonya.com/OAuthlogin/"
    f"investor-entry-level/login?"
    f"api_key={CLIENT_ID}&route_to={USER_ID}"
)

# ───── OPEN CHROME ─────

driver = webdriver.Chrome(
    service=Service(ChromeDriverManager().install())
)

driver.maximize_window()

driver.get(LOGIN_URL)

time.sleep(3)

# ───── INPUT BOXES ─────

inputs = [
    x for x in driver.find_elements(By.TAG_NAME, "input")
    if x.is_displayed()
]

# USER ID
inputs[0].send_keys(USER_ID)

# PASSWORD
inputs[1].send_keys(PASSWORD)

# OTP
otp = pyotp.TOTP(TOTP_SECRET).now()

inputs[2].send_keys(otp)

time.sleep(1)

# ───── CLICK LOGIN ─────

for b in driver.find_elements(By.TAG_NAME, "button"):

    if "LOGIN" in b.text.upper():

        driver.execute_script(
            "arguments[0].click();",
            b
        )

        print("LOGIN CLICKED")

        break

# ───── GET AUTH CODE ─────

while True:

    url = driver.current_url

    print(url)

    if "#/?code=" in url:

        code = url.split("code=")[1]
        
        ws.range("b7").value = code
        
        print("\nAUTH CODE:")
        print(code)

        break

    time.sleep(0.2)

driver.quit()
