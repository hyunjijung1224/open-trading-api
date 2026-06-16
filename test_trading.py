# -*- coding: utf-8 -*-
"""
KIS 모의투자 트레이딩 모듈 테스트
  - 잔고 조회
  - 매수 주문 (삼성전자 1주 시장가)
  - 미체결 주문 조회
  - 주문 취소
  - 매도 주문

실행:  uv run python test_trading.py
"""

import logging
import os
import sys

# Windows 콘솔 인코딩 대응
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%H:%M:%S",
)

import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "examples_user"))
import kis_auth as ka

# ── 모의투자 인증 ──────────────────────────────
print("\n" + "=" * 60)
print("  🔐 모의투자 인증")
print("=" * 60)
ka.auth(svr="vps", product="01")
print("  인증 완료 ✅\n")

# ── trading 모듈 import ──────────────────────────────
from trading.account import AccountManager
from trading.order import OrderManager

acct = AccountManager()
om = OrderManager()

# ════════════════════════════════════════════════
# STEP 1. 잔고 조회
# ════════════════════════════════════════════════
print("[ STEP 1 ] 잔고 조회")
acct.print_balance()

# ════════════════════════════════════════════════
# STEP 2. 미체결 주문 조회
# ════════════════════════════════════════════════
time.sleep(0.5)
print("[ STEP 2 ] 미체결 주문 조회")
om.print_orders()

# ════════════════════════════════════════════════
# STEP 3. 삼성전자 1주 지정가 매수 (현재가 기준 낮게 설정 → 미체결 유도)
# ════════════════════════════════════════════════
time.sleep(0.5)
print(f"[ STEP 3 ] 삼성전자(005930) 지정가 매수 주문 (1주 / 240,000원 — 미체결 유도용)")
buy_result = om.buy(stock_code="005930", qty=1, price=240000)
print(f"  결과: {buy_result}\n")

# ════════════════════════════════════════════════
# STEP 4. 미체결 주문 재조회 (매수 주문 확인)
# ════════════════════════════════════════════════
if buy_result.success:
    time.sleep(0.5)
    print("[ STEP 4 ] 미체결 주문 재조회 (매수 주문 확인)")
    om.print_orders()

    # ════════════════════════════════════════════════
    # STEP 5. 주문 취소
    # ════════════════════════════════════════════════
    time.sleep(0.5)
    print("[ STEP 5 ] 방금 넣은 매수 주문 취소")
    cancel_result = om.cancel(
        order_no=buy_result.order_no,
        org_no=buy_result.org_no,
        stock_code="005930",
    )
    print(f"  결과: {cancel_result}\n")
else:
    print("  매수 주문 실패로 취소 테스트 건너뜀\n")

# ════════════════════════════════════════════════
# STEP 6. 시장가 매수 테스트 (주석 해제 후 장중에 실행)
# ════════════════════════════════════════════════
# print("[ STEP 6 ] 삼성전자(005930) 시장가 매수 (1주)")
# buy_market = om.buy(stock_code="005930", qty=1)   # price 생략 = 시장가
# print(f"  결과: {buy_market}\n")

# ════════════════════════════════════════════════
# STEP 7. 최종 잔고 확인
# ════════════════════════════════════════════════
time.sleep(1.0)
print("[ STEP 7 ] 최종 잔고 확인")
acct.print_balance()

print("✅ 트레이딩 모듈 테스트 완료!")
