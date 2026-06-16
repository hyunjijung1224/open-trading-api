import os
import sys
import json
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
ccnl_url = f"{base_url}/uapi/domestic-futureoption/v1/trading/inquire-ccnl"
tr_id_ccnl = "VTTC0807R" if is_paper else "TTTC0807R"

headers = {
    "Content-Type": "application/json",
    "authorization": f"Bearer {access_token}",
    "appkey": app_key,
    "appsecret": app_secret,
    "tr_id": tr_id_ccnl
}

params = {
    "CANO": cano,
    "ACNT_PRDT_CD": prdt_cd,
    "STRT_ORD_DT": "20260615",
    "END_ORD_DT": "20260615",
    "SLL_BUY_DVSN_CD": "00",
    "CCLD_NCCS_DVSN": "00", # 00: 전체, 01: 체결, 02: 미체결
    "SORT_SQN": "DS",
    "PDNO": "",
    "STRT_ODNO": "",
    "MKET_ID_CD": "",
    "CTX_AREA_FK200": "",
    "CTX_AREA_NK200": ""
}

res = requests.get(ccnl_url, headers=headers, params=params)
print("Status Code:", res.status_code)
if res.status_code == 200:
    data = res.json()
    print("rt_cd:", data.get("rt_cd"))
    print("msg:", data.get("msg1"))
    
    output = data.get("output", [])
    print(f"\n=== Execution History ({len(output)} items) ===")
    for item in output:
        # Print relevant fields of execution
        print(f"Time: {item.get('ord_tmn') or item.get('ord_dt')} | Order No: {item.get('odno')} | Code: {item.get('shtn_pdno')} | Name: {item.get('prdt_name')} | Side: {item.get('sll_buy_dvsn_name')} | Qty: {item.get('ord_qty')} | Exec Qty: {item.get('tot_ccld_qty')} | Exec Price: {item.get('avg_prc') or item.get('avg_price') or item.get('ccld_prc')} | Status: {item.get('rmnd_qty')}")
        # Print the whole dict if small
        # print(item)
else:
    print("Failed:", res.text)
