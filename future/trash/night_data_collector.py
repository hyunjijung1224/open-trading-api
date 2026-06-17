import os
import sys
import time
import requests
import logging
from datetime import datetime, timedelta
from dotenv import load_dotenv

# 루트 경로를 sys.path에 추가하여 config 및 store 로드
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config import config
from future.store.mariadb_store import MariaDBStore

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("NightDataCollector")

load_dotenv()

def fetch_token() -> str:
    """한국투자증권 OAuth 토큰 발급 (실전투자 키가 있으면 실전투자 서버 사용)"""
    if config.KIS_REAL_APP_KEY and config.KIS_REAL_APP_SECRET:
        base_url = "https://openapi.koreainvestment.com:9443"
        appkey = config.KIS_REAL_APP_KEY
        appsecret = config.KIS_REAL_APP_SECRET
        logger.info("과거 데이터 조회를 위해 실전투자 API 키 및 서버(openapi.koreainvestment.com:9443)를 사용합니다.")
    else:
        base_url = config.KIS_BASE_URL
        appkey = config.KIS_APP_KEY
        appsecret = config.KIS_APP_SECRET
        logger.info("모의투자 API 키 및 서버를 사용하여 조회를 시도합니다. (과거 데이터 제한이 있을 수 있음)")

    url = f"{base_url}/oauth2/tokenP"
    
    payload = {
        "grant_type": "client_credentials",
        "appkey": appkey,
        "appsecret": appsecret
    }
    res = requests.post(url, json=payload)
    if res.status_code == 200:
        return res.json()["access_token"]
    else:
        logger.error(f"토큰 발급 실패: {res.status_code} - {res.text}")
        raise ValueError("토큰 획득 실패")

def collect_night_candles(days_limit: int = 30):
    """
    한국투자증권 Open API를 통해 최근 N일 간의 코스피200 미니선물 야간 데이터를 긁어서 MariaDB에 보완적재
    - symbol: '10500' (미니선물 연결)
    """
    logger.info(f"한국투자증권 Open API를 활용한 야간 분봉 수집 시작 (조회 한도: 최근 {days_limit}일)")
    
    # 1. DB 스토어 초기화
    try:
        db = MariaDBStore(
            host=os.getenv("MARIADB_HOST", "127.0.0.1"),
            port=int(os.getenv("MARIADB_PORT", 3306)),
            user=os.getenv("MARIADB_USER", "kis_user"),
            password=os.getenv("MARIADB_PASSWORD", "kis_password"),
            database=os.getenv("MARIADB_DATABASE", "kis_trading")
        )
    except Exception as e:
        logger.error(f"MariaDB 연결 오류: {e}")
        return

    # 2. KIS 토큰 및 접속 정보 설정
    try:
        if config.KIS_REAL_APP_KEY and config.KIS_REAL_APP_SECRET:
            base_url = "https://openapi.koreainvestment.com:9443"
            appkey = config.KIS_REAL_APP_KEY
            appsecret = config.KIS_REAL_APP_SECRET
        else:
            base_url = config.KIS_BASE_URL
            appkey = config.KIS_APP_KEY
            appsecret = config.KIS_APP_SECRET

        token = fetch_token()
        # 토큰 발급 API 호출 후 딜레이 확보 (초당 제한 방지)
        time.sleep(1.2)
    except Exception as e:
        logger.error(f"토큰 획득 중 예외 발생: {e}")
        return

    # 3. KIS API 설정
    url = f"{base_url}/uapi/domestic-futureoption/v1/quotations/inquire-time-fuopchartprice"
    symbol = "10500"
    
    headers = {
        "Content-Type": "application/json",
        "authorization": f"Bearer {token}",
        "appkey": appkey,
        "appsecret": appsecret,
        "tr_id": "FHKIF03020200"
    }

    # 수집 한계 날짜 계산
    limit_date_str = (datetime.now() - timedelta(days=days_limit)).strftime("%Y%m%d")
    
    # 페이징 루프 초기값
    current_date = datetime.now().strftime("%Y%m%d")
    current_hour = "" # 최초에는 최신 시간
    
    total_night_inserted = 0
    page_count = 1
    
    logger.info(f"수집 한계선 설정: {limit_date_str} 이후 데이터")

    while True:
        # 호출 제한 방지 (KIS API 초당 1회 제한 대응, 안전하게 2초 사용)
        time.sleep(2.0)
        logger.info(f"[페이지 {page_count}] KIS API 조회 시도 (기준날짜: {current_date}, 기준시간: {current_hour})")
        
        params = {
            "FID_COND_MRKT_DIV_CODE": "F",
            "FID_INPUT_ISCD": symbol,
            "FID_HOUR_CLS_CODE": "60", # 1분봉
            "FID_PW_DATA_INCU_YN": "Y",
            "FID_FAKE_TICK_INCU_YN": "N",
            "FID_INPUT_DATE_1": current_date,
            "FID_INPUT_HOUR_1": current_hour
        }
        
        max_retries = 5
        retry_count = 0
        success = False
        data = {}
        
        while retry_count < max_retries:
            try:
                res = requests.get(url, headers=headers, params=params, timeout=10)
                
                # HTTP 200 이나 500(초당 제한 오류 시 반환됨)의 경우 json 파싱 시도
                if res.status_code in [200, 500]:
                    try:
                        data = res.json()
                    except Exception:
                        data = {}
                    
                    # 초당 거래건수 초과 에러(EGW00201)인 경우 3초 대기 후 재시도
                    if data.get("msg_cd") == "EGW00201" or data.get("error_code") == "EGW00201":
                        logger.warning(f"초당 거래제한(EGW00201) 감지. 3초 대기 후 재시도 ({retry_count + 1}/{max_retries})...")
                        time.sleep(3.0)
                        retry_count += 1
                        continue
                
                if res.status_code == 200:
                    success = True
                    break
                else:
                    logger.error(f"API 호출 에러 (HTTP {res.status_code}): {res.text}")
                    break
            except Exception as e:
                logger.error(f"API 호출 중 예외 발생: {e}")
                break
                
        if not success:
            if data and data.get("rt_cd") != "0":
                logger.error(f"KIS API 내부 오류: {data.get('msg1')} (코드: {data.get('msg_cd')})")
            break
            
        output2 = data.get("output2", [])
        if not output2:
            logger.info("더 이상 수신할 과거 데이터가 존재하지 않습니다. 수집 루프 종료.")
            break
            
        night_candles = []
        
        for row in output2:
            date_val = row.get("stck_bsop_date", "")
            time_val = row.get("stck_cntg_hour", "") # HHMMSS
            
            if not date_val or not time_val:
                continue
                
            # 야간 세션 판별: 18:00:00 ~ 익일 06:00:00 (서머타임 및 일반 야간거래 포괄)
            # stck_cntg_hour 가 180000 이상이거나 060000 이하인 경우 야간 시세로 판정
            hour_int = int(time_val)
            is_night = (hour_int >= 180000) or (hour_int <= 60000)
            
            if not is_night:
                # 주간장 캔들은 수집 생략 (이미 대신증권 데이터로 채워져 있음)
                continue
                
            # 날짜와 시간 결합
            date_str = str(date_val)
            time_str = f"{hour_int:06d}"[:4] # HHMM
            
            try:
                dt_obj = datetime.strptime(f"{date_str} {time_str}", "%Y%m%d %H%M")
            except Exception:
                continue
                
            open_val = float(row.get("futs_oprc", 0))
            high_val = float(row.get("futs_hgpr", 0))
            low_val = float(row.get("futs_lwpr", 0))
            close_val = float(row.get("futs_prpr", 0))
            volume = int(row.get("cntg_vol", 0))
            
            night_candles.append({
                "futures_code": symbol,
                "candle_time": dt_obj,
                "open": open_val,
                "high": high_val,
                "low": low_val,
                "close": close_val,
                "volume": volume,
                "open_interest": 0, # KIS API 분봉은 미결제약정을 주지 않으므로 0 폴백
                "accum_amount": None
            })

        # DB 벌크 upsert 실행
        if night_candles:
            db.save_candles(night_candles)
            total_night_inserted += len(night_candles)
            logger.info(f"[페이지 {page_count}] 야간 분봉 {len(night_candles)}개 적재 완료 (누적: {total_night_inserted}개)")

        # 다음 페이지 계산용 포인터 업데이트 (가장 이른 시간 검색)
        last_row = output2[-1]
        next_date = last_row.get("stck_bsop_date", "")
        next_hour = last_row.get("stck_cntg_hour", "")
        
        # 중복 요청 및 개장 시간(08:45:00) 무한루프 방지를 위해 1분을 빼서 다음 포인터로 설정
        if next_date and next_hour:
            try:
                hour_str = f"{int(next_hour):06d}"
                dt_pointer = datetime.strptime(f"{next_date} {hour_str}", "%Y%m%d %H%M%S")
                dt_pointer = dt_pointer - timedelta(minutes=1)
                next_date = dt_pointer.strftime("%Y%m%d")
                next_hour = dt_pointer.strftime("%H%M%S")
            except Exception as e:
                logger.error(f"포인터 계산 중 예외 발생: {e}")

        # 만약 더 이상 과거 데이터로 포인터가 이동하지 않는다면 무한 루프 차단
        if next_date == current_date and next_hour == current_hour:
            logger.info("더 이상 과거 데이터로 포인터가 이동하지 않아 수집을 마감합니다.")
            break

        # 만약 한계선 날짜 이전으로 이동했으면 종료
        if next_date < limit_date_str:
            logger.info(f"설정된 한계 날짜({limit_date_str}) 이전 시점({next_date})에 도달하여 수집을 마감합니다.")
            break
            
        current_date = next_date
        current_hour = next_hour
        page_count += 1

    db.close()
    logger.info(f"🎉 KIS API 기반 야간 분봉 수집 완료! 총 {total_night_inserted}개의 야간 분봉 캔들이 MariaDB에 안전하게 병합되었습니다.")

if __name__ == "__main__":
    # 최근 30일치 야간 분봉 수집 실행 (조절 가능)
    collect_night_candles(days_limit=30)
