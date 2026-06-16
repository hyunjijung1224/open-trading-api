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
    print("Fetching real token...")
    token = get_real_token()
    url = "https://openapi.koreainvestment.com:9443/uapi/domestic-stock/v1/quotations/inquire-investor-time-by-market"
    
    headers = {
        "Content-Type": "application/json",
        "authorization": f"Bearer {token}",
        "appkey": config.KIS_REAL_APP_KEY,
        "appsecret": config.KIS_REAL_APP_SECRET,
        "tr_id": "FHPTJ04030000"
    }

    test_combinations = [
        {"ISCD": "K2O", "ISCD_2": "0001", "desc": "K2O / 0001 (KOSPI200 Option Call?)"},
        {"ISCD": "K2O", "ISCD_2": "0002", "desc": "K2O / 0002 (KOSPI200 Option Put?)"},
        {"ISCD": "K2O", "ISCD_2": "0000", "desc": "K2O / 0000 (KOSPI200 Option Total?)"},
        {"ISCD": "OPT", "ISCD_2": "0001", "desc": "OPT / 0001"},
        {"ISCD": "OPT", "ISCD_2": "0002", "desc": "OPT / 0002"},
        {"ISCD": "OPT", "ISCD_2": "0000", "desc": "OPT / 0000"},
        {"ISCD": "K2I", "ISCD_2": "O001", "desc": "K2I / O001"},
        {"ISCD": "K2I", "ISCD_2": "O002", "desc": "K2I / O002"},
    ]

    for comb in test_combinations:
        params = {
            "FID_INPUT_ISCD": comb["ISCD"],
            "FID_INPUT_ISCD_2": comb["ISCD_2"]
        }
        print(f"\n--- Testing: {comb['desc']} ---")
        res = requests.get(url, headers=headers, params=params)
        if res.status_code == 200:
            data = res.json()
            rt_cd = data.get("rt_cd")
            msg1 = data.get("msg1")
            print(f"rt_cd: {rt_cd}, msg1: {msg1}")
            outputs = data.get("output", [])
            if outputs:
                latest = outputs[0]
                frgn_vol = latest.get("frgn_ntby_qty", latest.get("frgn_ntby_vol", "N/A"))
                frgn_money = latest.get("frgn_ntby_tr_pbmn", "N/A")
                print(f"Output count: {len(outputs)}")
                print(f"Foreign Net Qty: {frgn_vol}, Net Money: {frgn_money}")
                # Print non-zero fields
                non_zero = {k: v for k, v in latest.items() if v not in ["0", "0.0", 0, 0.0]}
                print(f"Non-zero fields: {non_zero}")
            else:
                print("Output list is empty.")
        else:
            print(f"Error: {res.text}")
        
        time.sleep(0.5)

if __name__ == "__main__":
    test_option_params()
