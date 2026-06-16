"""
KIS 해외선물옵션 분봉조회 API 테스트
EUREX 연계 야간 코스피200 선물 분봉 확인용

TR: HHDFC55020400
URL: /uapi/overseas-futureoption/v1/quotations/inquire-time-futurechartprice
exch_cd: EUREX
srs_cd: 코스피200 야간선물 종목코드 (ex: BONU25, KQXU25 ...)
"""
import os
import sys
import requests
import json
from dotenv import load_dotenv

sys.path.insert(0, r"E:\0-aiTrading\open-trading-api")
from config import config
load_dotenv()

def fetch_token():
    if config.KIS_REAL_APP_KEY and config.KIS_REAL_APP_SECRET:
        base_url = "https://openapi.koreainvestment.com:9443"
        appkey = config.KIS_REAL_APP_KEY
        appsecret = config.KIS_REAL_APP_SECRET
        print("Using REAL server")
    else:
        base_url = config.KIS_BASE_URL
        appkey = config.KIS_APP_KEY
        appsecret = config.KIS_APP_SECRET
        print("Using PAPER server")

    url = f"{base_url}/oauth2/tokenP"
    res = requests.post(url, json={"grant_type": "client_credentials", "appkey": appkey, "appsecret": appsecret})
    if res.status_code == 200:
        return res.json()["access_token"], base_url, appkey, appsecret
    else:
        print(f"Token Error: {res.status_code} - {res.text}")
        return None, None, None, None

def test_eurex(srs_cd: str, token: str, base_url: str, appkey: str, appsecret: str):
    """EUREX 해외선물 분봉 조회 테스트"""
    url = f"{base_url}/uapi/overseas-futureoption/v1/quotations/inquire-time-futurechartprice"
    headers = {
        "Content-Type": "application/json",
        "authorization": f"Bearer {token}",
        "appkey": appkey,
        "appsecret": appsecret,
        "tr_id": "HHDFC55020400"
    }
    params = {
        "SRS_CD": srs_cd,
        "EXCH_CD": "EUREX",
        "START_DATE_TIME": "",
        "CLOSE_DATE_TIME": "20260610",
        "QRY_TP": "Q",
        "QRY_CNT": "30",
        "QRY_GAP": "1",   # 1분봉
        "INDEX_KEY": ""
    }

    print(f"\n--- Testing srs_cd={srs_cd} ---")
    res = requests.get(url, headers=headers, params=params)
    print(f"HTTP {res.status_code}")
    data = res.json()
    if data.get("rt_cd") == "0":
        output2 = data.get("output2", {})
        print(f"output2 keys: {list(output2.keys()) if isinstance(output2, dict) else 'list'}")
        print(json.dumps(data.get("output1", {}), ensure_ascii=False, indent=2))
        if isinstance(output2, list):
            print(f"Candle count: {len(output2)}")
            for r in output2[:5]:
                print(r)
        else:
            print(f"output2: {output2}")
    else:
        print(f"Error: {data.get('msg1')} (code: {data.get('msg_cd')})")

def main():
    token, base_url, appkey, appsecret = fetch_token()
    if not token:
        return

    # EUREX 연계 코스피200 야간선물 종목코드 후보들 시도
    # KOSPI200 야간선물 = KQX (KRX EUREX), format: KQXU25 (2025년 9월물)
    candidates = [
        "KQXM26",  # 코스피200 야간선물 2026년 6월물
        "KQXU25",  # 코스피200 야간선물 2025년 9월물
        "KQXZ25",  # 코스피200 야간선물 2025년 12월물
        "BONU25",  # Bund 선물 (테스트용)
    ]

    for srs_cd in candidates:
        test_eurex(srs_cd, token, base_url, appkey, appsecret)
        import time; time.sleep(1.5)

if __name__ == "__main__":
    main()
