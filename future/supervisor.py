import asyncio
import logging
from datetime import datetime, time as datetime_time, timedelta
from typing import Dict, List, Optional, Any

from config import config
from future.store.mariadb_store import MariaDBStore
from future.store.sheets_store import SheetsStore
from future.ws_manager import WebSocketManager
from future.engines.telegram_agent import TelegramAgent

logger = logging.getLogger("TradingSupervisor")

class TradingSupervisor:
    """
    KOSPI200 미니/야간선물 자동매매 시스템의 중앙 오케스트레이터 (GCE 상시 실행)
    - WebSocket 실시간 시세 수신 -> 초 단위 실시간 손절 감시 및 청산
    - 1초 메인 루프: 데이터 수집 -> 레짐 검출 -> 신호 생성 -> 수급 필터 -> 리스크/적응형 사이징 검증 -> 주문 실행
    - MariaDB 로컬 영구 저장 및 Google Sheets 실시간 동기화 (Failover 보장)
    """
    def __init__(self, db_store: MariaDBStore, sheets_store: SheetsStore):
        self.db = db_store
        self.sheets = sheets_store
        
        # 설정 로드 (만기 3영업일 전 자동 롤오버 반영)
        self.futures_code_day = self._get_active_futures_code(days_before=3)
        self.futures_code_night = self.futures_code_day
        self.current_code = self.futures_code_day
        
        # KIS 인증 환경 변수 로드
        self.app_key = config.KIS_APP_KEY
        self.app_secret = config.KIS_APP_SECRET
        self.ws_url = "ws://ops.koreainvestment.com:21000" if not config.KIS_IS_PAPER else "ws://ops.koreainvestment.com:31000"
            
        self.ws_manager = WebSocketManager(
            ws_url=self.ws_url,
            app_key=self.app_key,
            app_secret=self.app_secret,
            hts_id=config.KIS_HTS_ID
        )
        
        # 엔진 객체 플레이스홀더 (나중에 구현될 엔진들을 임포트하여 인스턴스화함)
        self.regime_engine = None
        self.signal_engine = None
        self.foreign_flow_engine = None
        self.volatility_engine = None
        self.performance_engine = None
        self.risk_engine = None
        self.execution_engine = None
        self.ai_risk_agent = None
        
        # Telegram Agent 초기화
        self.telegram_agent = TelegramAgent(self)
        
        # 상태 캐시
        self.active_positions: List[Dict[str, Any]] = []
        self.is_running = False
        self.last_health_update = datetime.min
        self.last_rest_sync = datetime.min
        self.current_session: Optional[str] = None
        
        # 1분봉 캔들 집계용 상태
        self._current_candle: Optional[Dict[str, Any]] = None
        self._current_candle_minute: Optional[str] = None  # "HH:MM" 포맷
        self._candle_tick_count: int = 0
        self._last_accum_volume: int = 0  # 누적 거래량 추적용
        self._history_candles: List[Dict[str, Any]] = []  # 지표 계산용 최근 캔들 캐시
        self._tick_log_count: int = 0  # 틱 로깅용 카운터
        self._latest_atr: float = 2.0  # 동적 트레일링 스톱을 위한 최신 ATR 캐시
        self._last_foreign_net_buy: Optional[int] = None
        self._latest_foreign_net_buy_1m: int = 0
        
        # 레짐 저장 상태 관리 변수 (매초 DB 부하 방지용)
        self._last_saved_regime: Optional[str] = None
        self._last_regime_save_time: datetime = datetime.min
        
        # REST API 폴링 폴백 상태 (웹소켓 틱 미유입 대비)
        self._last_rest_price_poll: datetime = datetime.min
        self._rest_price_poll_interval: int = 5  # 5초마다 REST 현재가 폴링
        self._last_rest_price: Optional[float] = None  # 마지막으로 획득한 REST 현재가
        self.last_pre_market_sync: datetime = datetime.min
        self.latest_temp_basis: float = 0.0
        
        # 성능 엔진(Performance Engine) 업데이트 주기 제어 변수
        self._last_perf_update_time: datetime = datetime.min
        
        # 신규 포지션 진입 시 리스크 엔진 계산 값을 KIS 잔고 동기화 시점에 반영하기 위한 임시 저장소
        self._pending_sl_tp: Dict[str, tuple[float, float]] = {}

        # 외국인 옵션 수급 누적 계약수 캐시
        self._latest_option_call_net: int = 0
        self._latest_option_put_net: int = 0

    def load_engines(self, regime_eng, signal_eng, flow_eng, vol_eng, perf_eng, risk_eng, exec_eng, ai_agent):
        """각 트레이딩 핵심 엔진 주입"""
        self.regime_engine = regime_eng
        self.signal_engine = signal_eng
        self.foreign_flow_engine = flow_eng
        self.volatility_engine = vol_eng
        self.performance_engine = perf_eng
        self.risk_engine = risk_eng
        self.execution_engine = exec_eng
        self.ai_risk_agent = ai_agent

    async def start(self):
        """시스템 시동 및 메인 오케스트레이션 루프 시작"""
        logger.info("Trading Supervisor 시동 중...")
        
        # 1. MariaDB에서 현재 활성 포지션 로드
        self._load_active_positions_from_db()
        
        # 1.2. MariaDB에서 최근 120개 캔들 로드하여 캐시 탑재
        self._load_history_candles_from_db()
        
        # 1.5. KIS 실시간 잔고 동기화 (기동 시 강제 DB/Sheets 싱크)
        try:
            await self._sync_positions_with_kis()
        except Exception as e:
            logger.error(f"기동 시 KIS 잔고 동기화 실패: {e}")
        
        # 2. 웹소켓 콜백 바인딩
        self.ws_manager.on_execution_callback = self._on_realtime_execution
        self.ws_manager.on_orderbook_callback = self._on_realtime_orderbook
        self.ws_manager.on_my_order_callback = self._on_my_order_fill
        
        # 3. 웹소켓 가동
        await self.ws_manager.connect()
        
        # 4. 실시간 감시 및 1초 주기 메인 루프 가동
        self.is_running = True
        asyncio.create_task(self._main_loop())
        
        # ── 텔레그램 에이전트 가동 ──
        asyncio.create_task(self.telegram_agent.scheduler_loop())
        asyncio.create_task(self.telegram_agent.telegram_polling_loop())
        
        logger.info("Trading Supervisor가 시작되었습니다.")

    def _sanitize_position_types(self, pos: Dict[str, Any]) -> Dict[str, Any]:
        """DB에서 읽어온 포지션의 Decimal 필드들을 float으로 변환하여 타입 충돌 방지"""
        for field in ["avg_price", "stop_loss", "take_profit", "trailing_stop", "highest_price", "lowest_price", "last_checked_price"]:
            if pos.get(field) is not None:
                pos[field] = float(pos[field])
        return pos

    def _load_active_positions_from_db(self):
        """MariaDB 데이터베이스로부터 현재 활성 포지션을 조회하여 캐시에 탑재"""
        db_positions = self.db.get_active_positions()
        self.active_positions = [self._sanitize_position_types(p) for p in db_positions]
        logger.info(f"MariaDB에서 {len(self.active_positions)}개의 활성 포지션을 로드했습니다.")
        # Google Sheets에 최신 포지션 실시간 동기화
        self.sheets.update_active_positions(self.active_positions)

    def _load_history_candles_from_db(self):
        """최근 120개의 분봉 캔들을 DB에서 로드하여 메모리 캐시에 탑재"""
        try:
            from datetime import timedelta
            end_time = datetime.now()
            start_time = end_time - timedelta(hours=3) # 최근 3시간치
            candles = self.db.get_candles(self.current_code, start_time, end_time)
            # DB 조회된 캔들은 시간 오름차순
            self._history_candles = list(candles)[-120:]
            logger.info(f"MariaDB에서 {len(self._history_candles)}개의 과거 캔들을 캐시에 로드했습니다.")
        except Exception as e:
            logger.error(f"과거 캔들 로드 실패: {e}")
            self._history_candles = []

    def _to_db_code(self, code: str) -> str:
        """실시간 데이터 단축코드(A05607)를 DB 적재 및 지표 계산용 표준코드(105V07)로 변환"""
        if len(code) == 6 and code.startswith("A"):
            prod = code[1:3]  # "05" or "01"
            year_char = code[3]  # e.g., "6"
            month = code[4:]  # e.g., "07"
            std_prefix = "1" + prod
            if year_char.isdigit():
                digit_to_letter = {
                    "4": "T", "5": "U", "6": "V", "7": "W", "8": "X", "9": "Y",
                    "0": "Z", "1": "A", "2": "B", "3": "C"
                }
                year_letter = digit_to_letter.get(year_char, "V")
            else:
                year_letter = year_char.upper()
            return f"{std_prefix}{year_letter}{month}"
        return code

    def _to_kis_code(self, code: str) -> str:
        """시스템 표준코드(105V07)를 KIS 웹소켓/REST API용 단축코드(A05607)로 변환"""
        if len(code) == 6 and code.startswith("1"):
            prod = code[1:3]  # "05" or "01"
            year_letter = code[3]  # e.g., "V"
            month = code[4:]  # e.g., "07"
            letter_to_digit = {
                "T": "4", "U": "5", "V": "6", "W": "7", "X": "8", "Y": "9",
                "Z": "0", "A": "1", "B": "2", "C": "3"
            }
            year_digit = letter_to_digit.get(year_letter.upper(), year_letter)
            return f"A{prod}{year_digit}{month}"
        return code

    def _get_second_thursday(self, year: int, month: int):
        """매월 두 번째 목요일(최종거래일)을 구함"""
        import datetime
        first_day = datetime.date(year, month, 1)
        first_weekday = first_day.weekday()
        days_to_first_thursday = (3 - first_weekday) % 7
        return first_day + datetime.timedelta(days=days_to_first_thursday + 7)

    def _is_final_trading_day(self, date_val) -> bool:
        """오늘이 최종거래일(만기일)인지 판단 (매월 두 번째 목요일)"""
        second_thurs = self._get_second_thursday(date_val.year, date_val.month)
        return date_val.date() == second_thurs

    def _get_rollover_date(self, year: int, month: int, days_before: int = 3):
        """최종거래일로부터 주말(토, 일)을 제외하고 지정된 영업일(기본 3일)만큼 역산하여 롤오버 날짜 계산"""
        import datetime
        second_thursday = self._get_second_thursday(year, month)
        current_date = second_thursday
        stepped_days = 0
        while stepped_days < days_before:
            current_date -= datetime.timedelta(days=1)
            if current_date.weekday() < 5:  # 월~금
                stepped_days += 1
        return current_date

    def _get_active_futures_code(self, days_before: int = 3) -> str:
        """롤오버 영업일 기준에 맞춰 현재 주간 거래 대상 미니선물 표준코드를 자동 계산 (예: 105V07)"""
        now = config.get_kst_now()
        year = now.year
        month = now.month
        
        rollover_date = self._get_rollover_date(year, month, days_before)
        if now.date() >= rollover_date:
            month += 1
            if month > 12:
                month = 1
                year += 1
                
        year_map = {
            2024: "T", 2025: "U", 2026: "V", 2027: "W", 2028: "X", 2029: "Y", 2030: "Z",
            2031: "A", 2032: "B", 2033: "C", 2034: "D", 2035: "E", 2036: "F", 2037: "G"
        }
        letter = year_map.get(year, "V")
        return f"105{letter}{month:02d}"

    def _get_current_session(self) -> str:
        """현재 KST 시간 기준 세션(주간장, 야간장, 휴장기 등) 반환"""
        kst_now = config.get_kst_now()
        now = kst_now.time()
        
        # 주말인 경우 휴장
        if kst_now.weekday() in [5, 6]:
            # 토요일 아침 06:00 이전까지는 금요일 야간 거래 세션에 해당할 수 있음
            if kst_now.weekday() == 5:  # 토요일
                if now <= datetime_time(6, 0):
                    if getattr(config, "ENABLE_NIGHT_TRADING", False):
                        return "night_market"
                elif datetime_time(6, 0) < now <= datetime_time(6, 30):
                    if getattr(config, "ENABLE_NIGHT_TRADING", False):
                        return "night_close"
            return "sleep"
            
        # 월요일 아침 00:00 ~ 08:00는 sleep
        if kst_now.weekday() == 0 and now < datetime_time(8, 0):
            return "sleep"

        # 최종거래일(만기일) 여부 확인
        is_final_day = self._is_final_trading_day(kst_now)
        day_end_time = datetime_time(15, 20) if is_final_day else datetime_time(15, 45)

        # 세션 분기 (주간장: 08:45 ~ 15:45 또는 15:20)
        if datetime_time(8, 45) <= now <= day_end_time:
            return "day_market"
        # 장전 시간외 세션 (08:00 ~ 08:45)
        elif datetime_time(8, 0) <= now < datetime_time(8, 45):
            return "pre_market"
        # 주간 정산 및 대기 (15:45/15:20 ~ 16:00)
        elif day_end_time < now <= datetime_time(16, 0):
            return "day_close"
        # 야간 개장 전 휴식기 (16:00 ~ 18:00)
        elif datetime_time(16, 0) < now < datetime_time(18, 0):
            return "gap"
        # 야간장: 18:00 ~ 다음날 06:00
        elif now >= datetime_time(18, 0) or now <= datetime_time(6, 0):
            if not getattr(config, "ENABLE_NIGHT_TRADING", False):
                return "sleep"
            return "night_market"
        # 야간 정산기 (06:00 ~ 06:30)
        elif datetime_time(6, 0) < now <= datetime_time(6, 30):
            if not getattr(config, "ENABLE_NIGHT_TRADING", False):
                return "sleep"
            return "night_close"
        # 대기 상태 (06:30 ~ 08:00)
        else:
            return "sleep"

    async def _check_and_force_close_at_session_end(self) -> bool:
        """장 종료 직전(예: 5분 전) 모든 포지션을 강제 청산하고 신규 매매 진입을 금지"""
        now = config.get_kst_now()
        now_time = now.time()
        
        force_close_minutes = getattr(config, "FORCE_CLOSE_MINUTES_BEFORE_CLOSE", 5)
        
        # 최종거래일(만기일) 여부 확인
        is_final_day = self._is_final_trading_day(now)
        day_close_time = datetime_time(15, 20) if is_final_day else datetime_time(15, 45)
        
        # 1. 주간장 마감 임박 체크
        day_force_start = (datetime.combine(now.date(), day_close_time) - timedelta(minutes=force_close_minutes)).time()
        is_day_force_window = day_force_start <= now_time < day_close_time
        
        # 2. 야간장 마감 임박 체크 (06:00 장 마감 기준, 야간 매매 활성화 시에만 적용)
        is_night_force_window = False
        if getattr(config, "ENABLE_NIGHT_TRADING", False):
            night_close_time = datetime_time(6, 0)
            night_force_start = (datetime.combine(now.date(), night_close_time) - timedelta(minutes=force_close_minutes)).time()
            is_night_force_window = night_force_start <= now_time < night_close_time

        if is_day_force_window or is_night_force_window:
            if self.active_positions:
                logger.warning(
                    f"[FORCE CLOSE] 장 마감 {force_close_minutes}분 전 감지! "
                    f"현재 포지션 {len(self.active_positions)}개 전량 시장가 청산 실행."
                )
                for pos in list(self.active_positions):
                    await self._execute_emergency_close(pos, reason="장 마감 임박 강제 청산")
            return True
            
        return False

    async def _manage_subscriptions_by_session(self, session: str):
        """현재 시장 세션에 따라 주간/야간 종목 웹소켓 구독 스위칭"""
        if session == self.current_session:
            return
            
        logger.info(f"세션 변경 감지: {self.current_session} -> {session}")
        
        tr_sign_id = "H0IFCNI9" if config.KIS_IS_PAPER else "H0IFCNI0"
        
        # 1. 주간장인 경우
        if session == "day_market":
            if self.current_session == "night_market":
                await self.ws_manager.unsubscribe("H0IFCNT0", self._to_kis_code(self.futures_code_night))
                await self.ws_manager.unsubscribe("H0IFASP0", self._to_kis_code(self.futures_code_night))
                await self.ws_manager.unsubscribe(tr_sign_id, "")
            self.current_code = self.futures_code_day
            kis_code = self._to_kis_code(self.current_code)
            await self.ws_manager.subscribe("H0IFCNT0", kis_code)
            await self.ws_manager.subscribe("H0IFASP0", kis_code)
            await self.ws_manager.subscribe(tr_sign_id, "")
            logger.info(f"주간 세션 진입: {self.current_code} ({kis_code}) 구독 활성화")
            
        # 2. 야간장인 경우
        elif session == "night_market":
            if self.current_session == "day_market":
                await self.ws_manager.unsubscribe("H0IFCNT0", self._to_kis_code(self.futures_code_day))
                await self.ws_manager.unsubscribe("H0IFASP0", self._to_kis_code(self.futures_code_day))
                await self.ws_manager.unsubscribe(tr_sign_id, "")
            self.current_code = self.futures_code_night
            kis_code = self._to_kis_code(self.current_code)
            await self.ws_manager.subscribe("H0IFCNT0", kis_code)
            await self.ws_manager.subscribe("H0IFASP0", kis_code)
            await self.ws_manager.subscribe(tr_sign_id, "")
            logger.info(f"야간 세션 진입: {self.current_code} ({kis_code}) 구독 활성화")
            
        # 3. 장외/휴장인 경우 기존 구독 해지
        else:
            if self.current_session == "day_market":
                await self.ws_manager.unsubscribe("H0IFCNT0", self._to_kis_code(self.futures_code_day))
                await self.ws_manager.unsubscribe("H0IFASP0", self._to_kis_code(self.futures_code_day))
                await self.ws_manager.unsubscribe(tr_sign_id, "")
            elif self.current_session == "night_market":
                await self.ws_manager.unsubscribe("H0IFCNT0", self._to_kis_code(self.futures_code_night))
                await self.ws_manager.unsubscribe("H0IFASP0", self._to_kis_code(self.futures_code_night))
                await self.ws_manager.unsubscribe(tr_sign_id, "")
            logger.info(f"장외 세션 진입 ({session}): 실시간 구독 비활성화")
            
        self.current_session = session

    def _on_realtime_execution(self, exec_data: Dict[str, Any]):
        """웹소켓 실시간 가격 수신 시 즉시 호출되는 초단위 손절 감시 + 1분봉 집계 콜백"""
        # 단축코드를 시스템 표준코드로 역변환하여 매핑 연동
        db_code = self._to_db_code(exec_data["code"])
        current_price = exec_data["price"]
        
        # 50틱마다 데이터 유입 로그 출력 (시각화 모니터링용)
        self._tick_log_count += 1
        if self._tick_log_count % 50 == 0:
            logger.info(f"웹소켓 실시간 틱 유입 중: {db_code} 현재가 {current_price:.2f} (누적 {self._tick_log_count}틱)")
            
        # ── 1분봉 캔들 집계 ──────────────────────────────────
        exec_data_copy = exec_data.copy()
        exec_data_copy["code"] = db_code
        self._aggregate_candle(exec_data_copy)
        
        # ── 손절/익절 감시 ──────────────────────────────────
        for pos in list(self.active_positions):
            if pos["futures_code"] == db_code:
                side = pos["side"]
                avg_price = pos["avg_price"]
                stop_loss = pos["stop_loss"]
                take_profit = pos["take_profit"]
                
                is_stop_loss_triggered = False
                is_take_profit_triggered = False
                
                if side == "LONG":
                    if current_price <= stop_loss:
                        is_stop_loss_triggered = True
                    elif take_profit > 0 and current_price >= take_profit:
                        is_take_profit_triggered = True
                elif side == "SHORT":
                    if current_price >= stop_loss:
                        is_stop_loss_triggered = True
                    elif take_profit > 0 and current_price <= take_profit:
                        is_take_profit_triggered = True
                        
                if is_stop_loss_triggered:
                    reason = "실시간 전량 손절 트리거"
                    logger.warning(f"[ALERT] {reason}! 즉시 청산 실행: 가격 {current_price} (진입단가: {avg_price})")
                    asyncio.create_task(self._execute_emergency_close(pos, reason))
                    continue
                elif is_take_profit_triggered:
                    reason = "실시간 분할 익절 트리거"
                    if pos["quantity"] >= 2:
                        tp_qty = pos["quantity"] // 2
                        logger.warning(f"[ALERT] {reason}! 50% 분할 익절 집행: 가격 {current_price} (수량: {tp_qty}계약 / 진입단가: {avg_price})")
                        asyncio.create_task(self._execute_partial_close(pos, tp_qty, reason))
                        
                        # 로컬 메모리 상태 변경 및 보존
                        pos["quantity"] -= tp_qty
                        pos["take_profit"] = 0.0
                        pos["half_tp_hit"] = 1
                        self.db.save_position(pos)
                        self.sheets.update_active_positions(self.active_positions)
                    else:
                        logger.warning(f"[ALERT] {reason} (1계약 잔여)! 전량 익절 청산 실행: 가격 {current_price} (진입단가: {avg_price})")
                        asyncio.create_task(self._execute_emergency_close(pos, "실시간 익절 트리거"))
                    continue
                    
                # ── 트레일링 스톱 업데이트 및 감시 ──
                atr_gap = self._latest_atr * 2.0
                is_trailing_stop_triggered = False
                
                # 틱 가격 및 타임스탬프 실시간 갱신
                pos["last_checked_price"] = current_price
                pos["updated_at"] = datetime.now()
                
                if side == "LONG":
                    # 최고가 경신 시 최고가 및 트레일링 스톱 상향
                    if pos.get("highest_price") is None or current_price > pos["highest_price"]:
                        pos["highest_price"] = current_price
                        pos["trailing_stop"] = current_price - atr_gap
                        
                    if pos.get("trailing_stop") is not None and current_price <= pos["trailing_stop"]:
                        is_trailing_stop_triggered = True
                        
                elif side == "SHORT":
                    # 최저가 경신 시 최저가 및 트레일링 스톱 하향
                    if pos.get("lowest_price") is None or current_price < pos["lowest_price"]:
                        pos["lowest_price"] = current_price
                        pos["trailing_stop"] = current_price + atr_gap
                        
                    if pos.get("trailing_stop") is not None and current_price >= pos["trailing_stop"]:
                        is_trailing_stop_triggered = True

                # 매 틱마다 로컬 MariaDB에 실시간 가격 필드(highest, lowest, last_checked) 즉시 반영
                self.db.save_position(pos)

                # 트레일링 스톱 기능 비활성화 (3단계 게이트 및 기술적 청산 위주 거래)
                # if is_trailing_stop_triggered:
                #     reason = f"실시간 트레일링 스톱 트리거 (스톱가: {pos['trailing_stop']:.2f})"
                #     logger.warning(f"[ALERT] {reason}! 즉시 청산 실행: 가격 {current_price} (진입단가: {avg_price})")
                #     asyncio.create_task(self._execute_emergency_close(pos, reason))

    def _aggregate_candle(self, exec_data: Dict[str, Any]):
        """실시간 틱 데이터를 1분봉 OHLCV 캔들로 집계하여 MariaDB에 저장"""
        price = exec_data["price"]
        code = exec_data["code"]
        accum_volume = exec_data.get("volume", 0)
        open_interest = exec_data.get("open_interest", 0)
        
        # 순거래량 계산
        if self._last_accum_volume == 0 or accum_volume < self._last_accum_volume:
            self._last_accum_volume = accum_volume
            tick_volume = 0
        else:
            tick_volume = accum_volume - self._last_accum_volume
            self._last_accum_volume = accum_volume
            
        # 체결시간에서 분 단위 추출 ("HHMMSS" -> "HH:MM")
        tick_time = exec_data.get("time", "")
        if len(tick_time) >= 4:
            tick_minute = f"{tick_time[:2]}:{tick_time[2:4]}"
        else:
            tick_minute = datetime.now().strftime("%H:%M")
        
        # 분이 바뀌면 이전 캔들을 DB에 저장하고 새 캔들 시작
        if self._current_candle_minute and tick_minute != self._current_candle_minute:
            self._flush_candle()
        
        # 새 캔들 시작 또는 기존 캔들 업데이트
        if self._current_candle is None or tick_minute != self._current_candle_minute:
            # 새 1분봉 시작
            now = datetime.now()
            candle_time_str = f"{now.strftime('%Y-%m-%d')} {tick_minute}:00"
            self._current_candle = {
                "futures_code": code,
                "candle_time": candle_time_str,
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "volume": tick_volume,  # 순거래량 기록
                "open_interest": open_interest,
                "accum_amount": 0.0
            }
            self._current_candle_minute = tick_minute
            self._candle_tick_count = 1
            logger.info(f"[NEW] 새 1분봉 집계 개시: {code} {candle_time_str} (시가={price:.2f})")
        else:
            # 기존 캔들 갱신 (OHLCV 업데이트)
            candle = self._current_candle
            if price > candle["high"]:
                candle["high"] = price
            if price < candle["low"]:
                candle["low"] = price
            candle["close"] = price
            candle["volume"] += tick_volume  # 순거래량 누적
            candle["open_interest"] = open_interest
            self._candle_tick_count += 1

    def _flush_candle(self):
        """완성된 1분봉 캔들을 MariaDB market_candles 테이블에 저장 및 캐시 갱신"""
        if not self._current_candle:
            return
        try:
            self.db.save_candles([self._current_candle])
            candle = self._current_candle
            logger.info(
                f"1분봉 저장 완료: {candle['futures_code']} {candle['candle_time']} "
                f"O={candle['open']:.2f} H={candle['high']:.2f} L={candle['low']:.2f} C={candle['close']:.2f} "
                f"V={candle['volume']} ({self._candle_tick_count}틱)"
            )
            
            # 메모리 캐시 갱신
            self._history_candles.append(candle)
            if len(self._history_candles) > 120:
                self._history_candles.pop(0)
                
        except Exception as e:
            logger.error(f"1분봉 DB 저장 실패: {e}")
        finally:
            self._current_candle = None
            self._candle_tick_count = 0

    def _on_realtime_orderbook(self, ob_data: Dict[str, Any]):
        """웹소켓 실시간 호가 수신 콜백 (스프레드 감시용)"""
        # 필요시 호가 정보를 활용한 변동성 보정이나 유동성(Spread) 검증 로직 반영 가능
        pass

    def _on_my_order_fill(self, fill_data: Dict[str, Any]):
        """주문 체결 통보 수신 시 내부 DB 및 포지션 동기화 즉시 실행"""
        logger.info(f"체결 통보 수신: {fill_data}")
        # REST API를 통해 실제 미체결 잔량 및 계좌 잔고를 최종 동기화하여 active_positions 동기화
        asyncio.create_task(self._sync_positions_with_kis())

    async def _execute_emergency_close(self, pos: Dict[str, Any], reason: str):
        """실시간 손절/익절 감시 또는 GCE 비상 복구에 의한 즉시 전량 시장가 청산 집행"""
        try:
            # Execution Engine을 통한 KIS 시장가 주문 발생
            if self.execution_engine:
                success = await self.execution_engine.market_close_position(pos)
                if success:
                    # DB에서 삭제
                    self.db.delete_position(pos["position_id"])
                    
                    # DB에 주문 이력 저장 (청산 시장가 주문)
                    order_db_data = {
                        "order_id": f"O_CLOSE_{int(datetime.now().timestamp())}",
                        "futures_code": pos["futures_code"],
                        "order_side": "SELL" if pos["side"] == "LONG" else "BUY",
                        "order_qty": pos["quantity"],
                        "order_price": self.ws_manager.get_latest_price(self._to_kis_code(pos["futures_code"])) or pos["stop_loss"],
                        "order_type": "MARKET",
                        "status": "FILLED",
                        "result_msg": f"긴급 청산 실행: {reason}"
                    }
                    try:
                        self.db.save_order(order_db_data)
                        logger.info(f"긴급 청산 주문 이력 DB 저장 완료: {order_db_data['order_id']}")
                    except Exception as e:
                        logger.error(f"긴급 청산 주문 이력 DB 저장 실패: {e}")
                    
                    # 완결된 거래 이력 저장
                    exit_price = self.ws_manager.get_latest_price(self._to_kis_code(pos["futures_code"])) or pos["stop_loss"]
                    trade_record = {
                        "trade_id": f"T_{int(datetime.now().timestamp())}",
                        "futures_code": pos["futures_code"],
                        "entry_side": pos["side"],
                        "entry_qty": pos["quantity"],
                        "entry_price": pos["avg_price"],
                        "exit_price": exit_price,
                        "entry_time": pos["updated_at"],
                        "exit_time": datetime.now(),
                        "net_pnl": self._calculate_pnl(pos, exit_price),
                        "fee": 0.0  # 간소화 표기
                    }
                    self.db.save_trade(trade_record)
                    self.sheets.append_trade_history(trade_record)
                    
                    # 로컬 캐시에서 제거 및 Sheets 동기화
                    if pos in self.active_positions:
                        self.active_positions.remove(pos)
                    self.sheets.update_active_positions(self.active_positions)
                    logger.info(f"시장가 청산 완료: {pos['futures_code']} {pos['side']} {pos['quantity']}계약 ({reason})")
                    
                    # 텔레그램 알림 전송
                    self.send_telegram(
                        f"[CLOSE] [포지션 청산] {reason}\n"
                        f"- 종목: {pos['futures_code']}\n"
                        f"- 진입방향: {pos['side']}\n"
                        f"- 수량: {pos['quantity']}계약\n"
                        f"- 진입단가: {pos['avg_price']:,.2f}\n"
                        f"- 청산단가: {trade_record['exit_price']:,.2f}\n"
                        f"- 실현손익: {trade_record['net_pnl']:+,}원"
                    )
        except Exception as e:
            logger.error(f"긴급 청산 실패: {e}")

    async def _execute_partial_close(self, pos: Dict[str, Any], qty: int, reason: str):
        """실시간 일부(50%) 분할 익절 집행"""
        try:
            # Execution Engine을 통한 KIS 시장가 일부 청산 주문 발생
            if self.execution_engine:
                success = await self.execution_engine.market_close_position(pos, qty=qty)
                if success:
                    # DB에 주문 이력 저장 (분할 청산 시장가 주문)
                    order_db_data = {
                        "order_id": f"O_PARTIAL_{int(datetime.now().timestamp())}",
                        "futures_code": pos["futures_code"],
                        "order_side": "SELL" if pos["side"] == "LONG" else "BUY",
                        "order_qty": qty,
                        "order_price": self.ws_manager.get_latest_price(self._to_kis_code(pos["futures_code"])) or pos["take_profit"],
                        "order_type": "MARKET",
                        "status": "FILLED",
                        "result_msg": f"분할 청산 실행: {reason}"
                    }
                    try:
                        self.db.save_order(order_db_data)
                        logger.info(f"분할 청산 주문 이력 DB 저장 완료: {order_db_data['order_id']}")
                    except Exception as e:
                        logger.error(f"분할 청산 주문 이력 DB 저장 실패: {e}")
                    
                    # 일부 완결된 거래 이력 저장
                    exit_price = self.ws_manager.get_latest_price(self._to_kis_code(pos["futures_code"])) or pos["take_profit"]
                    
                    multiplier = 50000 if "105" in pos["futures_code"] else 250000
                    diff = float(exit_price) - float(pos["avg_price"])
                    if pos["side"] == "SHORT":
                        diff = -diff
                    pnl_amount = diff * qty * multiplier
                    
                    trade_record = {
                        "trade_id": f"T_PARTIAL_{int(datetime.now().timestamp())}",
                        "futures_code": pos["futures_code"],
                        "entry_side": pos["side"],
                        "entry_qty": qty,
                        "entry_price": pos["avg_price"],
                        "exit_price": exit_price,
                        "entry_time": pos["updated_at"],
                        "exit_time": datetime.now(),
                        "net_pnl": pnl_amount,
                        "fee": 0.0
                    }
                    self.db.save_trade(trade_record)
                    self.sheets.append_trade_history(trade_record)
                    
                    logger.info(f"분할 청산 완료: {pos['futures_code']} {pos['side']} {qty}계약 ({reason})")
                    
                    # 텔레그램 알림 전송
                    self.send_telegram(
                        f"[PARTIAL_CLOSE] [분할 익절] {reason}\n"
                        f"- 종목: {pos['futures_code']}\n"
                        f"- 진입방향: {pos['side']}\n"
                        f"- 익절수량: {qty}계약\n"
                        f"- 진입단가: {pos['avg_price']:,.2f}\n"
                        f"- 익절단가: {trade_record['exit_price']:,.2f}\n"
                        f"- 실현손익: {trade_record['net_pnl']:+,}원"
                    )
        except Exception as e:
            logger.error(f"분할 청산 실패: {e}")

    def send_telegram(self, msg: str):
        """텔레그램 메시지 발송 헬퍼"""
        if self.telegram_agent:
            self.telegram_agent.send_telegram(msg)

    def _calculate_pnl(self, pos: Dict[str, Any], exit_price: float) -> float:
        """선물 포지션 손익 계산 (승수 50,000원 반영)"""
        multiplier = 50000 if "105" in pos["futures_code"] else 250000 # 코스피200 미니선물 승수 5만원, 일반선물 승수 25만원
        diff = float(exit_price) - float(pos["avg_price"])
        if pos["side"] == "SHORT":
            diff = -diff
        return diff * pos["quantity"] * multiplier

    async def _sync_positions_with_kis(self):
        """KIS API 잔고 조회와 내부 active_positions 캐시 및 데이터베이스 최종 상태 동기화"""
        try:
            if self.execution_engine:
                # 동기화 전 기존 활성 포지션 캐시 백업
                old_positions = {p["position_id"]: p for p in self.active_positions}
                
                kis_positions = await self.execution_engine.fetch_active_positions()
                
                # 1. 기존 DB의 active_positions 테이블 비우기
                existing_in_db = [self._sanitize_position_types(p) for p in self.db.get_active_positions()]
                for ep in existing_in_db:
                    self.db.delete_position(ep["position_id"])
                
                # 2. KIS에서 받아온 데이터로 DB 및 캐시 갱신
                # 기존 DB 레코드에서 가격 추적 필드 보존을 위해 먼저 로드
                existing_db_map = {ep["position_id"]: ep for ep in existing_in_db}
                self.active_positions.clear()
                new_position_ids = set()
                for kp in kis_positions:
                    # KIS 단축코드(A05609)를 시스템 표준코드(105V09)로 일치화하여 DB 적재 및 모니터링 연동
                    kp["futures_code"] = self._to_db_code(kp["futures_code"])
                    kp["position_id"] = f"P_{kp['futures_code']}_{kp['side']}"
                    
                    existing = existing_db_map.get(kp["position_id"])
                    if existing:
                        # highest_price: 기존 값이 현재 avg_price보다 높으면 보존
                        ex_high = existing.get("highest_price")
                        if ex_high and float(ex_high) > kp["avg_price"]:
                            kp["highest_price"] = float(ex_high)
                        # lowest_price: 기존 값이 현재 avg_price보다 낮으면 보존
                        ex_low = existing.get("lowest_price")
                        if ex_low and float(ex_low) < kp["avg_price"]:
                            kp["lowest_price"] = float(ex_low)
                        # last_checked_price: 기존 갱신 값 보존
                        ex_lcp = existing.get("last_checked_price")
                        if ex_lcp and float(ex_lcp) != kp["avg_price"]:
                            kp["last_checked_price"] = float(ex_lcp)
                        # trailing_stop 보존
                        ex_ts = existing.get("trailing_stop")
                        if ex_ts:
                            kp["trailing_stop"] = float(ex_ts)
                            
                        # 수량 변경 감지 (피라미딩 혹은 일부 청산 동기화)
                        ex_qty = int(existing.get("quantity", 0))
                        kp_qty = int(kp["quantity"])
                        
                        if kp_qty != ex_qty:
                            if kp_qty > ex_qty:
                                # 수량 증가 (추가 매수) -> 새로운 평단가 기준 SL/TP 재설정 및 상태 리셋
                                logger.info(f"수량 증가 감지 (피라미딩): {ex_qty} -> {kp_qty}. 손절/익절가를 재산출합니다.")
                                sl_pts = getattr(config, "FIXED_STOP_LOSS_PTS", 2.0)
                                tp_pts = getattr(config, "FIXED_TAKE_PROFIT_PTS", 4.0)
                                
                                kp["half_tp_hit"] = 0
                                kp["half_sl_hit"] = 0
                                if kp["side"] == "LONG":
                                    kp["stop_loss"] = kp["avg_price"] - sl_pts
                                    kp["take_profit"] = kp["avg_price"] + tp_pts
                                else:
                                    kp["stop_loss"] = kp["avg_price"] + sl_pts
                                    kp["take_profit"] = kp["avg_price"] - tp_pts
                            else:
                                # 수량 감소 (분할 청산 반영) -> 기존 DB 상태 보존
                                kp["stop_loss"] = float(existing.get("stop_loss")) if existing.get("stop_loss") is not None else kp["avg_price"]
                                kp["take_profit"] = float(existing.get("take_profit")) if existing.get("take_profit") is not None else kp["avg_price"]
                                kp["half_tp_hit"] = int(existing.get("half_tp_hit", 0))
                                kp["half_sl_hit"] = int(existing.get("half_sl_hit", 0))
                        else:
                            # 수량 변동 없음 -> 기존 DB 값 보존
                            ex_sl = existing.get("stop_loss")
                            if ex_sl is not None:
                                kp["stop_loss"] = float(ex_sl)
                            ex_tp = existing.get("take_profit")
                            if ex_tp is not None:
                                kp["take_profit"] = float(ex_tp)
                            kp["half_tp_hit"] = int(existing.get("half_tp_hit", 0))
                            kp["half_sl_hit"] = int(existing.get("half_sl_hit", 0))
                        
                        logger.info(f"기존 DB 가격 추적 필드 보존: {kp['position_id']} (highest={kp.get('highest_price')}, last_checked={kp.get('last_checked_price')}, sl={kp.get('stop_loss')}, tp={kp.get('take_profit')}, half_sl_hit={kp.get('half_sl_hit')})")
                    else:
                        # 신규 포지션: Risk Engine이 산출한 손절가/익절가가 있다면 적용
                        code = kp["futures_code"]
                        if code in self._pending_sl_tp:
                            sl_price, tp_price = self._pending_sl_tp[code]
                            kp["stop_loss"] = sl_price
                            kp["take_profit"] = tp_price
                            logger.info(f"[RISK SYNC] 신규 포지션 진입 감지 -> 리스크 엔진 계산값 적용: {kp['position_id']} (sl={sl_price:.2f}, tp={tp_price:.2f})")
                            del self._pending_sl_tp[code]
                    
                    self.db.save_position(kp)
                    self.active_positions.append(self._sanitize_position_types(kp))
                    new_position_ids.add(kp["position_id"])

                
                # 3. 사라진 포지션(청산 완료된 포지션) 탐지 및 trades DB 자동 적재
                for pos_id, old_pos in old_positions.items():
                    if pos_id not in new_position_ids:
                        logger.info(f"동기화 중 포지션 청산 완료 감지: {pos_id}")
                        
                        # 청산 가격 획득 시도
                        exit_price = self.ws_manager.get_latest_price(self._to_kis_code(old_pos["futures_code"]))
                        if not exit_price:
                            exit_price = old_pos.get("last_checked_price") or old_pos["avg_price"]
                            
                        # 거래 이력 레코드 생성
                        trade_record = {
                            "trade_id": f"T_{int(datetime.now().timestamp())}_{old_pos['futures_code']}",
                            "futures_code": old_pos["futures_code"],
                            "entry_side": old_pos["side"],
                            "entry_qty": old_pos["quantity"],
                            "entry_price": old_pos["avg_price"],
                            "exit_price": exit_price,
                            "entry_time": old_pos.get("updated_at") or datetime.now(),
                            "exit_time": datetime.now(),
                            "net_pnl": self._calculate_pnl(old_pos, exit_price),
                            "fee": 0.0
                        }
                        
                        # DB 및 구글 시트에 청산 내역 기록
                        try:
                            self.db.save_trade(trade_record)
                            self.sheets.append_trade_history(trade_record)
                            logger.info(f"청산 거래 이력 DB/Sheets 적재 완료: {trade_record}")
                        except Exception as e:
                            logger.error(f"청산 거래 이력 적재 중 실패: {e}")
                
                # 4. Google Sheets 동기화
                self.sheets.update_active_positions(self.active_positions)
                logger.info(f"KIS 잔고 동기화 완료: {len(self.active_positions)}개 포지션 적재.")
        except Exception as e:
            logger.error(f"KIS 잔고 동기화 오류: {e}")

    async def _poll_rest_price_and_update(self) -> Optional[float]:
        """REST API를 통해 현재가를 폴링하고, active_positions 가격 필드를 직접 갱신 (웹소켓 폴백)"""
        if not self.execution_engine:
            return None
        
        short_code = self._to_kis_code(self.current_code)
        try:
            price = await self.execution_engine.fetch_futures_price_rest(short_code)
            if price:
                self._last_rest_price = price
                logger.info(f"[REST 폴백] 현재가 획득: {self.current_code} = {price:.2f} (웹소켓 틱 대체)")
                
                # 틱 데이터 시뮬레이션하여 분봉 집계 및 손절/익절/트레일링스톱 실시간 감시 가동
                simulated_tick = {
                    "code": short_code,
                    "price": price,
                    "volume": 0,
                    "time": datetime.now().strftime("%H%M%S")
                }
                # _on_realtime_execution에서 분봉 집계 및 active_positions 갱신/DB 저장이 일괄 자동 수행됨
                self._on_realtime_execution(simulated_tick)
                
                return price
        except Exception as e:
            logger.error(f"REST 현재가 폴링 실패: {e}")
        return None

    async def _main_loop(self):
        """Trading Supervisor 1초 주기 메인 루프"""
        while self.is_running:
            try:
                # 0. 영업일 롤오버 체크 (3영업일 전) 및 종목 스위칭
                active_code = self._get_active_futures_code(days_before=3)
                if active_code != self.futures_code_day:
                    logger.warning(
                        f"[ROLLOVER] 롤오버 감지! 활성 종목코드가 {self.futures_code_day}에서 {active_code}로 변경됩니다."
                    )
                    # 기존 세션 구독 해지
                    if self.current_session == "day_market":
                        await self.ws_manager.unsubscribe("H0IFCNT0", self._to_kis_code(self.futures_code_day))
                        await self.ws_manager.unsubscribe("H0IFASP0", self._to_kis_code(self.futures_code_day))
                    elif self.current_session == "night_market":
                        await self.ws_manager.unsubscribe("H0IFCNT0", self._to_kis_code(self.futures_code_night))
                        await self.ws_manager.unsubscribe("H0IFASP0", self._to_kis_code(self.futures_code_night))
                    
                    old_code = self.futures_code_day
                    self.futures_code_day = active_code
                    self.futures_code_night = active_code
                    self.current_code = active_code
                    
                    # 텔레그램 알림 발송
                    self.send_telegram(
                        f"[ROLLOVER] [종목 롤오버 실행]\n"
                        f"- 기존: {old_code} ({self._to_kis_code(old_code)})\n"
                        f"- 신규: {active_code} ({self._to_kis_code(active_code)})"
                    )
                    
                    # 새 종목 기준 과거 캔들 데이터 재적재
                    self._load_history_candles_from_db()
                    
                    # 현재 장이 열려 있는 세션이면 새 종목 구독 신청
                    session = self._get_current_session()
                    if session in ["day_market", "night_market"]:
                        new_kis_code = self._to_kis_code(self.current_code)
                        await self.ws_manager.subscribe("H0IFCNT0", new_kis_code)
                        await self.ws_manager.subscribe("H0IFASP0", new_kis_code)
                        logger.info(f"롤오버 완료: 새 종목 구독 실행 ({self.current_code} / {new_kis_code})")
                
                session = self._get_current_session()
                
                # 메인 루프 시간 경과에 따른 1분봉 강제 마감 체크
                if self._current_candle_minute:
                    current_minute = datetime.now().strftime("%H:%M")
                    if current_minute != self._current_candle_minute:
                        logger.info(f"시간 경과 감지: 1분봉 강제 마감 실행 ({self._current_candle_minute} -> {current_minute})")
                        self._flush_candle()
                        
                # 1. 세션별 웹소켓 구독 스위칭
                await self._manage_subscriptions_by_session(session)
                
                # 2. 주간/야간 실거래 시간대인 경우 메인 거래 흐름 수행
                if session in ["day_market", "night_market"]:
                    now = datetime.now()
                    
                    # 웹소켓 현재가 우선 확인
                    latest_price = self.ws_manager.get_latest_price(self._to_kis_code(self.current_code))
                    
                    # 웹소켓 틱이 없는 경우 REST API 폴링 폴백 (5초마다)
                    if not latest_price:
                        elapsed_since_poll = (now - self._last_rest_price_poll).total_seconds()
                        if elapsed_since_poll >= self._rest_price_poll_interval:
                            self._last_rest_price_poll = now
                            latest_price = await self._poll_rest_price_and_update()
                        else:
                            # 마지막으로 획득한 REST 가격 재사용 (폴링 간격 사이)
                            latest_price = self._last_rest_price
                    
                    if latest_price:
                        # 1분 주기로 REST API 보조 데이터(수급, 차트 등) 동기화
                        if (now - self.last_rest_sync).total_seconds() >= 60:
                            await self._sync_supplementary_data()
                            self.last_rest_sync = now
                            
                        # 거래 흐름 집행
                        await self._process_trading_logic(latest_price)
                elif session == "pre_market":
                    now = datetime.now()
                    # 1분 주기로 장전 임시 베이시스 산출 및 DB 적재
                    if (now - self.last_pre_market_sync).total_seconds() >= 60:
                        await self._process_pre_market_logic()
                        self.last_pre_market_sync = now
                        
                # 3. 5분 주기 구글 시트 헬스체크 업데이트
                now = datetime.now()
                if (now - self.last_health_update).total_seconds() >= 300:
                    self._send_health_to_sheets(session)
                    self.last_health_update = now
                    
            except Exception as e:
                logger.error(f"메인 루프 에러 발생: {e}")
                
            await asyncio.sleep(1)

    async def _sync_supplementary_data(self):
        """1분마다 분봉 차트, 미결제약정 및 투자자별 수급 동향 REST API 동기화"""
        logger.info("보조 데이터(수급, 차트) 동기화 수행...")
        # 현재 진행 중인 캔들을 중간 저장 (데이터 유실 방지)
        if self._current_candle:
            try:
                self.db.save_candles([self._current_candle])
                logger.info(f"미완성 캔들 중간 저장 완료: {self._current_candle['candle_time']}")
            except Exception as e:
                logger.error(f"미완성 캔들 중간 저장 실패: {e}")
                
        # ?? ?? ?? ??
        if self.execution_engine:
            try:
                raw_option_data = await self.execution_engine.fetch_option_trend()
                self._latest_option_call_net = raw_option_data.get("foreign_call_net", 0)
                self._latest_option_put_net = raw_option_data.get("foreign_put_net", 0)
                logger.debug(f"[?? ??] ??????={self._latest_option_call_net:+,} ??????={self._latest_option_put_net:+,}")
            except Exception as e:
                logger.error(f"?? ?? ?? ?? ? ??: {e}")
                self._latest_option_call_net = 0
                self._latest_option_put_net = 0
                
        # ?? ?? ??? ? DB ??
        if self.execution_engine and self.foreign_flow_engine:
            try:
                raw_investor_data = await self.execution_engine.fetch_investor_trend(self.current_code)
                self.foreign_flow_engine.update_flow(raw_investor_data)
                flow_data = self.foreign_flow_engine.get_latest_flow()
                current_foreign_net = flow_data["foreign_net_buy"]
                    
                if self._last_foreign_net_buy is not None:
                    self._latest_foreign_net_buy_1m = current_foreign_net - self._last_foreign_net_buy
                else:
                    self._latest_foreign_net_buy_1m = 0
                self._last_foreign_net_buy = current_foreign_net

                total_activity = (
                    abs(flow_data.get("foreign_net_buy", 0))
                    + abs(flow_data.get("institution_net_buy", 0))
                    + abs(flow_data.get("individual_net_buy", 0))
                    + abs(self._latest_option_call_net)
                    + abs(self._latest_option_put_net)
                )
                
                # ?? ???? ?? 0? ??? DB? ??
                option_activity = abs(self._latest_option_call_net) + abs(self._latest_option_put_net)
                if total_activity > 0 or option_activity > 0:
                    db_flow = {
                        "foreign_net_buy": flow_data["foreign_net_buy"],
                        "institution_net_buy": flow_data["institution_net_buy"],
                        "individual_net_buy": flow_data["individual_net_buy"],
                        "foreign_oi_change": flow_data["foreign_oi_change"],
                        "flow_strength": flow_data["flow_strength"],
                        "foreign_net_buy_1m": self._latest_foreign_net_buy_1m,
                        "foreign_call_net": self._latest_option_call_net,
                        "foreign_put_net": self._latest_option_put_net
                    }
                    self.db.save_foreign_flow(db_flow)
                    logger.info(
                        f"??? ?? ???? MariaDB(foreign_flows)? ?? ??: "
                        f"??={db_flow['foreign_net_buy']:+,} (1???={self._latest_foreign_net_buy_1m:+}) "
                        f"??={db_flow['institution_net_buy']:+,} ???={db_flow['foreign_call_net']:+,} ???={db_flow['foreign_put_net']:+,}"
                    )
            except Exception as e:
                logger.error(f"?? ??? ??? ? DB ?? ??: {e}")

    async def _process_pre_market_logic(self):
        """장전(08:00 ~ 08:45) 1분 주기 임시 베이시스 산출 및 DB 적재"""
        logger.info("[PRE-MARKET] 임시 베이시스 산출 시작...")
        if not self.execution_engine:
            logger.warning("[PRE-MARKET] Execution Engine이 로드되지 않았습니다.")
            return

        try:
            # 1. 선물 예상체결가 조회
            futs_short_code = self._to_kis_code(self.current_code)
            futs_data = await self.execution_engine.fetch_futures_pre_market_price_rest(futs_short_code)
            if not futs_data:
                logger.warning(f"[PRE-MARKET] 선물 {futs_short_code} 시세 조회 실패")
                return

            futs_expected = futs_data["futs_prpr"]
            futs_prev_close = futs_data["prev_close"]
            futs_return = (futs_expected / futs_prev_close) - 1.0

            # 2. 대형주 5개 예상체결가 조회 및 가중 수익률 계산
            # 삼성전자(50%), SK하이닉스(20%), LG에너지솔루션(10%), 삼성바이오로직스(10%), 현대차(10%)
            stock_basket = {
                "005930": 0.50,  # 삼성전자
                "000660": 0.20,  # SK하이닉스
                "373220": 0.10,  # LG에너지솔루션
                "207940": 0.10,  # 삼성바이오로직스
                "005380": 0.10   # 현대차
            }

            weighted_spot_return = 0.0
            fetched_count = 0

            for ticker, weight in stock_basket.items():
                # API 호출 사이 간격 확보 (초당 호출 제한 방어: 모의투자 2 TPS 한도를 고려하여 0.6초로 변경)
                await asyncio.sleep(0.6)
                stock_data = await self.execution_engine.fetch_stock_price_rest(ticker)
                if stock_data:
                    prev_close = stock_data["stck_sdpr"]
                    expected_price = stock_data["antg_prc"]
                    if not expected_price or expected_price == 0:
                        expected_price = stock_data["stck_prpr"] or prev_close

                    if prev_close > 0:
                        ret = (expected_price / prev_close) - 1.0
                        weighted_spot_return += ret * weight
                        fetched_count += 1
                        logger.debug(f"[PRE-MARKET] 종목 {ticker}: 예상가={expected_price:.0f}, 전일종가={prev_close:.0f}, 수익률={ret*100:+.2f}%")
                else:
                    logger.warning(f"[PRE-MARKET] 종목 {ticker} 시세 조회 실패")

            if fetched_count == 0:
                logger.warning("[PRE-MARKET] 대형주 시세를 전혀 조회하지 못했습니다.")
                return

            # 3. 임시 베이시스 계산
            # 임시 현물 지수 = 선물 전일종가 * (1 + 대형주 가중 수익률)
            # 임시 베이시스 (포인트) = 선물 예상체결가 - 임시 현물 지수
            temp_spot_index = futs_prev_close * (1 + weighted_spot_return)
            temp_basis = futs_expected - temp_spot_index
            self.latest_temp_basis = temp_basis

            logger.info(
                f"[PRE-MARKET] 임시 베이시스 계산 완료: "
                f"선물 예상가={futs_expected:.2f} (수익률={futs_return*100:+.2f}%), "
                f"대형주 가중수익률={weighted_spot_return*100:+.2f}%, "
                f"임시 베이시스={temp_basis:+.2f} Pt"
            )

            # 4. DB 저장
            db_data = {
                "futures_code": self.current_code,
                "expected_futures_price": futs_expected,
                "expected_spot_return": weighted_spot_return,
                "expected_futures_return": futs_return,
                "temporary_basis": temp_basis
            }
            self.db.save_pre_market_basis(db_data)

        except Exception as e:
            logger.error(f"[PRE-MARKET] 임시 베이시스 집계 중 오류 발생: {e}", exc_info=True)

    def _has_morning_entry_today(self) -> bool:
        """오늘 아침 개장 이후 이미 진입했거나 완료된 거래가 있는지 확인"""
        # 1. 현재 활성 포지션이 있으면 이미 진입한 상태
        if self.active_positions:
            return True
            
        # 2. 오늘 완료된 거래가 있는지 DB에서 조회
        now = datetime.now()
        today_start = datetime(now.year, now.month, now.day, 8, 45, 0)
        try:
            recent_trades = self.db.get_recent_trades(limit=10)
            for t in recent_trades:
                exit_time = t["exit_time"]
                if isinstance(exit_time, str):
                    exit_time = datetime.strptime(exit_time, "%Y-%m-%d %H:%M:%S")
                if exit_time >= today_start:
                    return True
        except Exception as e:
            logger.error(f"최근 거래 내역 조회 및 오늘 진입 여부 판별 실패: {e}")
        return False

    def _calculate_indicators(self, candles: List[Dict[str, Any]], current_price: float) -> Dict[str, Any]:
        """최근 분봉 캔들 데이터와 현재가를 기반으로 보조 지표 계산 (NumPy 활용)"""
        now = datetime.now()
        # 아침 개장 직후 1시간 동안 (08:45 ~ 09:45) 캔들이 부족할 경우, 모닝 브리핑 점수를 기반으로 한 아침 매매 모드 적용
        if len(candles) < 59:
            # KST 시간 기준 08:45 ~ 09:45 사이이며 오늘 장 개시 후 첫 진입인 경우
            if datetime_time(8, 45) <= now.time() < datetime_time(9, 45):
                if not self._has_morning_entry_today():
                    today_str = now.strftime("%Y-%m-%d")
                    try:
                        briefing = self.db.get_morning_briefing_score(today_str)
                        if briefing and briefing.get("direction") in ["BUY", "SELL"]:
                            logger.info(f"[MORNING MODE] 아침 캔들 부족 ({len(candles)}/60) -> 모닝 브리핑 점수를 사용하여 즉시 진입 준비 (방향: {briefing['direction']}, 점수: {briefing['score']})")
                            return {
                                "is_morning_mode": True,
                                "morning_direction": briefing["direction"],
                                "morning_score": float(briefing["score"]),
                                "atr": 2.0,  # 아침 모드 기본 ATR값 설정 (약 2.0포인트)
                                "prev_close": current_price,
                                "current_price": current_price,
                                "option_call_net": self._latest_option_call_net,
                                "option_put_net": self._latest_option_put_net
                            }
                    except Exception as e:
                        logger.error(f"아침 모드 DB 조회 실패: {e}")
            return {}

        import numpy as np
        
        closes = np.array([c["close"] for c in candles] + [current_price], dtype=float)
        highs = np.array([c["high"] for c in candles] + [current_price], dtype=float)
        lows = np.array([c["low"] for c in candles] + [current_price], dtype=float)
        
        # 1. 이동평균 (SMA) 계산
        ma20 = float(np.mean(closes[-20:]))
        prev_ma20 = float(np.mean(closes[-21:-1]))
        ma60 = float(np.mean(closes[-60:]))
        prev_ma60 = float(np.mean(closes[-61:-1]))
        
        # 2. 볼린저 밴드 (20, 2std)
        recent_20 = closes[-20:]
        std_20 = np.std(recent_20)
        bb_upper = ma20 + (2.0 * std_20)
        bb_lower = ma20 - (2.0 * std_20)
        
        # 3. ATR (14) and ATR MA (20)
        tr = np.zeros(len(closes) - 1)
        for i in range(len(closes) - 1):
            h_l = highs[i+1] - lows[i+1]
            h_pc = abs(highs[i+1] - closes[i])
            l_pc = abs(lows[i+1] - closes[i])
            tr[i] = max(h_l, h_pc, l_pc)
        atr = float(np.mean(tr[-14:]))
        atr_ma = float(np.mean(tr[-20:]))
        
        # 4. MACD (Config values applied)
        fast_pd = getattr(config, "MACD_FAST", 5)
        slow_pd = getattr(config, "MACD_SLOW", 35)
        sig_pd = getattr(config, "MACD_SIGNAL", 9)
        
        def calculate_ema(data, period):
            alpha = 2.0 / (period + 1.0)
            ema = np.zeros_like(data)
            ema[0] = data[0]
            for i in range(1, len(data)):
                ema[i] = data[i] * alpha + ema[i-1] * (1.0 - alpha)
            return ema
            
        ema_fast = calculate_ema(closes, fast_pd)
        ema_slow = calculate_ema(closes, slow_pd)
        macd_line = ema_fast - ema_slow
        macd_signal = calculate_ema(macd_line, sig_pd)
        macd_hist = macd_line - macd_signal
        
        # 미결제약정 변동 (OI Change) 계산: 현재 진행 중인 캔들의 OI - 직전 완성된 캔들의 OI
        oi_change = 0
        if self._current_candle and len(candles) > 0:
            current_oi = self._current_candle.get("open_interest", 0)
            prev_oi = candles[-1].get("open_interest", 0)
            if current_oi > 0 and prev_oi > 0:
                oi_change = current_oi - prev_oi
                
        return {
            "ma20": ma20,
            "prev_ma20": prev_ma20,
            "ma60": ma60,
            "prev_ma60": prev_ma60,
            "macd": float(macd_line[-1]),
            "prev_macd": float(macd_line[-2]),
            "macd_signal": float(macd_signal[-1]),
            "prev_macd_signal": float(macd_signal[-2]),
            "macd_hist": float(macd_hist[-1]),
            "prev_macd_hist": float(macd_hist[-2]),
            "atr": atr,
            "atr_ma": atr_ma,
            "prev_close": float(closes[-2]),
            "current_price": current_price,
            "bb_upper": bb_upper,
            "bb_lower": bb_lower,
            "oi_change": oi_change,
            "option_call_net": self._latest_option_call_net,
            "option_put_net": self._latest_option_put_net
        }

    async def _process_trading_logic(self, current_price: float):
        """전체 규칙 기반 엔진 및 리스크 관리가 연계된 신호 판별 및 매매 처리"""
        # 0. 장 마감 직전 강제 청산 및 진입 제한 체크
        if await self._check_and_force_close_at_session_end():
            return

        # 1. Regime Engine -> 시장 상태 검출
        if not self.regime_engine:
            return
            
        regime_state = self.regime_engine.detect(current_price, self._history_candles)
        
        # 레짐 변경 시 또는 1분 단위(60초) 주기 만료 시 MariaDB에 레짐 상태 적재
        now = datetime.now()
        is_regime_changed = regime_state["regime"] != self._last_saved_regime
        is_interval_expired = (now - self._last_regime_save_time).total_seconds() >= 60
        
        if is_regime_changed or is_interval_expired:
            try:
                # DB 저장용 딕셔너리 정돈 (detected_at 필드는 DB default 적용)
                db_regime = regime_state.copy()
                if "detected_at" in db_regime:
                    del db_regime["detected_at"]
                    
                self.db.save_regime_state(db_regime)
                self._last_saved_regime = regime_state["regime"]
                self._last_regime_save_time = now
                logger.info(f"시장 레짐 상태 DB 적재 완료 (레짐: {self._last_saved_regime}, 사유: {'변경감지' if is_regime_changed else '1분주기'})")
            except Exception as e:
                logger.error(f"시장 레짐 상태 DB 적재 실패: {e}")
        
        # 횡보장인 경우 신규 매매 원천 차단
        if not regime_state["signal_allowed"]:
            if is_regime_changed:
                logger.info(f"[{regime_state['regime'].upper()}] 시장 진입으로 신호 탐색을 일시 중단합니다 (안전 모드).")
            return
            
        # 2. 지표 계산 및 수급 방향성 필터링
        indicators = self._calculate_indicators(self._history_candles, current_price)
        if not indicators:
            logger.debug(f"지표 계산 실패: 과거 캔들 데이터 부족 ({len(self._history_candles)}/60)")
            return
            
        # 최신 ATR 값 갱신
        self._latest_atr = indicators.get("atr", self._latest_atr)
            
        # Volatility Engine 업데이트 (ATR / ATR MA 전달)
        if self.volatility_engine:
            atr_val = indicators.get("atr", 1.0)
            atr_ma_val = indicators.get("atr_ma", 1.0)
            self.volatility_engine.update_indicators(atr_val, atr_ma_val)
        
        # Performance Engine 업데이트 (최근 거래 기록 가져오기 - 5분 주기로 제약하여 DB 부하 및 로그 유입량 차단)
        if self.performance_engine:
            now = datetime.now()
            if (now - self._last_perf_update_time).total_seconds() >= 300:
                try:
                    recent_trades = self.db.get_recent_trades(limit=50)
                    self.performance_engine.update_trades_history(recent_trades)
                    self._last_perf_update_time = now
                except Exception as e:
                    logger.error(f"Performance Engine 거래 기록 업데이트 실패: {e}")
            
        flow_direction = "NEUTRAL"
        foreign_zscore = 0.0
        
        if self.foreign_flow_engine:
            # 가격 변동량 및 미결제약정 변동량 산출
            prev_close = indicators.get("prev_close", current_price)
            price_change = current_price - prev_close
            
            oi_change = 0
            if len(self._history_candles) >= 2:
                curr_oi = self._history_candles[-1].get("open_interest", 0)
                prev_oi = self._history_candles[-2].get("open_interest", 0)
                oi_change = curr_oi - prev_oi if curr_oi > 0 and prev_oi > 0 else 0
                
            flow_direction = self.foreign_flow_engine.get_flow_direction(price_change, oi_change)
            flow_data = self.foreign_flow_engine.get_latest_flow()
            foreign_zscore = flow_data.get("foreign_zscore", 0.0)
            
        # 3단계 게이트 기반 최종 신호 생성
        signal = self.signal_engine.generate(
            self.current_code, 
            regime_state["regime"], 
            flow_direction, 
            foreign_zscore, 
            indicators
        )
        # Regime의 size_multiplier를 신호에 추가 (Risk Engine에서 사용)
        signal["size_multiplier"] = regime_state.get("size_multiplier", 1.0)
        
        if signal["direction"] == "HOLD":
            return

        # ── 진입 신호 발생 시 실시간 잔고(예수금) 최종 동기화 (적응형 사이징용 최신 자본금 갱신) ──
        logger.info("매매 진입 신호 감지. 실시간 계좌 잔고를 조회하여 최신 평가자산(예수금)을 갱신합니다.")
        try:
            await self._sync_positions_with_kis()
        except Exception as e:
            logger.error(f"주문 직전 실시간 잔고 동기화 실패: {e}")

        # ── 반대 신호 감지 시 기존 포지션 시장가 청산 (스위칭 청산) ──
        # 게이트 필터링을 거쳐 신뢰할 수 있는 최종 신호가 BUY/SELL일 때만 안전하게 청산을 수행합니다.
        if self.active_positions:
            existing_pos = self.active_positions[0]
            existing_side = existing_pos["side"]
            
            if (existing_side == "LONG" and signal["direction"] == "SELL") or \
               (existing_side == "SHORT" and signal["direction"] == "BUY"):
                logger.warning(
                    f"[SWITCH] 반대 신호 감지! ({existing_side} -> {signal['direction']}) "
                    f"기존 포지션({existing_pos['position_id']}) 즉시 시장가 청산 및 스위칭 대기"
                )
                await self._execute_emergency_close(existing_pos, "반대 신호 발생으로 인한 스위칭 청산")
                
        # 4. Volatility Engine -> 변동성 분석 및 손절가/배수 산출
        vol_state = self.volatility_engine.analyze()
        
        # 5. Performance Engine -> 최근 거래 성과 기반 사이징 가중치 산출
        current_cap = self.execution_engine.current_capital if self.execution_engine else 100_000_000.0
        perf_metrics = self.performance_engine.calculate_multiplier(current_cap)
        self.db.save_performance_metrics(perf_metrics)
        
        # 6. Risk Engine & Adaptive Sizing -> 리스크 검증 및 최종 계약 수 결정
        decision = self.risk_engine.validate(
            signal, 
            vol_state, 
            perf_metrics, 
            self.active_positions,
            total_capital=current_cap,
            current_price=current_price
        )
        
        # 7. Execution Engine -> 최종 주문 실행 및 데이터베이스 기록
        if decision["approved"] and decision["contracts"] > 0:
            logger.info(f"주문 승인! 주문 집행 시도: {signal['direction']} {decision['contracts']}계약")
            order_res = await self.execution_engine.execute_order(
                code=self.current_code,
                direction=signal["direction"],
                qty=decision["contracts"],
                price=current_price,
                stop_loss=decision["stop_loss"],
                take_profit=decision["take_profit"]
            )
            
            # 주문 ID 생성 (실패 시에도 이력 추적을 위해 임시 ID 부여)
            order_id = order_res.get("order_id") if order_res["success"] else f"O_ERR_{int(datetime.now().timestamp())}"
            
            # DB에 주문 이력 저장 (성공/실패 무관 기록)
            order_db_data = {
                "order_id": order_id,
                "futures_code": self.current_code,
                "order_side": signal["direction"],
                "order_qty": decision["contracts"],
                "order_price": current_price,
                "order_type": "LIMIT",
                "status": "FILLED" if order_res["success"] else "REJECTED",
                "result_msg": "체결완료" if order_res["success"] else f"주문 실패: {order_res.get('error')}"
            }
            try:
                self.db.save_order(order_db_data)
                logger.info(f"주문 이력 DB 저장 완료 (주문 ID: {order_id}, 상태: {order_db_data['status']})")
            except Exception as e:
                logger.error(f"주문 이력 DB 저장 실패: {e}")
                
            if order_res["success"]:
                # 리스크 엔진이 계산한 손절가/익절가 임시 저장 (잔고 동기화 시 매핑 목적)
                self._pending_sl_tp[self.current_code] = (decision["stop_loss"], decision["take_profit"])
                # 잔고 재동기화 트리거
                await self._sync_positions_with_kis()
                
                # 텔레그램 알림 전송
                self.send_telegram(
                    f"[ORDER] [자동 주문 체결] 진입 완료\n"
                    f"- 종목: {self.current_code}\n"
                    f"- 방향: {signal['direction']}\n"
                    f"- 수량: {decision['contracts']}계약\n"
                    f"- 진입가: {current_price:,.2f}\n"
                    f"- 손절가: {decision['stop_loss']:,.2f}\n"
                    f"- 익절가: {decision['take_profit']:,.2f}"
                )

    def _send_health_to_sheets(self, session: str):
        """현재 시스템 상태 스냅샷을 Google Sheets의 BotHealth에 기록"""
        import psutil
        process = psutil.Process()
        cpu_usage = psutil.cpu_percent()
        ram_usage_mb = process.memory_info().rss / (1024 * 1024)
        
        if session == "pre_market":
            latest_price = f"PreMarket (Basis: {self.latest_temp_basis:+.2f} Pt)"
        else:
            latest_price = self.ws_manager.get_latest_price(self._to_kis_code(self.current_code)) or "No Data"
            
        metrics = {
            "BotStatus": "RUNNING",
            "CurrentSession": session,
            "TargetCode": self.current_code,
            "CpuPercent": f"{cpu_usage}%",
            "MemoryUsageMB": f"{ram_usage_mb:.1f} MB",
            "ActivePositionsCount": len(self.active_positions),
            "LatestPrice": latest_price
        }
        self.sheets.update_bot_health(metrics)
        logger.info("Google Sheets에 헬스 메트릭을 기록했습니다.")

    def stop(self):
        """수퍼바이저 기동 정지"""
        self.is_running = False
        # 마지막 진행 중이던 캔들 저장 (데이터 유실 방지)
        self._flush_candle()
        self.ws_manager.close()
        logger.info("Trading Supervisor가 정지되었습니다.")
