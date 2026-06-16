import asyncio
import os
import sys
import logging
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from future.store.mariadb_store import MariaDBStore
from future.engines.execution_engine import ExecutionEngine
from future.engines.signal_engine import SignalEngine
from config import config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("TestOptionsLive")

async def main():
    logger.info("1. DB 테이블 초기화 및 마이그레이션 테스트...")
    db = MariaDBStore(
        host=os.getenv("MARIADB_HOST", "127.0.0.1"),
        port=int(os.getenv("MARIADB_PORT", 3306)),
        user=os.getenv("MARIADB_USER", "kis_user"),
        password=os.getenv("MARIADB_PASSWORD", "kis_password"),
        database=os.getenv("MARIADB_DATABASE", "kis_trading")
    )
    try:
        db.initialize_tables()
        logger.info("DB 테이블 초기화 및 ALTER TABLE 마이그레이션 성공!")
    except Exception as e:
        logger.error(f"DB 초기화 실패: {e}")
        return

    logger.info("\n2. KIS API 옵션 수급 조회 테스트...")
    engine = ExecutionEngine()
    try:
        option_flow = await engine.fetch_option_trend()
        logger.info(f"옵션 수급 조회 결과: {option_flow}")
    except Exception as e:
        logger.error(f"옵션 수급 조회 실패: {e}")
        return

    logger.info("\n3. 캔들 DB 적재 테스트 (옵션 수급 데이터 포함)...")
    mock_candle = {
        "futures_code": "105V07",
        "candle_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "open": 350.0,
        "high": 351.0,
        "low": 349.0,
        "close": 350.5,
        "volume": 120,
        "open_interest": 250000,
        "accum_amount": 100000000.0,
        "foreign_call_net": option_flow.get("foreign_call_net", 100),
        "foreign_put_net": option_flow.get("foreign_put_net", -50)
    }
    
    try:
        db.save_candles([mock_candle])
        logger.info("Mock 캔들(옵션 데이터 포함) DB 벌크 저장 성공!")
        
        # 저장 확인을 위해 DB에서 조회
        with db.conn.cursor() as cursor:
            cursor.execute("SELECT * FROM market_candles WHERE futures_code='105V07' ORDER BY candle_time DESC LIMIT 1;")
            row = cursor.fetchone()
            logger.info(f"DB 조회 결과: {row}")
    except Exception as e:
        logger.error(f"캔들 DB 저장/조회 실패: {e}")
        return

    logger.info("\n4. SignalEngine 옵션 필터링 검증...")
    signal_engine = SignalEngine()
    
    # 케이스 A: MACD 롱 트리거 + 풋 옵션 우위 (net_option_flow = -150) -> 진입 차단 확인
    indicators_long_blocked = {
        "macd": 0.5, "prev_macd": 0.1,
        "macd_signal": 0.2, "prev_macd_signal": 0.2,
        "option_call_net": 50,
        "option_put_net": 200  # 풋 우위 (상대적 하방 배팅)
    }
    sig_a = signal_engine.generate("105V07", "trending", "LONG_ONLY", 0.5, indicators_long_blocked)
    logger.info(f"케이스 A (롱 차단) 결과 방향: {sig_a['direction']} (사유: {sig_a['reasons'][-1]})")
    
    # 케이스 B: MACD 롱 트리거 + 콜 옵션 우위 (net_option_flow = +150) -> 진입 허용 확인
    indicators_long_allowed = {
        "macd": 0.5, "prev_macd": 0.1,
        "macd_signal": 0.2, "prev_macd_signal": 0.2,
        "option_call_net": 200,  # 콜 우위 (상대적 상방 배팅)
        "option_put_net": 50
    }
    sig_b = signal_engine.generate("105V07", "trending", "LONG_ONLY", 0.5, indicators_long_allowed)
    logger.info(f"케이스 B (롱 허용) 결과 방향: {sig_b['direction']} (사유: {sig_b['reasons'][-1]})")

    # 케이스 C: MACD 숏 트리거 + 콜 옵션 우위 (net_option_flow = +150) -> 진입 차단 확인
    indicators_short_blocked = {
        "macd": -0.5, "prev_macd": -0.1,
        "macd_signal": -0.2, "prev_macd_signal": -0.2,
        "option_call_net": 200,  # 콜 우위 (상대적 상방 배팅)
        "option_put_net": 50
    }
    sig_c = signal_engine.generate("105V07", "trending", "SHORT_ONLY", -0.5, indicators_short_blocked)
    logger.info(f"케이스 C (숏 차단) 결과 방향: {sig_c['direction']} (사유: {sig_c['reasons'][-1]})")
    
    # 케이스 D: MACD 숏 트리거 + 풋 옵션 우위 (net_option_flow = -150) -> 진입 허용 확인
    indicators_short_allowed = {
        "macd": -0.5, "prev_macd": -0.1,
        "macd_signal": -0.2, "prev_macd_signal": -0.2,
        "option_call_net": 50,
        "option_put_net": 200  # 풋 우위 (상대적 하방 배팅)
    }
    sig_d = signal_engine.generate("105V07", "trending", "SHORT_ONLY", -0.5, indicators_short_allowed)
    logger.info(f"케이스 D (숏 허용) 결과 방향: {sig_d['direction']} (사유: {sig_d['reasons'][-1]})")

if __name__ == "__main__":
    asyncio.run(main())
