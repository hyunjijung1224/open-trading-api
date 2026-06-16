import os
import sys
import requests
import json
from datetime import datetime, timedelta

# Root directory import path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config import config

def get_real_token():
    url = "https://openapi.koreainvestment.com:9443/oauth2/tokenP"
    payload = {
        "grant_type": "client_credentials",
        "appkey": config.KIS_REAL_APP_KEY,
        "appsecret": config.KIS_REAL_APP_SECRET
    }
    res = requests.post(url, json=payload, headers={"content-type": "application/json"})
    res.raise_for_status()
    return res.json()["access_token"]

def test_trend():
    print("Fetching real server token...")
    try:
        token = get_real_token()
        print(f"Token obtained: {token[:10]}...")
    except Exception as e:
        print(f"Failed to fetch token: {e}")
        return

    url = "https://openapi.koreainvestment.com:9443/uapi/domestic-stock/v1/quotations/inquire-investor-time-by-market"
    
    headers = {
        "Content-Type": "application/json",
        "authorization": f"Bearer {token}",
        "appkey": config.KIS_REAL_APP_KEY,
        "appsecret": config.KIS_REAL_APP_SECRET,
        "tr_id": "FHPTJ04030000"
    }

    # Let's try different parameter combinations!
    # KOSPI 200 Mini Futures: FID_INPUT_ISCD="MKI", FID_INPUT_ISCD_2="F004"
    params_1 = {
        "FID_INPUT_ISCD": "MKI",
        "FID_INPUT_ISCD_2": "F004"
    }
    
    # Regular KOSPI 200 Futures: FID_INPUT_ISCD="K2I", FID_INPUT_ISCD_2="F001"
    params_2 = {
        "FID_INPUT_ISCD": "K2I",
        "FID_INPUT_ISCD_2": "F001"
    }
    
    print("\n--- Requesting Mini Futures Trend: MKI / F004 ---")
    res1 = requests.get(url, headers=headers, params=params_1)
    print(f"Status: {res1.status_code}")
    if res1.status_code == 200:
        data = res1.json()
        print(f"rt_cd: {data.get('rt_cd')}, msg1: {data.get('msg1')}")
        outputs = data.get("output", [])
        if outputs:
            print(f"First output item (Mini Futures): {outputs[0]}")
        else:
            print(f"Output list is empty or missing. Full response: {data}")
    else:
        print(f"Error response: {res1.text}")

    print("\n--- Requesting Regular Futures Trend: K2I / F001 ---")
    res2 = requests.get(url, headers=headers, params=params_2)
    if res2.status_code == 200:
        data = res2.json()
        print(f"rt_cd: {data.get('rt_cd')}, msg1: {data.get('msg1')}")
        outputs = data.get("output", [])
        if outputs:
            print(f"First output item (Regular Futures): {outputs[0]}")
        else:
            print(f"Full response: {data}")


if __name__ == "__main__":
    test_trend()
