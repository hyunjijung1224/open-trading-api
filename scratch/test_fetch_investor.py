import asyncio
import os
import sys
import logging

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from future.engines.execution_engine import ExecutionEngine
from config import config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("TestInvestor")

async def main():
    engine = ExecutionEngine()
    # KIS API 토큰 발급 및 수급 데이터 원본 조회
    await engine._ensure_real_token()
    
    url = "https://openapi.koreainvestment.com:9443/uapi/domestic-stock/v1/quotations/inquire-investor-time-by-market"
    headers = {
        "Content-Type": "application/json",
        "authorization": f"Bearer {engine.real_access_token}",
        "appkey": config.KIS_REAL_APP_KEY,
        "appsecret": config.KIS_REAL_APP_SECRET,
        "tr_id": "FHPTJ04030000"
    }
    
    # 미니선물(105) 기준 파라미터
    params = {
        "FID_INPUT_ISCD": "MKI",
        "FID_INPUT_ISCD_2": "F004"
    }
    
    res = engine._request_with_retry("GET", url, headers=headers, params=params)
    if res.status_code == 200:
        data = res.json()
        outputs = data.get("output", [])
        logger.info(f"조회 성공! 데이터 개수: {len(outputs)}")
        if outputs:
            logger.info("첫 번째 데이터 샘플:")
            logger.info(outputs[0])
            logger.info("두 번째 데이터 샘플:")
            logger.info(outputs[1] if len(outputs) > 1 else "없음")
    else:
        logger.error(f"조회 실패: {res.status_code} {res.text}")

if __name__ == "__main__":
    asyncio.run(main())
