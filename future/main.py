import os
import sys
import logging
import asyncio
from datetime import datetime
from typing import Optional, List, Dict, Any
import uvicorn
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Header, Depends
from pydantic import BaseModel
from dotenv import load_dotenv

# 루트 디렉토리를 path에 추가하여 config 및 utils 임포트 가능케 함
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config import config
from future.store.mariadb_store import MariaDBStore
from future.store.sheets_store import SheetsStore
from future.supervisor import TradingSupervisor
from future.engines import (
    RegimeEngine,
    SignalEngine,
    VolatilityEngine,
    PerformanceEngine,
    ForeignFlowEngine,
    RiskEngine,
    ExecutionEngine,
    OrderFlowEngine,
    OrderBookEngine,
    ExecutionPressureEngine,
    MorningEngine,
)

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("MainEntry")

# .env 명시적 로드
load_dotenv()

@asynccontextmanager
async def lifespan(app: FastAPI):
    global db_store, sheets_store, supervisor
    
    logger.info(f"시스템 초기 로드 시작... 실행 모드: {config.ENVIRONMENT.upper()}")
    
    # 1. Google Sheets 연결 객체 생성 (GCE 및 Cloud Run 공통 백업 연동용)
    sheets_store = SheetsStore(
        credentials_path=config.GOOGLE_SA_KEY_PATH,
        sheet_id=config.GOOGLE_SHEET_ID
    )
    
    # 2. GCE 또는 로컬 PC 환경인 경우
    if config.ENVIRONMENT == "local" or config.ENVIRONMENT == "gce":
        # 2.1 MariaDB 연결 객체 생성
        db_store = MariaDBStore(
            host=os.getenv("MARIADB_HOST", "127.0.0.1"),
            port=int(os.getenv("MARIADB_PORT", 3306)),
            user=os.getenv("MARIADB_USER", "kis_user"),
            password=os.getenv("MARIADB_PASSWORD", "kis_password"),
            database=os.getenv("MARIADB_DATABASE", "kis_trading")
        )
        
        # 2.2 Trading Supervisor 초기화
        supervisor = TradingSupervisor(db_store=db_store, sheets_store=sheets_store)
        
        # 2.3 핵심 거래 엔진 초기화 및 주입 (규칙 기반)
        regime_eng = RegimeEngine()
        signal_eng = SignalEngine()
        flow_eng = ForeignFlowEngine()
        vol_eng = VolatilityEngine()
        perf_eng = PerformanceEngine()
        risk_eng = RiskEngine(
            total_capital=config.TOTAL_CAPITAL if hasattr(config, "TOTAL_CAPITAL") else 100_000_000,
            single_trade_risk=0.01,
            daily_loss_limit=0.02,
            max_contracts=5
        )
        exec_eng = ExecutionEngine()
        order_flow_eng = OrderFlowEngine()
        order_book_eng = OrderBookEngine()
        exec_pressure_eng = ExecutionPressureEngine()
        morning_eng = MorningEngine(db=db_store)
        
        # AI Risk Agent (의결권 없는 리스크 분석용 목객체)
        class MockAIAgent:
            def analyze_risk(self):
                return {"risk_score": 0.1, "macro_regime_match": True}
        ai_agent = MockAIAgent()
        
        supervisor.load_engines(
            regime_eng=regime_eng,
            signal_eng=signal_eng,
            flow_eng=flow_eng,
            vol_eng=vol_eng,
            perf_eng=perf_eng,
            risk_eng=risk_eng,
            exec_eng=exec_eng,
            ai_agent=ai_agent,
            order_flow_eng=order_flow_eng,
            order_book_eng=order_book_eng,
            exec_pressure_eng=exec_pressure_eng,
            morning_eng=morning_eng,
        )
        
        # 2.4 수퍼바이저 기동 (웹소켓 연결 및 메인 루프 가동)
        await supervisor.start()
        
    elif config.ENVIRONMENT == "cloud_run":
        logger.info("[Cloud Run] Stateless 백업 서버 모드로 동작합니다. (GCE 헬스 모니터 및 긴급 복구 전용)")

    yield

    # Shutdown 로직
    if supervisor:
        supervisor.stop()
    if db_store:
        db_store.close()
    logger.info("시스템이 정상적으로 정지되었습니다.")

# FastAPI 앱 생성
app = FastAPI(title="KOSPI200 Futures AI Trading System", version="3.0", lifespan=lifespan)

# 글로벌 스토어 및 수퍼바이저 홀더
db_store: Optional[MariaDBStore] = None
sheets_store: Optional[SheetsStore] = None
supervisor: Optional[TradingSupervisor] = None

# 보안 토큰 의존성
def verify_token(authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="인증 토큰이 제공되지 않았습니다.")
    token = authorization.split("Bearer ")[1]
    if token != config.API_AUTH_TOKEN:
        raise HTTPException(status_code=403, detail="유효하지 않은 보안 토큰입니다.")
    return token

# =========================================================================
# GCE / Local - 헬스 API 엔드포인트
# =========================================================================
@app.get("/health")
def get_health():
    """GCE 봇이 정상 작동 중인지 Cloud Run이 확인하기 위한 API"""
    if supervisor and supervisor.is_running:
        return {
            "status": "UP",
            "active_positions_count": len(supervisor.active_positions),
            "target_code": supervisor.current_code,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
    raise HTTPException(status_code=503, detail="트레이딩 봇이 작동하지 않고 있거나 정지 상태입니다.")

# =========================================================================
# Cloud Run (Backup) 전용 API 엔드포인트
# =========================================================================
class HealthCheckRequest(BaseModel):
    gce_url: str

@app.post("/health-check")
async def cloud_run_health_check(payload: HealthCheckRequest, token: str = Depends(verify_token)):
    """
    [Cloud Run 전용] 5분마다 GCE 봇의 헬스 상태를 점검
    - GCE가 응답이 없으면 즉시 경보 및 비상 청산 프로세스 유도
    """
    import requests
    logger.info(f"[Cloud Run] GCE 헬스 체크 요청 수신 -> 대상: {payload.gce_url}")
    
    headers = {"Authorization": f"Bearer {token}"}
    try:
        res = requests.get(f"{payload.gce_url}/health", headers=headers, timeout=5)
        if res.status_code == 200:
            logger.info("GCE 상태 정상 확인 (UP).")
            return {"status": "GCE_OK", "detail": res.json()}
    except Exception as e:
        logger.error(f"GCE 연결 실패 (장애 의심): {e}")
        
    # 장애 판별 시 비상 청산(Emergency Check) 가동
    logger.warning("GCE 장애 감지! 비상 포지션 강제 청산 체크 진행...")
    return await execute_emergency_cleanup()

@app.post("/emergency-close")
async def force_emergency_close(token: str = Depends(verify_token)):
    """[Cloud Run 전용] 수동 또는 강제 비상 청산 실행 엔드포인트"""
    return await execute_emergency_cleanup()

async def execute_emergency_cleanup() -> dict:
    """Google Sheets의 ActivePositions를 대조하여 미청산 포지션 전량 시장가 강제 청산"""
    try:
        # Google Sheets로부터 액티브 포지션 복제본 로드 (GCE 장애 대안 원천 데이터)
        active_positions = sheets_store.get_active_positions()
        if not active_positions:
            logger.info("비상 복구: Google Sheets에 기록된 보유 포지션이 없어 조치하지 않습니다.")
            return {"status": "NO_ACTION", "detail": "보유 중인 비상 포지션 없음"}

        logger.warning(f"비상 복구: Google Sheets에서 {len(active_positions)}개의 활성 포지션 감지. 긴급 시장가 청산 집행!")
        
        # KIS API를 통한 강제 시장가 청산 주문 전송 (Cloud Run은 직접 KIS REST API 호출)
        cleaned_count = 0
        for pos in active_positions:
            success = await emergency_rest_close_order(pos)
            if success:
                cleaned_count += 1
                
        # Google Sheets 포지션 초기화 및 알림 전송
        if cleaned_count > 0:
            sheets_store.update_active_positions([])
            send_telegram_alert(f"🚨 [비상 대피] GCE 장애로 인해 {cleaned_count}개의 포지션이 Google Sheets 백업 기준 시장가로 강제 청산되었습니다.")
            return {"status": "CLEANED", "cleaned_count": cleaned_count}
            
        return {"status": "FAIL_TO_CLEAN", "detail": "청산 주문 전송 과정에서 오류가 발생했습니다."}
    except Exception as e:
        logger.error(f"비상 청산 처리 중 예외 발생: {e}")
        return {"status": "ERROR", "detail": str(e)}

async def emergency_rest_close_order(pos: dict) -> bool:
    """Cloud Run에서 직접 KIS REST API를 통해 반대 방향 시장가 주문을 날려 청산"""
    # KIS REST 주문 API 연동 및 발송 (실제 KIS API 규격 반영)
    logger.info(f"REST 긴급 주문 전송: {pos['futures_code']} {pos['side']} {pos['quantity']}계약")
    # 반대 매매 방향 설정: LONG -> SELL(01), SHORT -> BUY(02)
    opp_side = "01" if pos["side"] == "LONG" else "02"
    
    # 1. KIS 접근 토큰 획득 (REST)
    token = await fetch_rest_token()
    if not token:
        return False
        
    # 2. 시장가 주문 전송 (주식/선물구분 및 실전/모의구분 맞춰서 주문 TR 전송)
    # 선물옵션 복수 주문 TR: [실전] - JTTT5001U, [모의] - VTTT5001U 등
    tr_id = "VTTT5001U" if config.KIS_IS_PAPER else "JTTT5001U"
    url = f"{config.KIS_BASE_URL}/uapi/domestic-futureoption/v1/trading/order"
    
    headers = {
        "Content-Type": "application/json",
        "authorization": f"Bearer {token}",
        "appkey": config.KIS_APP_KEY,
        "appsecret": config.KIS_APP_SECRET,
        "tr_id": tr_id
    }
    
    payload = {
        "CANO": config.KIS_ACCOUNT_NO.split("-")[0],
        "ACNT_PRDT_CD": config.KIS_ACCOUNT_NO.split("-")[1],
        "PDNO": pos["futures_code"],
        "ORD_DVSN": "02",  # 시장가 (Market Order)
        "ORD_QTY": str(pos["quantity"]),
        "ORD_UNPR": "0",  # 시장가는 단가 0
        "SHTN_PDNO": "",
        "ORD_DVSN_CD": "",
        "SELN_BUY_DVSN_CD": opp_side
    }
    
    import requests
    res = requests.post(url, json=payload, headers=headers)
    if res.status_code == 200 and res.json().get("rt_cd") == "0":
        logger.info(f"REST 긴급 시장가 청산 주문 성공: {pos['futures_code']}")
        return True
    else:
        logger.error(f"REST 긴급 청산 주문 실패: {res.text}")
        return False

async def fetch_rest_token() -> Optional[str]:
    """KIS REST 토큰 신규 획득"""
    import requests
    url = f"{config.KIS_BASE_URL}/oauth2/tokenP"
    payload = {
        "grant_type": "client_credentials",
        "appkey": config.KIS_APP_KEY,
        "appsecret": config.KIS_APP_SECRET
    }
    res = requests.post(url, json=payload)
    if res.status_code == 200:
        return res.json()["access_token"]
    return None

def send_telegram_alert(msg: str):
    """텔레그램 메세지 즉시 발송"""
    import requests
    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": config.TELEGRAM_CHAT_ID, "text": msg}
    try:
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        logger.error(f"텔레그램 발송 실패: {e}")

# =========================================================================
# APP 구동 및 초기화 완료 (lifespan 컨텍스트 매니저 적용됨)
# =========================================================================

if __name__ == "__main__":
    # uvicorn 서버 실행
    port = config.PORT
    logger.info(f"FastAPI 서버 실행 중... 포트: {port}")
    uvicorn.run("main:app", host="0.0.0.0", port=port)
