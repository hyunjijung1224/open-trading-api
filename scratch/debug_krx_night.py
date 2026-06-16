"""
KRX 야간선물 분봉 조회 테스트
- 2025년 6월 9일부터 EUREX 연계 야간거래 → KRX 자체 야간거래로 전환
- KRX 야간선물 종목코드 형식: 101W9000 (미니코스피200 야간), 106F9000 등
- TR: FHKIF03020200 (기존 선물 분봉 TR 그대로 사용)
- 야간장 시간: 18:00 ~ 익일 06:00

종목코드 형식 추정:
  10500 (미니코스피200 주간 연결) → 야간은 별도 종목코드
  예시: 101W9000, 106F9000
"""
import os, sys, time, requests, json
from dotenv import load_dotenv

sys.path.insert(0, r"E:\0-aiTrading\open-trading-api")
from config import config
load_dotenv()

def fetch_token():
    if config.KIS_REAL_APP_KEY and config.KIS_REAL_APP_SECRET:
        base_url = "https://openapi.koreainvestment.com:9443"
        appkey, appsecret = config.KIS_REAL_APP_KEY, config.KIS_REAL_APP_SECRET
    else:
        base_url = config.KIS_BASE_URL
        appkey, appsecret = config.KIS_APP_KEY, config.KIS_APP_SECRET

    res = requests.post(f"{base_url}/oauth2/tokenP", json={
        "grant_type": "client_credentials", "appkey": appkey, "appsecret": appsecret
    })
    if res.status_code == 200:
        return res.json()["access_token"], base_url, appkey, appsecret
    print(f"Token Error: {res.status_code} - {res.text}")
    return None, None, None, None

def query_chart(code, date, hour, token, base_url, appkey, appsecret):
    """FHKIF03020200 분봉 조회"""
    url = f"{base_url}/uapi/domestic-futureoption/v1/quotations/inquire-time-fuopchartprice"
    headers = {
        "Content-Type": "application/json",
        "authorization": f"Bearer {token}",
        "appkey": appkey,
        "appsecret": appsecret,
        "tr_id": "FHKIF03020200"
    }
    params = {
        "FID_COND_MRKT_DIV_CODE": "F",
        "FID_INPUT_ISCD": code,
        "FID_HOUR_CLS_CODE": "60",
        "FID_PW_DATA_INCU_YN": "Y",
        "FID_FAKE_TICK_INCU_YN": "N",
        "FID_INPUT_DATE_1": date,
        "FID_INPUT_HOUR_1": hour
    }
    res = requests.get(url, headers=headers, params=params)
    data = res.json()
    rt = data.get("rt_cd")
    output2 = data.get("output2", [])
    if rt == "0" and output2:
        # 야간 시간대 필터 (18:00 이후 or 06:00 이전)
        night = [r for r in output2 if int(r.get("stck_cntg_hour","0")) >= 180000 or int(r.get("stck_cntg_hour","999999")) <= 60000]
        print(f"  code={code:12s} date={date} hour={hour}: total={len(output2)}, night={len(night)}")
        if night:
            print(f"    First night bar: {night[0]}")
        return output2
    else:
        msg = data.get("msg1","") or data.get("message","")
        print(f"  code={code:12s} date={date} hour={hour}: ERROR rt={rt} - {msg[:60]}")
        return []

def main():
    token, base_url, appkey, appsecret = fetch_token()
    if not token:
        return
    print(f"Token OK. Server: {base_url}\n")
    time.sleep(1.0)

    # ── 테스트할 종목코드 목록 ──────────────────────────────
    # KRX 야간선물 종목코드 후보:
    # - 10500: 미니코스피200 연결 (주간)
    # - 10100: 코스피200 연결 (주간)
    # - KRX 야간선물은 별도 월물코드: 101W9000, 106F9000 형식 추정
    # - 실시간-064 예시: 101W9000
    candidates = [
        # KRX 야간선물 종목코드 후보들
        ("101W9000", "20260610", "200000"),   # 코스피200 야간선물 20:00 조회
        ("106F9000", "20260610", "200000"),   # 미니코스피200 야간선물 20:00 조회
        ("10100",    "20260610", "200000"),   # 코스피200 연결 20:00 조회 (주간TR로)
        ("10500",    "20260610", "200000"),   # 미니코스피200 연결 20:00 조회 (주간TR로)
        # 오늘 날짜 야간 첫 진입점으로도 시도
        ("101W9000", "20260609", "050000"),   # 전날 05:00 기준 과거 야간 조회
    ]

    for code, date, hour in candidates:
        query_chart(code, date, hour, token, base_url, appkey, appsecret)
        time.sleep(1.5)

if __name__ == "__main__":
    main()
