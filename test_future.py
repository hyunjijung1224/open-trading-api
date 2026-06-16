# -*- coding: utf-8 -*-
"""
KOSPI200 미니선물 모의투자 연동 테스트 스크립트
.env 에 등록된 KIS_ACCOUNT_NO(예: 60044330-03) 정보를 사용하여 모의투자를 테스트합니다.
"""

import os
import sys
import logging

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
logger = logging.getLogger("TestFuture")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "examples_user"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "examples_user", "domestic_futureoption"))

# 1. config 모듈을 통해 .env의 설정값 읽기
from config import config
import kis_auth as ka




# 2. .env의 KIS 환경변수들을 kis_auth의 _cfg 딕셔너리에 매핑
logger.info("🔧 .env 설정값을 기반으로 KIS API 세션을 구성합니다.")
ka._cfg["paper_app"] = config.KIS_APP_KEY
ka._cfg["paper_sec"] = config.KIS_APP_SECRET

# 계좌번호 설정 (.env에 정의된 KIS_ACCOUNT_NO를 자동으로 사용합니다)
cano = config.KIS_ACCOUNT_NO.split("-")[0]
prod = config.KIS_ACCOUNT_NO.split("-")[1]

ka._cfg["my_paper_future"] = cano
ka._cfg["my_prod"] = prod

logger.info(f"   설정 계좌: {cano}-{prod} (모의투자 파생)")

# 3. KIS 모의투자 인증 시도 (svr="vps", product="03")
print("\n" + "=" * 60)
print("  🔐 모의투자 선물옵션 인증")
print("=" * 60)

# 토큰 캐시가 만료되었거나 없을 경우 수동으로 받아 캐싱
if ka.read_token() is None:
    logger.info("🔑 캐시된 토큰이 없어 KIS 토큰 API를 호출합니다.")
    import requests
    import json
    from datetime import datetime, timedelta
    
    url = "https://openapivts.koreainvestment.com:29443/oauth2/tokenP"
    p = {
        "grant_type": "client_credentials",
        "appkey": config.KIS_APP_KEY,
        "appsecret": config.KIS_APP_SECRET
    }
    try:
        res = requests.post(url, json=p, headers={"Content-Type": "application/json"}, timeout=10)
        if res.status_code == 200:
            data = res.json()
            token = data["access_token"]
            expired = data["access_token_token_expired"]
            ka.save_token(token, expired)
            logger.info("✅ 신규 토큰 발급 및 로컬 캐싱 완료.")
        else:
            logger.error(f"❌ 토큰 발급 실패 (HTTP {res.status_code}): {res.text}")
    except Exception as e:
        logger.error(f"❌ 토큰 발급 중 예외 발생: {e}")

ka.auth(svr="vps", product=prod)
print("  인증 완료 ✅\n")

# API 호출 간격 확보 (EGW00201 초당 거래건수 초과 방지)
import time
time.sleep(1.5)

# 4. 국내 선물옵션 API 호출 함수 임포트
from domestic_futureoption_functions import inquire_price, order

# 코스피200 미니선물 최근월물 종목코드 설정 (KIS API는 A05609 같은 포맷을 사용합니다)
target_code = "A05609" 

# 5. 미니선물 현재가 조회
print(f"[ STEP 1 ] 미니선물({target_code}) 현재가 조회")
try:
    res_price, _, _ = inquire_price(fid_cond_mrkt_div_code="F", fid_input_iscd=target_code, env_dv="real")
    if not res_price.empty:
        current_price = float(res_price.iloc[0].get("futs_prpr", 0.0))
        print(f"  현재가: {current_price} Pt\n")
        
        # API 호출 간격 확보
        time.sleep(1.5)
        
        # 6. 지정가 1계약 매수 주문 (체결 방지를 위해 현재가보다 5.0 Pt 낮게 설정)
        test_order_price = current_price - 5.0
        print(f"[ STEP 2 ] 미니선물 지정가 매수 주문 요청 | 1계약 / {test_order_price:.2f} Pt")
        
        ord_res = order(
            env_dv="real",
            ord_dv="day",
            ord_prcs_dvsn_cd="02", # 신규 매수
            cano=cano,
            acnt_prdt_cd=prod,
            sll_buy_dvsn_cd="02", # 매수
            shtn_pdno=target_code,
            ord_qty="1",
            unit_price=f"{test_order_price:.2f}",
            nmpr_type_cd="02",     # 지정가
            krx_nmpr_cndt_cd="0",  # 일반
            ord_dvsn_cd="01"       # 개별 주문
        )
        
        if not ord_res.empty:
            odno = ord_res.iloc[0].get("ODNO", "")
            print(f"  주문 성공 ✅ | 주문번호: {odno}\n")
        else:
            print(f"  주문 실패 ❌\n")
    else:
        print(f"  시세 조회 실패: {res_price}")
except Exception as e:
    logger.exception(f"오류 발생: {e}")

print("✅ 테스트 완료!")
