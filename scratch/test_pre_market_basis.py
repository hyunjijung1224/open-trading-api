import os
import sys
import logging
import asyncio
from datetime import datetime, timedelta

# Windows 콘솔 인코딩 대응 (cp949 에러 방지)
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

# 루트 디렉토리를 path에 추가
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config import config
from future.store.mariadb_store import MariaDBStore
from future.engines import ExecutionEngine
from future.supervisor import TradingSupervisor

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("TestPreMarketBasis")

class MockSheetsStore:
    def update_active_positions(self, positions):
        pass
    def update_bot_health(self, metrics):
        pass

async def test_pre_market_basis():
    logger.info("=" * 60)
    logger.info("장전 임시 베이시스 산출 통합 테스트 시작 (인코딩 및 월물 보정)")
    logger.info("=" * 60)

    # 1. MariaDB 초기화
    logger.info("1. MariaDB 연결 및 테이블 초기화 검증...")
    db = MariaDBStore(
        host=os.getenv("MARIADB_HOST", "127.0.0.1"),
        port=int(os.getenv("MARIADB_PORT", 3306)),
        user=os.getenv("MARIADB_USER", "kis_user"),
        password=os.getenv("MARIADB_PASSWORD", "kis_password"),
        database=os.getenv("MARIADB_DATABASE", "kis_trading")
    )
    logger.info("MariaDB 초기화 완료.")

    # 2. 수퍼바이저와 ExecutionEngine 초기화
    logger.info("2. Supervisor & Execution Engine 초기화...")
    mock_sheets = MockSheetsStore()
    supervisor = TradingSupervisor(db_store=db, sheets_store=mock_sheets)
    engine = ExecutionEngine()
    
    # 만기 3영업일 전 자동 계산된 현재 활성 월물 선물코드 사용 (예: 105V07)
    futures_std_code = supervisor.current_code
    futs_short_code = supervisor._to_kis_code(futures_std_code)
    
    logger.info(f"3. 선물 {futs_short_code} ({futures_std_code}) 장전 시세 조회 시도...")
    futs_data = await engine.fetch_futures_pre_market_price_rest(futs_short_code)
    if not futs_data:
        logger.error("❌ 선물 시세 조회 실패")
        db.close()
        return

    futs_expected = futs_data["futs_prpr"]
    futs_prev_close = futs_data["prev_close"]
    futs_return = (futs_expected / futs_prev_close) - 1.0
    logger.info(f"✅ 선물 시세 조회 성공: 예상체결가={futs_expected:.2f}, 전일종가={futs_prev_close:.2f}, 예상수익률={futs_return*100:+.2f}%")

    # 4. 대형주 5개 종목 조회 테스트
    logger.info("4. 대형주 5개 종목 예상체결가 조회 및 가중수익률 연산...")
    stock_basket = {
        "005930": 0.50,  # 삼성전자
        "000660": 0.20,  # SK하이닉스
        "373220": 0.10,  # LG에너지솔루션
        "207940": 0.10,  # Samsung Biologics
        "005380": 0.10   # 현대차
    }

    weighted_spot_return = 0.0
    fetched_count = 0

    for ticker, weight in stock_basket.items():
        await asyncio.sleep(0.6)  # Rate Limit 방어 (2 TPS 한도 고려)
        logger.info(f"   - 종목 {ticker} 조회 중...")
        stock_data = await engine.fetch_stock_price_rest(ticker)
        if stock_data:
            prev_close = stock_data["stck_sdpr"]
            expected_price = stock_data["antg_prc"]
            if not expected_price or expected_price == 0:
                expected_price = stock_data["stck_prpr"] or prev_close

            if prev_close > 0:
                ret = (expected_price / prev_close) - 1.0
                weighted_spot_return += ret * weight
                fetched_count += 1
                logger.info(f"     ✅ {ticker}: 예상체결가={expected_price:.0f}, 전일종가={prev_close:.0f}, 수익률={ret*100:+.2f}% (가중치={weight*100}%)")
        else:
            logger.warning(f"     ❌ {ticker} 조회 실패")

    if fetched_count == 0:
        logger.error("❌ 대형주 종목 조회를 전부 실패했습니다.")
        db.close()
        return

    # 5. 임시 베이시스 계산
    temp_spot_index = futs_prev_close * (1 + weighted_spot_return)
    temp_basis = futs_expected - temp_spot_index
    logger.info(f"5. 덧셈 베이시스 연산 완료:")
    logger.info(f"   - 대형주 가중 예상 수익률: {weighted_spot_return*100:+.2f}%")
    logger.info(f"   - 임시 현물 지수 대용치: {temp_spot_index:.2f} Pt")
    logger.info(f"   - 임시 베이시스: {temp_basis:+.2f} Pt")

    # 6. DB 적재 테스트
    logger.info("6. DB 저장 시도...")
    db_data = {
        "futures_code": futures_std_code,
        "expected_futures_price": futs_expected,
        "expected_spot_return": weighted_spot_return,
        "expected_futures_return": futs_return,
        "temporary_basis": temp_basis
    }
    db.save_pre_market_basis(db_data)
    logger.info("✅ DB 저장 완료.")

    # 7. DB 로드 및 요약 요출 테스트
    logger.info("7. DB 조회 및 요약 생성 검증...")
    today_start = datetime.now() - timedelta(hours=1)
    basis_records = db.get_pre_market_basis_since(today_start)
    logger.info(f"   - 오늘 조회된 레코드 수: {len(basis_records)}개")
    
    if basis_records:
        initial_basis = basis_records[0]["temporary_basis"]
        latest_basis = basis_records[-1]["temporary_basis"]
        min_basis = min(d["temporary_basis"] for d in basis_records)
        max_basis = max(d["temporary_basis"] for d in basis_records)
        change = latest_basis - initial_basis
        
        trend_str = "콘탱고 강화(선물 강세)" if change > 0 else "백워데이션 강화/베이시스 하락(선물 약세)"
        
        summary = (
            f"📊 장전 임시 베이시스 동향 (08:00~현재):\n"
            f"- 현재 임시 베이시스: {latest_basis:+.2f} Pt (전체 범위: {min_basis:+.2f} ~ {max_basis:+.2f})\n"
            f"- 08:00 대비 변동: {change:+.2f} Pt ({trend_str})"
        )
        print("\n" + "="*50)
        print(summary)
        print("="*50 + "\n")

    db.close()
    logger.info("🎉 테스트 완료!")

if __name__ == "__main__":
    asyncio.run(test_pre_market_basis())
