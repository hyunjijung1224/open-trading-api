import os
import sys
import requests
import json
import logging
import time
from datetime import datetime
from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config import config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("TestKrxNight")

load_dotenv()

def get_cached_token() -> str:
    config_dir = "KIS/config"
    files = [f for f in os.listdir(config_dir) if f.startswith("KIS")]
    files.sort(reverse=True)
    for filename in files:
        filepath = os.path.join(config_dir, filename)
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                token = None
                valid_date_str = None
                for line in f:
                    if line.startswith("token:"):
                        token = line.split("token:")[1].strip()
                    elif line.startswith("valid-date:"):
                        valid_date_str = line.split("valid-date:")[1].strip()
                if token and valid_date_str:
                    valid_date = datetime.strptime(valid_date_str, "%Y-%m-%d %H:%M:%S")
                    if valid_date > datetime.now():
                        return token
        except Exception as e:
            logger.error(f"Error reading {filename}: {e}")
    return None

def test_query(token, base_url, appkey, appsecret, symbol, date_str):
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
        "FID_INPUT_ISCD": symbol,
        "FID_HOUR_CLS_CODE": "60", # 1-minute candle
        "FID_PW_DATA_INCU_YN": "Y",
        "FID_FAKE_TICK_INCU_YN": "N",
        "FID_INPUT_DATE_1": date_str,
        "FID_INPUT_HOUR_1": "235900"
    }
    
    logger.info(f"Querying {symbol} on date={date_str} at 235900")
    res = requests.get(url, headers=headers, params=params)
    
    try:
        data = res.json()
        output2 = data.get("output2", [])
        logger.info(f"[{symbol} | {date_str}] Returned {len(output2)} candles")
        
        # Check if there are night candles
        night_candles = []
        day_candles = []
        for row in output2:
            time_val = int(row.get("stck_cntg_hour", "0"))
            is_night = (time_val >= 180000) or (time_val <= 60000)
            if is_night:
                night_candles.append(row)
            else:
                day_candles.append(row)
                
        logger.info(f"[{symbol} | {date_str}] Night: {len(night_candles)}, Day: {len(day_candles)}")
        
        if night_candles:
            logger.info(f"[{symbol} | {date_str}] First 5 night candles:")
            for i, row in enumerate(night_candles[:5]):
                logger.info(f"  [{i}] Date: {row.get('stck_bsop_date')}, Time: {row.get('stck_cntg_hour')}, Close: {row.get('futs_prpr')}")
        if day_candles:
            logger.info(f"[{symbol} | {date_str}] First 5 day candles:")
            for i, row in enumerate(day_candles[:5]):
                logger.info(f"  [{i}] Date: {row.get('stck_bsop_date')}, Time: {row.get('stck_cntg_hour')}, Close: {row.get('futs_prpr')}")
    except Exception as e:
        logger.error(f"Error: {e}")

def main():
    token = get_cached_token()
    if not token:
        logger.error("Could not find cached token.")
        return
        
    if config.KIS_REAL_APP_KEY and config.KIS_REAL_APP_SECRET:
        base_url = "https://openapi.koreainvestment.com:9443"
        appkey = config.KIS_REAL_APP_KEY
        appsecret = config.KIS_REAL_APP_SECRET
    else:
        base_url = config.KIS_BASE_URL
        appkey = config.KIS_APP_KEY
        appsecret = config.KIS_APP_SECRET

    # Test Monday 20260615 (which will contain Friday night's trades)
    test_query(token, base_url, appkey, appsecret, "10100", "20260615")
    time.sleep(2.0)
    test_query(token, base_url, appkey, appsecret, "10500", "20260615")

if __name__ == "__main__":
    main()
