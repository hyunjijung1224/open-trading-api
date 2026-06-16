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
logger = logging.getLogger("BuyOneContract")

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from future.engines.execution_engine import ExecutionEngine
from config import config

async def main():
    logger.info("모의투자 테스트용 1계약 매수 실행")
    engine = ExecutionEngine()
    
    target_code = "A05607"
    current_price = await engine.fetch_futures_price_rest(target_code)
    if not current_price:
        logger.error("현재가 조회 실패")
        return
        
    logger.info(f"현재가: {current_price:.2f} Pt")
    
    # 즉시 체결을 유도하기 위해 현재가보다 0.10 Pt 높게 지정가 매수
    buy_price = current_price + 0.10
    qty = 1
    
    logger.info(f"매수 주문 전송: 가격={buy_price:.2f} Pt, 수량={qty}계약")
    order_res = await engine.execute_order(
        code=target_code,
        direction="BUY",
        qty=qty,
        price=buy_price,
        stop_loss=buy_price * 0.98,
        take_profit=buy_price * 1.02
    )
    
    if order_res.get("success"):
        logger.info(f"매수 성공! 주문번호: {order_res.get('order_id')}")
    else:
        logger.error(f"매수 실패: {order_res.get('error')}")

if __name__ == "__main__":
    asyncio.run(main())
