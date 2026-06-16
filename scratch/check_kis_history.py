import os
import sys
import json
import requests
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config import config

base_url = config.KIS_BASE_URL
app_key = config.KIS_APP_KEY
app_secret = config.KIS_APP_SECRET
account_no = config.KIS_ACCOUNT_NO
is_paper = config.KIS_IS_PAPER

print(f"Base URL: {base_url}")
print(f"Is Paper: {is_paper}")
print(f"Account: {account_no}")

# 1. Fetch token
token_url = f"{base_url}/oauth2/tokenP"
token_res = requests.post(token_url, json={
    "grant_type": "client_credentials",
    "appkey": app_key,
    "appsecret": app_secret
})
access_token = token_res.json()["access_token"]
print("Token fetched successfully.")

# 2. Query Inquire Balance to check current status
cano, prdt_cd = account_no.split("-")
balance_url = f"{base_url}/uapi/domestic-futureoption/v1/trading/inquire-balance"
tr_id_bal = "VTFO6118R" if is_paper else "CTFO6118R"
headers_bal = {
    "Content-Type": "application/json",
    "authorization": f"Bearer {access_token}",
    "appkey": app_key,
    "appsecret": app_secret,
    "tr_id": tr_id_bal
}
params_bal = {
    "CANO": cano,
    "ACNT_PRDT_CD": prdt_cd,
    "MGNA_DVSN": "01",
    "EXCC_STAT_CD": "1",
    "CTX_AREA_FK200": "",
    "CTX_AREA_NK200": ""
}
res_bal = requests.get(balance_url, headers=headers_bal, params=params_bal)
print("\n=== KIS Balance & Positions ===")
if res_bal.status_code == 200:
    bal_data = res_bal.json()
    print("rt_cd:", bal_data.get("rt_cd"))
    print("msg:", bal_data.get("msg1"))
    print("Positions (output1):", bal_data.get("output1"))
    print("Account info (output2):", bal_data.get("output2"))
else:
    print("Failed to query balance:", res_bal.text)

# 3. Query Inquire CCDL (체결/미체결 내역)
ccld_url = f"{base_url}/uapi/domestic-futureoption/v1/trading/inquire-ccld"
tr_id_ccld = "VTTC0807R" if is_paper else "TTTC0807R"
headers_ccld = {
    "Content-Type": "application/json",
    "authorization": f"Bearer {access_token}",
    "appkey": app_key,
    "appsecret": app_secret,
    "tr_id": tr_id_ccld
}
params_ccld = {
    "CANO": cano,
    "ACNT_PRDT_CD": prdt_cd,
    "ORD_DT": "20260615", # Today's date
    "ORD_GUBUN": "00", # 00: 전체, 01: 체결, 02: 미체결
    "QUERY_INDEX": "",
    "CTX_AREA_FK200": "",
    "CTX_AREA_NK200": ""
}
res_ccld = requests.get(ccld_url, headers=headers_ccld, params=params_ccld)
print("\n=== KIS Orders/Executions Today (2026-06-15) ===")
if res_ccld.status_code == 200:
    ccld_data = res_ccld.json()
    print("rt_cd:", ccld_data.get("rt_cd"))
    print("msg:", ccld_data.get("msg1"))
    print("Executions (output):", ccld_data.get("output"))
else:
    print("Failed to query executions:", res_ccld.text)
