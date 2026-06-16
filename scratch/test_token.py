import os
import requests
from dotenv import load_dotenv

load_dotenv()

base_url = "https://openapivts.koreainvestment.com:29443"
app_key = os.getenv("KIS_APP_KEY")
app_secret = os.getenv("KIS_APP_SECRET")

print("App Key:", app_key)
print("Base URL:", base_url)

token_url = f"{base_url}/oauth2/tokenP"
res = requests.post(token_url, json={
    "grant_type": "client_credentials",
    "appkey": app_key,
    "appsecret": app_secret
})

print("Status Code:", res.status_code)
print("Response:", res.text)
