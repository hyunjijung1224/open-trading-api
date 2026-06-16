# -*- coding: utf-8 -*-
import os
import sys
import requests
import yaml
from datetime import datetime
from dotenv import load_dotenv

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

def main():
    token = get_cached_token()
    if not token:
        print("No cached token found. Running kis_auth.auth to generate it first.")
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "examples_user"))
        import kis_auth as ka
        ka._cfg["paper_app"] = config.KIS_APP_KEY
        ka._cfg["paper_sec"] = config.KIS_APP_SECRET
        ka._cfg["my_paper_future"] = config.KIS_ACCOUNT_NO.split("-")[0]
        ka._cfg["my_prod"] = config.KIS_ACCOUNT_NO.split("-")[1]
        ka.auth(svr="vps", product=config.KIS_ACCOUNT_NO.split("-")[1])
        token = get_cached_token()
        
    print(f"Token length: {len(token) if token else 0}")
    if not token:
        print("Failed to obtain token.")
        return
        
    base_url = config.KIS_BASE_URL
    
    url = f"{base_url}/uapi/domestic-futureoption/v1/quotations/inquire-time-fuopchartprice"
    headers = {
        "Content-Type": "application/json",
        "authorization": f"Bearer {token}",
        "appkey": config.KIS_APP_KEY,
        "appsecret": config.KIS_APP_SECRET,
        "tr_id": "FHKIF03020200"
    }
    
    # Query for standard KOSPI 200 futures (Sep 2026): A01609 / continuous: 10100
    params = {
        "FID_COND_MRKT_DIV_CODE": "F",
        "FID_INPUT_ISCD": "10100",  # Continuous
        "FID_HOUR_CLS_CODE": "60",  # 1-minute
        "FID_PW_DATA_INCU_YN": "Y",
        "FID_FAKE_TICK_INCU_YN": "N",
        "FID_INPUT_DATE_1": "20260611",
        "FID_INPUT_HOUR_1": "180000"
    }
    
    print("=== Querying standard continuous code 10100 ===")
    res1 = requests.get(url, headers=headers, params=params)
    if res1.status_code == 200:
        data = res1.json()
        rt = data.get("rt_cd")
        output2 = data.get("output2", [])
        print(f"rt_cd: {rt} | candles count: {len(output2)}")
        for i, row in enumerate(output2[:15]):
            print(f"Row {i:02d}: Time={row.get('stck_cntg_hour')}, Close={row.get('futs_prpr')}, Vol={row.get('cntg_vol')}")
    else:
        print(f"Error: {res1.text}")
        
    print("\n=== Querying standard specific code A01609 ===")
    params["FID_INPUT_ISCD"] = "A01609"
    res2 = requests.get(url, headers=headers, params=params)
    if res2.status_code == 200:
        data = res2.json()
        rt = data.get("rt_cd")
        output2 = data.get("output2", [])
        print(f"rt_cd: {rt} | candles count: {len(output2)}")
        for i, row in enumerate(output2[:15]):
            print(f"Row {i:02d}: Time={row.get('stck_cntg_hour')}, Close={row.get('futs_prpr')}, Vol={row.get('cntg_vol')}")
    else:
        print(f"Error: {res2.text}")

if __name__ == "__main__":
    main()
