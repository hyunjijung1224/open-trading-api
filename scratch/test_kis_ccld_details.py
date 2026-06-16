import os
import sys
import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config import config

base_url = config.KIS_BASE_URL
app_key = config.KIS_APP_KEY
app_secret = config.KIS_APP_SECRET
account_no = config.KIS_ACCOUNT_NO
is_paper = config.KIS_IS_PAPER

token_url = f"{base_url}/oauth2/tokenP"
token_res = requests.post(token_url, json={
    "grant_type": "client_credentials",
    "appkey": app_key,
    "appsecret": app_secret
})
access_token = token_res.json()["access_token"]

cano, prdt_cd = account_no.split("-")
ccld_url = f"{base_url}/uapi/domestic-futureoption/v1/trading/inquire-ccld"
tr_id_ccld = "VTTC0807R" if is_paper else "TTTC0807R"

headers = {
    "Content-Type": "application/json",
    "authorization": f"Bearer {access_token}",
    "appkey": app_key,
    "appsecret": app_secret,
    "tr_id": tr_id_ccld
}

params = {
    "CANO": cano,
    "ACNT_PRDT_CD": prdt_cd,
    "ORD_DT": "20260615",
    "ORD_GUBUN": "00",
    "QUERY_INDEX": "",
    "CTX_AREA_FK200": "",
    "CTX_AREA_NK200": ""
}

res = requests.get(ccld_url, headers=headers, params=params)
print("Status Code:", res.status_code)
print("Headers:", dict(res.headers))
try:
    print("Response JSON:", res.json())
except Exception:
    print("Response Text:", res.text)
