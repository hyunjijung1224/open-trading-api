# -*- coding: utf-8 -*-
import os
import sys
import requests
import json
import time
from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config import config

load_dotenv()

def fetch_real_token():
    url = "https://openapi.koreainvestment.com:9443/oauth2/tokenP"
    payload = {
        "grant_type": "client_credentials",
        "appkey": config.KIS_REAL_APP_KEY,
        "appsecret": config.KIS_REAL_APP_SECRET
    }
    res = requests.post(url, json=payload)
    if res.status_code == 200:
        return res.json()["access_token"]
    else:
        print(f"Failed to fetch real token: {res.text}")
        return None

def query_real_candles(token, code, date):
    url = "https://openapi.koreainvestment.com:9443/uapi/domestic-futureoption/v1/quotations/inquire-time-fuopchartprice"
    headers = {
        "Content-Type": "application/json",
        "authorization": f"Bearer {token}",
        "appkey": config.KIS_REAL_APP_KEY,
        "appsecret": config.KIS_REAL_APP_SECRET,
        "tr_id": "FHKIF03020200"
    }
    params = {
        "FID_COND_MRKT_DIV_CODE": "F",
        "FID_INPUT_ISCD": code,
        "FID_HOUR_CLS_CODE": "60",  # 1-minute
        "FID_PW_DATA_INCU_YN": "Y",
        "FID_FAKE_TICK_INCU_YN": "N",
        "FID_INPUT_DATE_1": date,
        "FID_INPUT_HOUR_1": "183000"  # check around current time (18:20 - 18:30)
    }
    res = requests.get(url, headers=headers, params=params)
    print(f"\n=== Real Server | Code: {code} | Date: {date} ===")
    if res.status_code == 200:
        data = res.json()
        rt = data.get("rt_cd")
        output2 = data.get("output2", [])
        print(f"rt_cd: {rt} | candles count: {len(output2)}")
        for i, row in enumerate(output2[:15]):
            print(f"Row {i:02d}: Date={row.get('stck_bsop_date')}, Time={row.get('stck_cntg_hour')}, Close={row.get('futs_prpr')}, Vol={row.get('cntg_vol')}")
    else:
        print(f"Error: {res.text}")

def main():
    token = fetch_real_token()
    if not token:
        return
        
    # Query with tomorrow's date 20260612 (which represents the business day for tonight's session)
    codes = ["101V09", "105V09", "10100", "10500"]
    for c in codes:
        query_real_candles(token, c, "20260612")
        time.sleep(1.2)

if __name__ == "__main__":
    main()
