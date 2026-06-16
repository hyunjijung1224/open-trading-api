# -*- coding: utf-8 -*-
"""
KIS 선물 잔고조회 API 응답 디버그 스크립트
실제 KIS API 응답 구조를 확인하여 파싱 로직을 수정한다.
"""
import os, sys, json, requests

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config import config

base_url = config.KIS_BASE_URL
app_key = config.KIS_APP_KEY
app_secret = config.KIS_APP_SECRET
account_no = config.KIS_ACCOUNT_NO

# 1. 토큰 발급
print("=== Step 1: 토큰 발급 ===")
token_url = f"{base_url}/oauth2/tokenP"
token_res = requests.post(token_url, json={
    "grant_type": "client_credentials",
    "appkey": app_key,
    "appsecret": app_secret
}, timeout=15)
token_data = token_res.json()
access_token = token_data["access_token"]
print(f"토큰 발급 성공: {access_token[:20]}...")

# 2. 선물 잔고 조회
print("\n=== Step 2: 선물 잔고 조회 (VTFO6118R) ===")
cano, prdt_cd = account_no.split("-")

headers = {
    "Content-Type": "application/json",
    "authorization": f"Bearer {access_token}",
    "appkey": app_key,
    "appsecret": app_secret,
    "tr_id": "VTFO6118R"
}
params = {
    "CANO": cano,
    "ACNT_PRDT_CD": prdt_cd,
    "MGNA_DVSN": "01",
    "EXCC_STAT_CD": "1",
    "CTX_AREA_FK200": "",
    "CTX_AREA_NK200": ""
}

url = f"{base_url}/uapi/domestic-futureoption/v1/trading/inquire-balance"
print(f"URL: {url}")
print(f"Headers tr_id: {headers['tr_id']}")
print(f"Params: {json.dumps(params, indent=2)}")

res = requests.get(url, headers=headers, params=params, timeout=15)
print(f"\nHTTP Status: {res.status_code}")

data = res.json()
print(f"rt_cd: {data.get('rt_cd')}")
print(f"msg_cd: {data.get('msg_cd')}")
print(f"msg1: {data.get('msg1')}")

# output1 상세 출력
output1 = data.get("output1", [])
print(f"\n=== output1 ({len(output1)}개 항목) ===")
for i, item in enumerate(output1):
    print(f"\n--- output1[{i}] ---")
    for k, v in item.items():
        if v and v != "0" and v != "0.00" and v != "0.0000000000000" and v != "":
            print(f"  {k}: {v}")

# output2 상세 출력
output2 = data.get("output2", [])
print(f"\n=== output2 ({len(output2) if isinstance(output2, list) else 'dict'}) ===")
if isinstance(output2, list):
    for i, item in enumerate(output2):
        print(f"\n--- output2[{i}] ---")
        for k, v in item.items():
            if v and v != "0" and v != "0.00" and v != "":
                print(f"  {k}: {v}")
elif isinstance(output2, dict):
    for k, v in output2.items():
        if v and v != "0" and v != "0.00" and v != "":
            print(f"  {k}: {v}")

# 전체 JSON 출력 (참고용)
print(f"\n=== FULL RAW JSON ===")
print(json.dumps(data, indent=2, ensure_ascii=False))
