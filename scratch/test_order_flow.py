import asyncio
import logging
import sys
import os

# Windows 콘솔 인코딩 대응
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("TestOrderFlow")

# 프로젝트 루트 경로 추가
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from future.engines.execution_engine import ExecutionEngine
from config import config

async def main():
    logger.info("TEST: KIS 선물 주문 / 청산 테스트 시작")
    logger.info(f"계좌번호: {config.KIS_ACCOUNT_NO} (모의투자: {config.KIS_IS_PAPER})")
    
    engine = ExecutionEngine()
    
    # 1. 현재가 조회
    target_code = "A05607"  # 2026-06-12 기준 활성 단축코드
    logger.info(f"1. 종목 {target_code} 현재가 조회 시도")
    current_price = await engine.fetch_futures_price_rest(target_code)
    if not current_price:
        logger.error("현재가 조회 실패")
        return
        
    logger.info(f"현재가: {current_price:.2f} Pt")
    
    # 즉시 체결을 유도하기 위해 현재가보다 0.10 Pt (미니선물 5틱) 높게 지정가 매수 주문
    buy_price = current_price + 0.10
    qty = 1
    
    logger.info(f"2. 매수 주문 요청: 가격={buy_price:.2f} Pt, 수량={qty}계약")
    # execute_order parameter: code, direction, qty, price, stop_loss, take_profit
    order_res = await engine.execute_order(
        code=target_code,
        direction="BUY",
        qty=qty,
        price=buy_price,
        stop_loss=buy_price * 0.98,
        take_profit=buy_price * 1.02
    )
    
    if not order_res.get("success"):
        logger.error(f"매수 주문 실패: {order_res.get('error')}")
        return
        
    odno = order_res.get("order_id")
    logger.info(f"매수 주문 접수 완료. 주문번호: {odno}")
    
    # 체결 대기 및 잔고 확인
    logger.info("3. 체결 대기 및 잔고 확인 (5초 대기)...")
    await asyncio.sleep(5)
    
    positions = await engine.fetch_active_positions()
    logger.info(f"현재 보유 잔고 수: {len(positions)}")
    
    target_pos = None
    for pos in positions:
        # 단축코드가 리턴되므로 비교
        if pos["futures_code"] in [target_code, "105V07"]:
            target_pos = pos
            break
            
    if not target_pos:
        logger.warning("잔고에 해당 포지션이 아직 잡히지 않았습니다. 5초 더 대기합니다.")
        await asyncio.sleep(5)
        positions = await engine.fetch_active_positions()
        for pos in positions:
            if pos["futures_code"] in [target_code, "105V07"]:
                target_pos = pos
                break
                
    if not target_pos:
        logger.error("체결 지연 또는 미체결 상태입니다. (모의투자 호가 대기)")
        logger.info("가상 포지션 데이터로 청산 주문 (매도) 테스트를 진행합니다.")
        target_pos = {
            "futures_code": target_code,
            "side": "LONG",  # LONG 청산 = 매도
            "quantity": qty
        }
        
    logger.info("4. 청산 주문 (시장가 매도) 요청")
    close_success = await engine.market_close_position(target_pos)
    if close_success:
        logger.info("청산 주문 (시장가 매도) 전송 성공")
    else:
        logger.error("청산 주문 전송 실패")

if __name__ == "__main__":
    asyncio.run(main())
