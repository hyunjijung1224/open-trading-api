import os
import sys
import requests
import json
import time

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

def test_option_params():
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

    test_combinations = [
        {"ISCD": "OPT", "ISCD_2": "O001", "desc": "OPT / O001 (Option Call)"},
        {"ISCD": "OPT", "ISCD_2": "O002", "desc": "OPT / O002 (Option Put)"},
        {"ISCD": "K2O", "ISCD_2": "O001", "desc": "K2O / O001 (KOSPI200 Option Call)"},
        {"ISCD": "K2O", "ISCD_2": "O002", "desc": "K2O / O002 (KOSPI200 Option Put)"},
        {"ISCD": "OPT", "ISCD_2": "F001", "desc": "OPT / F001"},
        {"ISCD": "K2O", "ISCD_2": "F001", "desc": "K2O / F001"},
        {"ISCD": "K2O", "ISCD_2": "O003", "desc": "K2O / O003"},
        {"ISCD": "OPT", "ISCD_2": "O003", "desc": "OPT / O003"},
    ]

    for comb in test_combinations:
        params = {
            "FID_INPUT_ISCD": comb["ISCD"],
            "FID_INPUT_ISCD_2": comb["ISCD_2"]
        }
        print(f"\n--- Testing: {comb['desc']} ---")
        res = requests.get(url, headers=headers, params=params)
        print(f"Status: {res.status_code}")
        if res.status_code == 200:
            data = res.json()
            rt_cd = data.get("rt_cd")
            msg1 = data.get("msg1")
            print(f"rt_cd: {rt_cd}, msg1: {msg1}")
            outputs = data.get("output", [])
            if outputs:
                print(f"Success! Output count: {len(outputs)}")
                print(f"First item: {outputs[0]}")
            else:
                print(f"Output list is empty. Full response keys: {list(data.keys())}")
        else:
            print(f"Error: {res.text}")
        
        # Rate limiting safety sleep
        time.sleep(1.0)

if __name__ == "__main__":
    test_option_params()
