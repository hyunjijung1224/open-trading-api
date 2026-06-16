"""
config.py - 전역 설정 관리
모의투자 기준 1억원 / Cloud Run + 로컬 겸용
"""

import logging
import os
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


class Config:
    # ── 환경 설정 (Local vs Cloud Run) ───────────────────
    # 구글 클라우드 런은 기본적으로 K_SERVICE 환경변수를 제공함
    ENVIRONMENT = os.getenv(
        "ENVIRONMENT", "cloud_run" if os.getenv("K_SERVICE") else "local"
    )
    PORT = int(os.getenv("PORT", 8080))
    
    # ── 트레이딩 기본 설정 ─────────────────────────────
    TOTAL_CAPITAL = int(os.getenv("TOTAL_CAPITAL", 100_000_000))
    MAX_POSITION_RATIO = float(os.getenv("MAX_POSITION_RATIO", 0.2))
    MAX_PORTFOLIO_SIZE = int(os.getenv("MAX_PORTFOLIO_SIZE", 5))
    STOP_LOSS_RATIO = float(os.getenv("STOP_LOSS_RATIO", 0.02))
    TAKE_PROFIT_RATIO = float(os.getenv("TAKE_PROFIT_RATIO", 0.05))

    @property
    def IS_LOCAL(self) -> bool:
        return self.ENVIRONMENT == "local"

    @property
    def IS_CLOUD_RUN(self) -> bool:
        return self.ENVIRONMENT == "cloud_run"

    @staticmethod
    def get_kst_now() -> datetime:
        """KST(한국표준시) 현재 시각 반환 (로컬/서버 공통)"""
        return datetime.now(timezone(timedelta(hours=9)))

    # ── 한국투자증권 ──────────────────────────────────────
    KIS_APP_KEY = os.getenv("KIS_APP_KEY", "")
    KIS_APP_SECRET = os.getenv("KIS_APP_SECRET", "")
    KIS_ACCOUNT_NO = os.getenv("KIS_ACCOUNT_NO", "")  # "12345678-01"
    KIS_HTS_ID = os.getenv("KIS_HTS_ID", "urbanist")
    KIS_IS_PAPER = True  # ★ 실전투자 시 False로 변경

    # ── 야간 거래 및 강제 청산 설정 ───────────────────────────
    ENABLE_NIGHT_TRADING = os.getenv("ENABLE_NIGHT_TRADING", "False").lower() in ("true", "1", "t", "yes")
    FORCE_CLOSE_MINUTES_BEFORE_CLOSE = int(os.getenv("FORCE_CLOSE_MINUTES_BEFORE_CLOSE", "15"))

    # ── 거래 파라미터 설정 ───────────────────────────
    # MACD 파라미터
    MACD_FAST = int(os.getenv("MACD_FAST", 5))
    MACD_SLOW = int(os.getenv("MACD_SLOW", 35))
    MACD_SIGNAL = int(os.getenv("MACD_SIGNAL", 9))

    # 수급 필터 파라미터
    OI_CHANGE_THRESHOLD = int(os.getenv("OI_CHANGE_THRESHOLD", 5))
    ZSCORE_THRESHOLD = float(os.getenv("ZSCORE_THRESHOLD", 0.2))

    # 손절 / 익절 설정 (포인트 단위)
    FIXED_STOP_LOSS_PTS = float(os.getenv("FIXED_STOP_LOSS_PTS", 2.0))
    FIXED_STOP_LOSS_2ND_PTS = float(os.getenv("FIXED_STOP_LOSS_2ND_PTS", 3.5))
    FIXED_TAKE_PROFIT_PTS = float(os.getenv("FIXED_TAKE_PROFIT_PTS", 4.0))

    # 50% 분할 손절 적용 여부
    PARTIAL_STOP_LOSS = os.getenv("PARTIAL_STOP_LOSS", "True").lower() in ("true", "1", "t", "yes")

    # 분할 청산 후 본절컷(Breakeven) 작동 여부
    MOVE_TO_BREAKEVEN = os.getenv("MOVE_TO_BREAKEVEN", "False").lower() in ("true", "1", "t", "yes")

    # ── 한국투자증권 실서버 전용 키 (Paper 환경에서 종목명 조회용) ──────
    # CTPF1604R(search-stock-info)는 모의투자 서버에서 지원 안 함.
    # Paper 모드라도 해당 TR만 Real 서버를 호출해야 하므로 Real 토큰이 필요.
    # KIS 실전투자 앱키/시크릿을 여기에 넣으면 종목명 조회가 정상 작동함.
    # (설정 안 해도 폴백으로 get_stock_price()가 실행되므로 필수는 아님)
    KIS_REAL_APP_KEY = os.getenv("KIS_REAL_APP_KEY", "")
    KIS_REAL_APP_SECRET = os.getenv("KIS_REAL_APP_SECRET", "")
    KIS_REAL_ACCOUNT_NO = os.getenv("KIS_REAL_ACCOUNT_NO", "")

    # ── Finnhub (해외 뉴스 수집용, 무료 티어) ─────────────────
    FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY", "")

    # ── FRED (미국 연방준비은행 경제 데이터, 무료 일 10만 요청) ──
    # https://fred.stlouisfed.org/docs/api/fred/
    FRED_API_KEY = os.getenv("FRED_API_KEY", "14782ac5ad1e6908e1a457a418a19a0f")

    # ── Gemini (무료: 기기 분석/대화 이원화) ─────────────────
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
    # 분석용 — flash-lite: 분당 30회, 일 1500회 (호출 많으므로 한도 넉넉한 모델 사용)
    PREFERRED_ANALYSIS = "models/gemini-flash-lite-latest"
    # 대화용 — 잘 안 쓰므로 성능 좋은 2.5-flash 사용 (분당 10회, 일 500회)
    PREFERRED_CHAT = "models/gemini-2.5-flash"
    # 폴백 — 분석 실패 시 대체 (분당 30회, 일 1500회로 여유 있음)
    GEMINI_FALLBACK = "models/gemini-2.5-flash-lite"

    GEMINI_MODEL_NAME = os.getenv("GEMINI_MODEL_NAME", PREFERRED_ANALYSIS)

    # ── Telegram ─────────────────────────────────────────
    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
    # 웹훅 보안용 시크릿 토큰 (텔레그램 서버 검증용)
    TELEGRAM_WEBHOOK_SECRET = os.getenv("TELEGRAM_WEBHOOK_SECRET", "aibot_secret_999")

    # ── Google Sheets / Service Account ──────────────────
    GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID", "")
    GOOGLE_SA_KEY_PATH = os.getenv("GOOGLE_SA_KEY_PATH", "secrets/service_account.json")
    GOOGLE_SERVICE_ACCOUNT_EMAIL = os.getenv("GOOGLE_SERVICE_ACCOUNT_EMAIL", "")
    # 하위 호환 (구버전 필드명)
    GOOGLE_CREDENTIALS_JSON = os.getenv(
        "GOOGLE_CREDENTIALS_JSON",
        os.getenv("GOOGLE_SA_KEY_PATH", "secrets/service_account.json"),
    )

    # 웹 대시보드 및 API 인증 토큰 (외부 접근 보안용)
    API_AUTH_TOKEN = os.getenv("API_AUTH_TOKEN", "aibot_admin_token_777")

    # ── Google 무료 한도 보호 ─────────────────────────────
    # gemini-2.5-flash 무료: 분당 10회, 일 500회
    # gemini-2.0-flash-lite 무료: 분당 30회, 일 1500회
    GEMINI_DAILY_LIMIT = 450  # 2.5-flash 일 500회 중 여유분 50 제외
    GEMINI_MINUTE_LIMIT = 9  # 2.5-flash 분당 10회 중 여유분 1 제외
    # Sheets API: 분당 300회
    SHEETS_KEYWORD_TTL_MIN = 30  # 키워드 캐시 30분

    @property
    def KIS_BASE_URL(self):
        if self.KIS_IS_PAPER:
            return "https://openapivts.koreainvestment.com:29443"
        return "https://openapi.koreainvestment.com:9443"

    @property
    def MAX_POSITION_AMOUNT(self) -> int:
        """종목당 최대 투자금액 (원)"""
        return int(self.TOTAL_CAPITAL * self.MAX_POSITION_RATIO)  # 2000만

    def validate(self):
        """필수 환경변수 체크"""
        missing = []
        for key in [
            "KIS_APP_KEY",
            "KIS_APP_SECRET",
            "KIS_ACCOUNT_NO",
            "GEMINI_API_KEY",
            "TELEGRAM_BOT_TOKEN",
            "TELEGRAM_CHAT_ID",
        ]:
            if not getattr(self, key):
                missing.append(key)
        if missing:
            raise EnvironmentError(f"필수 환경변수 누락: {', '.join(missing)}")

        # 실전 투자 경고 (raise 대신 로그)
        if not self.KIS_IS_PAPER:
            logger.warning("=" * 55)
            logger.warning("⚠️  실전 투자 모드 활성화!")
            logger.warning(f"   계좌번호: {self.KIS_ACCOUNT_NO}")
            logger.warning(f"   총 자금: {self.TOTAL_CAPITAL:,}원")
            logger.warning("   3초 후 자동 진행...")
            logger.warning("=" * 55)
            import time

            time.sleep(3)
        else:
            logger.info("✅ 모의투자 모드 (KIS_IS_PAPER=true)")

    def is_trading_day(self) -> bool:
        """KRX 개장일 여부 확인 (주말 + 공휴일 체크)"""
        from utils.market_calendar import is_trading_day

        return is_trading_day()

    def is_krx_holiday(self) -> bool:
        """KRX 공휴일 여부 확인"""
        from utils.market_calendar import is_krx_holiday

        return is_krx_holiday()

    def log_status(self):
        """설정 상태 로그 출력"""
        mode = "모의투자" if self.KIS_IS_PAPER else "실전투자 ⚠️"
        logger.info(f"  환경     : {self.ENVIRONMENT.upper()}")
        logger.info(f"  모드     : {mode}")
        logger.info(f"  총 자금  : {self.TOTAL_CAPITAL:,}원")
        logger.info(
            f"  종목 한도: {self.MAX_PORTFOLIO_SIZE}개 / 종목당 {self.MAX_POSITION_AMOUNT:,}원"
        )
        logger.info(
            f"  손절     : {self.STOP_LOSS_RATIO * 100:.0f}% / 익절: {self.TAKE_PROFIT_RATIO * 100:.0f}%"
        )
        logger.info(
            f"  Gemini   : {self.GEMINI_MODEL_NAME} (일 {self.GEMINI_DAILY_LIMIT}회 한도)"
        )
        logger.info(
            f"  Sheets   : {self.GOOGLE_SHEET_ID[:20]}..."
            if self.GOOGLE_SHEET_ID
            else "  Sheets   : (미설정)"
        )
        logger.info(
            f"  Sheets   : {self.GOOGLE_SHEET_ID[:20]}..."
            if self.GOOGLE_SHEET_ID
            else "  Sheets   : (미설정)"
        )
        logger.info(f"  Sheets   : {self.GOOGLE_SHEET_ID[:20]}..." if self.GOOGLE_SHEET_ID else "  Sheets   : (미설정)")


config = Config()
