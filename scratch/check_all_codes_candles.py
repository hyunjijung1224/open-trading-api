# -*- coding: utf-8 -*-
import os
import sys
import requests
import yaml
from datetime import datetime
from dotenv import load_dotenv
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config import config

load_dotenv()

def get_cached_token():
    today_str = datetime.today().strftime('%Y%m%d')
    config_root = os.path.join(os.path.dirname(__file__), "..", "KIS", "config")
    token_file = os.path.join(config_root, f"KIS{today_str}")
    if os.path.exists(token_file):
        try:
            with open(token_file, "r", encoding="utf-8") as f:
                data = yaml.load(f, Loader=yaml.FullLoader)
                if data and "token" in data:
                    return data["token"]
        except Exception as e:
            print(f"Error reading token file: {e}")
    return None

def query_code(token, base_url, code):
    url = f"{base_url}/uapi/domestic-futureoption/v1/quotations/inquire-time-fuopchartprice"
    headers = {
        "Content-Type": "application/json",
        "authorization": f"Bearer {token}",
        "appkey": config.KIS_APP_KEY,
        "appsecret": config.KIS_APP_SECRET,
        "tr_id": "FHKIF03020200"
    }
    params = {
        "FID_COND_MRKT_DIV_CODE": "F",
        "FID_INPUT_ISCD": code,
        "FID_HOUR_CLS_CODE": "60",  # 1-minute
        "FID_PW_DATA_INCU_YN": "Y",
        "FID_FAKE_TICK_INCU_YN": "N",
        "FID_INPUT_DATE_1": datetime.now().strftime("%Y%m%d"),
        "FID_INPUT_HOUR_1": "180000"
    }
    
    res = requests.get(url, headers=headers, params=params)
    print(f"\n--- Code: {code} ---")
    if res.status_code == 200:
        data = res.json()
        rt = data.get("rt_cd")
        output2 = data.get("output2", [])
        print(f"rt_cd: {rt} | candles count: {len(output2)}")
        for i, row in enumerate(output2[:10]):
            print(f"Row {i:02d}: Time={row.get('stck_cntg_hour')}, Close={row.get('futs_prpr')}, Vol={row.get('cntg_vol')}")
    else:
        print(f"Error: {res.text}")

def main():
    token = get_cached_token()
    if not token:
        print("Token missing.")
        return
    base_url = config.KIS_BASE_URL
    
    # Query multiple potential codes for September 2026 futures
    codes = ["101V09", "105V09", "10100", "10500"]
    for c in codes:
        query_code(token, base_url, c)
        time.sleep(1.2) # Avoid rate limits

if __name__ == "__main__":
    main()
