import os
import sys
import requests
from dotenv import load_dotenv

load_dotenv()

base_url = "https://openapivts.koreainvestment.com:29443"
app_key = os.getenv("KIS_APP_KEY")
app_secret = os.getenv("KIS_APP_SECRET")
account_no = os.getenv("KIS_ACCOUNT_NO")

print("Base URL:", base_url)
print("Account:", account_no)

token_url = f"{base_url}/oauth2/tokenP"
token_res = requests.post(token_url, json={
    "grant_type": "client_credentials",
    "appkey": app_key,
    "appsecret": app_secret
})

if token_res.status_code != 200:
    print("Failed to get token:", token_res.text)
    sys.exit(1)

access_token = token_res.json()["access_token"]
print("Token fetched successfully.")

cano, prdt_cd = account_no.split("-")

# 1. Query Balance
balance_url = f"{base_url}/uapi/domestic-futureoption/v1/trading/inquire-balance"
headers = {
    "Content-Type": "application/json",
    "authorization": f"Bearer {access_token}",
    "appkey": app_key,
    "appsecret": app_secret,
    "tr_id": "VTFO6118R"
}
params_bal = {
    "CANO": cano,
    "ACNT_PRDT_CD": prdt_cd,
    "MGNA_DVSN": "01",
    "EXCC_STAT_CD": "1",
    "CTX_AREA_FK200": "",
    "CTX_AREA_NK200": ""
}
res_bal = requests.get(balance_url, headers=headers, params=params_bal)
print("\n=== Balance & Positions ===")
if res_bal.status_code == 200:
    bal_data = res_bal.json()
    print("rt_cd:", bal_data.get("rt_cd"))
    print("msg:", bal_data.get("msg1"))
    print("Positions (output1):", bal_data.get("output1"))
    print("Account (output2):", bal_data.get("output2"))
else:
    print("Failed to query balance:", res_bal.text)

# 2. Query Executions (inquire-ccld)
ccld_url = f"{base_url}/uapi/domestic-futureoption/v1/trading/inquire-ccld"
headers_ccld = {
    "Content-Type": "application/json",
    "authorization": f"Bearer {access_token}",
    "appkey": app_key,
    "appsecret": app_secret,
    "tr_id": "VTTC0807R"
}
params_ccld = {
    "CANO": cano,
    "ACNT_PRDT_CD": prdt_cd,
    "ORD_DT": "20260615",
    "ORD_GUBUN": "00", # 00: 전체, 01: 체결, 02: 미체결
    "QUERY_INDEX": "",
    "CTX_AREA_FK200": "",
    "CTX_AREA_NK200": ""
}
res_ccld = requests.get(ccld_url, headers=headers_ccld, params=params_ccld)
print("\n=== Executions Today ===")
if res_ccld.status_code == 200:
    ccld_data = res_ccld.json()
    print("rt_cd:", ccld_data.get("rt_cd"))
    print("msg:", ccld_data.get("msg1"))
    
    output = ccld_data.get("output", [])
    print(f"Total executions found: {len(output)}")
    for item in output:
         print(f"Time: {item.get('ord_tmn') or item.get('ord_dt')} | Order No: {item.get('odno')} | Code: {item.get('shtn_pdno')} | Side: {item.get('sll_buy_dvsn_name')} | Qty: {item.get('ord_qty')} | Exec Qty: {item.get('tot_ccld_qty')} | Exec Price: {item.get('avg_prc') or item.get('ccld_prc')} | Status: {item.get('rmnd_qty')}")
else:
    print("Failed to query executions:", res_ccld.text)
