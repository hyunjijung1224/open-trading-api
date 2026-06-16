import os
import sys
import requests
import time
from dotenv import load_dotenv

# 루트 경로 절대경로 삽입하여 config 로드
sys.path.insert(0, r"E:\0-aiTrading\open-trading-api")
from config import config

load_dotenv()

def fetch_token():
    # 실전투자 키 사용
    if config.KIS_REAL_APP_KEY and config.KIS_REAL_APP_SECRET:
        base_url = "https://openapi.koreainvestment.com:9443"
        appkey = config.KIS_REAL_APP_KEY
        appsecret = config.KIS_REAL_APP_SECRET
        print("Using REAL key for token...")
    else:
        base_url = config.KIS_BASE_URL
        appkey = config.KIS_APP_KEY
        appsecret = config.KIS_APP_SECRET
        print("Using PAPER key for token...")

    url = f"{base_url}/oauth2/tokenP"
    payload = {
        "grant_type": "client_credentials",
        "appkey": appkey,
        "appsecret": appsecret
    }
    res = requests.post(url, json=payload)
    if res.status_code == 200:
        return res.json()["access_token"], base_url, appkey, appsecret
    else:
        print(f"Token Fetch Error Status: {res.status_code}, Body: {res.text}")
    return None, None, None, None

def debug_kis_futures():
    print("Starting KIS Futures Real Server debug...")
    token, base_url, appkey, appsecret = fetch_token()
    if not token:
        print("Failed to fetch KIS token.")
        return
    
    url = f"{base_url}/uapi/domestic-futureoption/v1/quotations/inquire-time-fuopchartprice"
    symbol = "10100"
    
    headers = {
        "Content-Type": "application/json",
        "authorization": f"Bearer {token}",
        "appkey": appkey,
        "appsecret": appsecret,
        "tr_id": "FHKIF03020200"
    }
    
    # 2026-06-10 19:00:00 (야간 세션 중)
    params = {
        "FID_COND_MRKT_DIV_CODE": "F",
        "FID_INPUT_ISCD": symbol,
        "FID_HOUR_CLS_CODE": "60",  # 1분봉
        "FID_PW_DATA_INCU_YN": "Y",
        "FID_FAKE_TICK_INCU_YN": "N",
        "FID_INPUT_DATE_1": "20260609",
        "FID_INPUT_HOUR_1": "090000"  # 09:00:00
    }
    
    print(f"Requesting KIS API for symbol {symbol} at 19:00...")
    res = requests.get(url, headers=headers, params=params)
    print(f"Response Status Code: {res.status_code}")
    
    data = res.json()
    if res.status_code != 200 or data.get("rt_cd") != "0":
        print(f"API Error: {data}")
        return
            
    output1 = data.get("output1", {})
    output2 = data.get("output2", [])
    
    print(f"\n--- Output1 (Summary) ---")
    for k, v in output1.items():
        print(f"  {k}: {v}")
        
    print(f"\n--- Output2 (Candle count: {len(output2)}) ---")
    for i, row in enumerate(output2[:30]):
        print(f"Row {i:02d}: Date={row.get('stck_bsop_date')}, Time={row.get('stck_cntg_hour')}, Close={row.get('futs_prpr')}, Vol={row.get('cntg_vol')}")

if __name__ == "__main__":
    debug_kis_futures()
