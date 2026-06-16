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

def query_price(token, code):
    url = "https://openapi.koreainvestment.com:9443/uapi/domestic-futureoption/v1/quotations/inquire-price"
    headers = {
        "Content-Type": "application/json",
        "authorization": f"Bearer {token}",
        "appkey": config.KIS_REAL_APP_KEY,
        "appsecret": config.KIS_REAL_APP_SECRET,
        "tr_id": "FHMIF10000000"
    }
    params = {
        "FID_COND_MRKT_DIV_CODE": "F",
        "FID_INPUT_ISCD": code
    }
    res = requests.get(url, headers=headers, params=params)
    if res.status_code == 200:
        data = res.json()
        output1 = data.get("output1", {})
        price = output1.get("futs_prpr")
        volume = output1.get("acml_vol")
        time_str = output1.get("futs_cntg_hour")
        print(f"Code: {code} | Price: {price} | Vol: {volume} | Time: {time_str}")
    else:
        print(f"Error: {res.text}")

def main():
    token = fetch_real_token()
    if not token:
        return
    query_price(token, "A01609")
    time.sleep(1.2)
    query_price(token, "A05609")

if __name__ == "__main__":
    main()
