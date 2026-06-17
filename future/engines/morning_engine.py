import logging
from datetime import datetime, time as datetime_time
from typing import Dict, Any, Optional, List

from config import config

logger = logging.getLogger("MorningEngine")


class MorningEngine:
    """
    모닝 엔진 — 08:45 ~ 09:30 구간 전용 (장 초반 45분)

    5개 전략 기반 bull_score / bear_score (0~10) 산출, 7점 이상 진입

    전략 목록:
      1. Overnight Score    — 야간 시장(CME/S&P/NASDAQ/환율/OI) 기반 사전 점수
      2. Gap Analysis       — 시초 갭 방향 추종 (갭 > 0.5% → LONG, < -0.5% → SHORT)
      3. Opening Range Breakout (ORB) — 08:45~08:50 범위 돌파 매매
      4. Foreign Open Attack — 외국인 첫 진입 분석 (CVD + OI + 베이시스)
      5. Gap Fill            — 갭 과대 시 역방향 (갭 > 1.5% → SHORT)

    수명 주기:
      1. set_overnight_context() — 장 시작 전(08:00~08:45) 야간 데이터 주입
      2. activate()             — 08:45 도달 시 활성화
      3. evaluate()             — 매 틱마다 호출, 5개 전략 점수 합산
      4. deactivate()           — 09:30 도달 시 비활성화, handoff 반환

    확장 포인트:
      - 전략별 가중치 조정
      - 추가 전략 플러그인 (Strategy #6, #7...)
      - 실시간 데이터 소스 확장
    """

    def __init__(self, db=None):
        self.db = db

        # ── 활성화 상태 ──
        self.is_active: bool = False

        # ── 야간 컨텍스트 (장 시작 전 주입) ──
        self.overnight_context: Dict[str, Any] = {
            "night_futures_return": 0.0,   # 야간 미니선물 수익률 (%)
            "sp500_return": 0.0,           # S&P500 전일 수익률 (%)
            "nasdaq_return": 0.0,          # NASDAQ 전일 수익률 (%)
            "usdkrw_change": 0.0,          # USD/KRW 변동률 (%), 하락 = 원화 강세
            "foreign_net_buy": 0,          # 전일 외국인 선물 순매수 (계약)
            "oi_change": 0,               # 전일 미결제약정 변동
            "prev_close": 0.0,            # 전일 종가
        }

        # ── Gap 데이터 (08:45 시초가 수신 시 계산) ──
        self.today_open: float = 0.0
        self.gap_pct: float = 0.0         # (today_open - prev_close) / prev_close * 100

        # ── Opening Range (08:45~08:50) ──
        self.orb_high: float = 0.0
        self.orb_low: float = float("inf")
        self.orb_minutes_collected: int = 0
        self.orb_complete: bool = False

        # ── 실시간 데이터 캐시 ──
        self.latest_cvd: float = 0.0
        self.latest_oi: int = 0
        self.latest_basis: float = 0.0
        self.latest_exec_strength: float = 100.0
        self.latest_imbalance: float = 1.0

        # ── 포지션 추적 ──
        self.entry_made: bool = False
        self.position_side: Optional[str] = None
        self.position_entry_price: float = 0.0
        self.position_qty: int = 0

        # ── 점수 시스템 ──
        self.bull_score: int = 0
        self.bear_score: int = 0
        self.score_threshold: int = 7      # 7점 이상 진입

        # ── 설정 ──
        self.session_start = datetime_time(8, 45)
        self.session_end = datetime_time(9, 30)  # 09:30 종료
        self.score_threshold = getattr(config, "MORNING_SCORE_THRESHOLD", 7)
        self.gap_strong_pct = getattr(config, "MORNING_GAP_STRONG_PCT", 1.0)
        self.gap_medium_pct = getattr(config, "MORNING_GAP_MEDIUM_PCT", 0.5)
        self.gap_fill_pct = getattr(config, "MORNING_GAP_FILL_PCT", 1.5)

        logger.info("MorningEngine 초기화 완료")

    # ==================================================================
    # 사전 설정 (장 시작 전 호출)
    # ==================================================================

    def set_overnight_context(self, context: Dict[str, Any]):
        """
        장 시작 전(08:00~08:45) 야간 데이터 주입
        telegram_agent 또는 별도 수집 모듈에서 호출
        """
        self.overnight_context.update(context)
        logger.info(
            f"[MORNING] 야간 컨텍스트 로드: "
            f"NightFutures={self.overnight_context['night_futures_return']:+.2f}%, "
            f"S&P500={self.overnight_context['sp500_return']:+.2f}%, "
            f"NASDAQ={self.overnight_context['nasdaq_return']:+.2f}%, "
            f"USD/KRW={self.overnight_context['usdkrw_change']:+.2f}%, "
            f"ForeignNet={self.overnight_context['foreign_net_buy']:+d}, "
            f"OI_Change={self.overnight_context['oi_change']:+d}"
        )

    # ==================================================================
    # 수명 주기
    # ==================================================================

    def activate(self, current_price: float, prev_close: float = 0.0) -> bool:
        """
        모닝 세션 활성화 — 08:45 도달 시 호출
        Returns: True = 활성화 성공, False = 비활성
        """
        if self.is_active:
            return True

        now = datetime.now()
        if not (self.session_start <= now.time() <= self.session_end):
            logger.debug(f"모닝 세션 시간 외 ({now.time()}), 활성화 안함")
            return False

        # 오늘 매매 이력 체크
        if self._has_today_entry():
            logger.info("[MORNING] 오늘 이미 매매 이력 있음, 모닝 세션 비활성")
            return False

        # 전일 종가 설정
        if prev_close > 0:
            self.overnight_context["prev_close"] = prev_close

        # 시초가 설정
        self.today_open = current_price
        prev = self.overnight_context["prev_close"]
        if prev > 0:
            self.gap_pct = ((current_price - prev) / prev) * 100.0

        # ORB 초기화
        self.orb_high = current_price
        self.orb_low = current_price
        self.orb_minutes_collected = 0
        self.orb_complete = False

        # 활성화
        self.is_active = True
        self.bull_score = 0
        self.bear_score = 0

        logger.info(
            f"[MORNING] 모닝 엔진 활성화! "
            f"현재가={current_price:.2f}, 전일종가={prev:.2f}, "
            f"갭={self.gap_pct:+.2f}%"
        )
        return True

    def deactivate(self) -> Dict[str, Any]:
        """
        모닝 세션 비활성화 — 09:30 도달 시 호출
        Returns: handoff 정보
        """
        if not self.is_active:
            return {"was_active": False}

        handoff_info = {
            "was_active": True,
            "entry_made": self.entry_made,
            "position_side": self.position_side,
            "position_qty": self.position_qty,
            "position_entry_price": self.position_entry_price,
            "final_bull_score": self.bull_score,
            "final_bear_score": self.bear_score,
            "gap_pct": self.gap_pct,
        }

        logger.info(
            f"[MORNING] 모닝 세션 종료 → 정상 스캘핑 handoff "
            f"(진입여부={self.entry_made}, "
            f"포지션={self.position_side} {self.position_qty}계약 @ {self.position_entry_price:.2f})"
        )

        # 상태 리셋
        self.is_active = False
        self.entry_made = False
        self.position_side = None
        self.position_qty = 0
        self.position_entry_price = 0.0
        self.bull_score = 0
        self.bear_score = 0

        return handoff_info

    # ==================================================================
    # 실시간 데이터 업데이트
    # ==================================================================

    def update_realtime(self, indicators: Dict[str, Any]):
        """매 틱마다 호출 — 실시간 데이터 캐시 업데이트"""
        self.latest_cvd = indicators.get("delta_30s", 0.0)
        self.latest_oi = indicators.get("oi_change", 0)
        self.latest_basis = indicators.get("basis", 0.0)
        self.latest_exec_strength = indicators.get("exec_strength", 100.0)
        self.latest_imbalance = indicators.get("imbalance", 1.0)

    def update_orb(self, current_price: float):
        """08:45~08:50 구간에서 ORB 범위 업데이트"""
        if self.orb_complete:
            return

        now = datetime.now()
        orb_end = datetime_time(8, 50)

        if now.time() < orb_end:
            self.orb_high = max(self.orb_high, current_price)
            self.orb_low = min(self.orb_low, current_price)
            self.orb_minutes_collected += 1
            logger.debug(
                f"[ORB] 범위 업데이트: {self.orb_low:.2f} ~ {self.orb_high:.2f} "
                f"({self.orb_minutes_collected}분)"
            )
        else:
            if not self.orb_complete:
                self.orb_complete = True
                logger.info(
                    f"[ORB] 5분 범위 확정: {self.orb_low:.2f} ~ {self.orb_high:.2f} "
                    f"(범위: {self.orb_high - self.orb_low:.2f}포인트)"
                )

    # ==================================================================
    # 신호 생성
    # ==================================================================

    def evaluate(self, current_price: float, indicators: Dict[str, Any]) -> Dict[str, Any]:
        """
        매 틱마다 호출 — 5개 전략 점수 합산

        Returns:
            {
                "direction": "BUY" | "SELL" | "HOLD",
                "strength": float,
                "reasons": list[str],
                "bull_score": int,
                "bear_score": int,
                "strategy_scores": dict,
            }
        """
        hold_result = {
            "direction": "HOLD",
            "strength": 0.0,
            "reasons": [],
            "bull_score": self.bull_score,
            "bear_score": self.bear_score,
            "strategy_scores": {},
        }

        if not self.is_active:
            hold_result["reasons"].append("모닝 엔진 비활성")
            return hold_result

        if self.entry_made:
            hold_result["reasons"].append(f"이미 진입 완료 ({self.position_side})")
            return hold_result

        # 실시간 데이터 업데이트
        self.update_realtime(indicators)
        self.update_orb(current_price)

        # ── 5개 전략 점수 계산 ──
        strategy_scores = {}
        bull_total = 0
        bear_total = 0

        # Strategy #1: Overnight Score
        s1_bull, s1_bear, s1_detail = self._score_overnight()
        strategy_scores["overnight"] = {"bull": s1_bull, "bear": s1_bear, "detail": s1_detail}
        bull_total += s1_bull
        bear_total += s1_bear

        # Strategy #2: Gap Analysis
        s2_bull, s2_bear, s2_detail = self._score_gap_analysis()
        strategy_scores["gap_analysis"] = {"bull": s2_bull, "bear": s2_bear, "detail": s2_detail}
        bull_total += s2_bull
        bear_total += s2_bear

        # Strategy #3: Opening Range Breakout (ORB)
        s3_bull, s3_bear, s3_detail = self._score_orb(current_price)
        strategy_scores["orb"] = {"bull": s3_bull, "bear": s3_bear, "detail": s3_detail}
        bull_total += s3_bull
        bear_total += s3_bear

        # Strategy #4: Foreign Open Attack
        s4_bull, s4_bear, s4_detail = self._score_foreign_attack()
        strategy_scores["foreign_attack"] = {"bull": s4_bull, "bear": s4_bear, "detail": s4_detail}
        bull_total += s4_bull
        bear_total += s4_bear

        # Strategy #5: Gap Fill
        s5_bull, s5_bear, s5_detail = self._score_gap_fill()
        strategy_scores["gap_fill"] = {"bull": s5_bull, "bear": s5_bear, "detail": s5_detail}
        bull_total += s5_bull
        bear_total += s5_bear

        # 최종 점수 업데이트
        self.bull_score = bull_total
        self.bear_score = bear_total

        # ── 판정 ──
        reasons = []

        if bull_total >= self.score_threshold and bull_total > bear_total:
            direction = "BUY"
            strength = min(1.0, bull_total / 10.0)
            reasons.append(f"[MORNING] bull_score={bull_total} >= {self.score_threshold} → LONG")
        elif bear_total >= self.score_threshold and bear_total > bull_total:
            direction = "SELL"
            strength = min(1.0, bear_total / 10.0)
            reasons.append(f"[MORNING] bear_score={bear_total} >= {self.score_threshold} → SHORT")
        else:
            direction = "HOLD"
            strength = 0.0
            reasons.append(
                f"[MORNING] 점수 미충족 (bull={bull_total}, bear={bear_total}, "
                f"threshold={self.score_threshold})"
            )

        if direction != "HOLD":
            logger.info(f"[MORNING] {direction} 신호! bull={bull_total}, bear={bear_total}")

        return {
            "direction": direction,
            "strength": strength,
            "reasons": reasons,
            "bull_score": bull_total,
            "bear_score": bear_total,
            "strategy_scores": strategy_scores,
        }

    # ==================================================================
    # Strategy #1: Overnight Score
    # ==================================================================

    def _score_overnight(self) -> tuple:
        """
        야간 시장 데이터 기반 사전 점수 (08:45 전에 이미 계산됨)
        최대 4점 (bull 또는 bear)
        """
        bull = 0
        bear = 0
        details = []

        ctx = self.overnight_context

        # 야간 미니선물 수익률
        nf = ctx.get("night_futures_return", 0.0)
        if nf > 0.3:
            bull += 1
            details.append(f"NightFutures={nf:+.2f}%↑")
        elif nf < -0.3:
            bear += 1
            details.append(f"NightFutures={nf:+.2f}%↓")

        # S&P500
        sp = ctx.get("sp500_return", 0.0)
        if sp > 0.5:
            bull += 1
            details.append(f"S&P500={sp:+.2f}%↑")
        elif sp < -0.5:
            bear += 1
            details.append(f"S&P500={sp:+.2f}%↓")

        # NASDAQ
        nq = ctx.get("nasdaq_return", 0.0)
        if nq > 0.8:
            bull += 1
            details.append(f"NASDAQ={nq:+.2f}%↑")
        elif nq < -0.8:
            bear += 1
            details.append(f"NASDAQ={nq:+.2f}%↓")

        # USD/KRW (하락 = 원화 강세 = Bull)
        fx = ctx.get("usdkrw_change", 0.0)
        if fx < -0.2:
            bull += 1
            details.append(f"USD/KRW={fx:+.2f}%↓(원화강세)")
        elif fx > 0.2:
            bear += 1
            details.append(f"USD/KRW={fx:+.2f}%↓(원화약세)")

        return bull, bear, ", ".join(details) if details else " 야간 데이터 없음"

    # ==================================================================
    # Strategy #2: Gap Analysis
    # ==================================================================

    def _score_gap_analysis(self) -> tuple:
        """
        시초 갭 방향 추종 (갭 > 0.5% → LONG, < -0.5% → SHORT)
        최대 3점
        """
        bull = 0
        bear = 0
        details = []

        gap = self.gap_pct
        strong = self.gap_strong_pct
        medium = self.gap_medium_pct

        if gap > strong:
            bull += 3
            details.append(f"Gap={gap:+.2f}% (강한 갭업)")
        elif gap > medium:
            bull += 2
            details.append(f"Gap={gap:+.2f}% (갭업)")
        elif gap > 0.2:
            bull += 1
            details.append(f"Gap={gap:+.2f}% (약한 갭업)")
        elif gap < -strong:
            bear += 3
            details.append(f"Gap={gap:+.2f}% (강한 갭다운)")
        elif gap < -medium:
            bear += 2
            details.append(f"Gap={gap:+.2f}% (갭다운)")
        elif gap < -0.2:
            bear += 1
            details.append(f"Gap={gap:+.2f}% (약한 갭다운)")
        else:
            details.append(f"Gap={gap:+.2f}% (갭 없음)")

        return bull, bear, ", ".join(details)

    # ==================================================================
    # Strategy #3: Opening Range Breakout (ORB)
    # ==================================================================

    def _score_orb(self, current_price: float) -> tuple:
        """
        08:45~08:50 범위 돌파 매매
        - ORB 미확정: 대기 (0점)
        - ORB 확정 후 고점 돌파 + 체결강도 상승 → LONG
        - ORB 확정 후 저점 이탈 + 체결강도 하락 → SHORT
        최대 3점
        """
        bull = 0
        bear = 0
        details = []

        if not self.orb_complete:
            details.append(f"ORB 미확정 ({self.orb_minutes_collected}/5분)")
            return bull, bear, ", ".join(details)

        orb_range = self.orb_high - self.orb_low
        if orb_range <= 0:
            details.append("ORB 범위 0 (데이터 부족)")
            return bull, bear, ", ".join(details)

        # 고점 돌파
        if current_price > self.orb_high:
            if self.latest_exec_strength >= 110:
                bull += 3
                details.append(f"ORB 고점돌파 ({current_price:.2f} > {self.orb_high:.2f}) + 체결강도({self.latest_exec_strength:.0f})")
            elif self.latest_exec_strength >= 100:
                bull += 2
                details.append(f"ORB 고점돌파 (체결강도 보통)")
            else:
                bull += 1
                details.append(f"ORB 고점돌파 (체결강도 약함)")

        # 저점 이탈
        elif current_price < self.orb_low:
            if self.latest_exec_strength <= 90:
                bear += 3
                details.append(f"ORB 저점이탈 ({current_price:.2f} < {self.orb_low:.2f}) + 체결강도({self.latest_exec_strength:.0f})")
            elif self.latest_exec_strength <= 100:
                bear += 2
                details.append(f"ORB 저점이탈 (체결강도 보통)")
            else:
                bear += 1
                details.append(f"ORB 저점이탈 (체결강도 약함)")
        else:
            details.append(f"ORB 범위 내 ({self.orb_low:.2f} ~ {self.orb_high:.2f})")

        return bull, bear, ", ".join(details)

    # ==================================================================
    # Strategy #4: Foreign Open Attack
    # ==================================================================

    def _score_foreign_attack(self) -> tuple:
        """
        외국인 첫 진입 분석 (CVD + OI + 베이시스)
        - CVD 급증 + OI 증가 + 베이시스 상승 → LONG
        - CVD 감소 + OI 증가 + 베이시스 하락 → SHORT
        최대 3점
        """
        bull = 0
        bear = 0
        details = []

        cvd = self.latest_cvd
        oi = self.latest_oi
        basis = self.latest_basis

        # CVD 방향성
        cvd_bull = cvd > 50
        cvd_bear = cvd < -50

        # OI 증가 (신규 진입)
        oi_increasing = oi > 5

        # 베이시스 방향
        basis_rising = basis > 0.5
        basis_falling = basis < -0.5

        if cvd_bull and oi_increasing and basis_rising:
            bull += 3
            details.append(f"CVD({cvd:+.0f})↑ + OI({oi:+d})↑ + Basis({basis:+.2f})↑")
        elif cvd_bull and oi_increasing:
            bull += 2
            details.append(f"CVD({cvd:+.0f})↑ + OI({oi:+d})↑")
        elif cvd_bull or (oi_increasing and basis_rising):
            bull += 1
            details.append(f" 약한 Bull 신호")

        if cvd_bear and oi_increasing and basis_falling:
            bear += 3
            details.append(f"CVD({cvd:+.0f})↓ + OI({oi:+d})↑ + Basis({basis:+.2f})↓")
        elif cvd_bear and oi_increasing:
            bear += 2
            details.append(f"CVD({cvd:+.0f})↓ + OI({oi:+d})↑")
        elif cvd_bear or (oi_increasing and basis_falling):
            bear += 1
            details.append(f" 약한 Bear 신호")

        if not details:
            details.append("데이터 부족")

        return bull, bear, ", ".join(details)

    # ==================================================================
    # Strategy #5: Gap Fill
    # ==================================================================

    def _score_gap_fill(self) -> tuple:
        """
        갭 과대 시 역방향 (갭 > 1.5% → SHORT, 갭 < -1.5% → LONG)
        특히 +1.5% 이상 갭에서 효과적
        최대 2점
        """
        bull = 0
        bear = 0
        details = []

        gap = self.gap_pct
        fill_pct = self.gap_fill_pct

        # Gap Fill SHORT (갭업 과대)
        if gap > fill_pct + 0.5:
            bear += 2
            details.append(f"Gap={gap:+.2f}% ({fill_pct+0:.0f}%↑ 갭 Fill SHORT)")
        elif gap > fill_pct:
            bear += 1
            details.append(f"Gap={gap:+.2f}% ({fill_pct:.0f}%↑ 갭 Fill SHORT)")

        # Gap Fill LONG (갭다운 과대)
        elif gap < -(fill_pct + 0.5):
            bull += 2
            details.append(f"Gap={gap:+.2f}% ({fill_pct+0:.0f}%↓ 갭 Fill LONG)")
        elif gap < -fill_pct:
            bull += 1
            details.append(f"Gap={gap:+.2f}% ({fill_pct:.0f}%↓ 갭 Fill LONG)")
        else:
            details.append(f"Gap={gap:+.2f}% (갭 Fill 없음)")

        return bull, bear, ", ".join(details)

    # ==================================================================
    # 포지션 관리
    # ==================================================================

    def record_entry(self, side: str, qty: int, price: float):
        """진입 기록"""
        self.entry_made = True
        self.position_side = side
        self.position_qty = qty
        self.position_entry_price = price
        logger.info(f"[MORNING] 진입 기록: {side} {qty}계약 @ {price:.2f}")

    def record_exit(self):
        """청산 기록"""
        logger.info(
            f"[MORNING] 청산 기록: {self.position_side} {self.position_qty}계약"
        )
        self.entry_made = False
        self.position_side = None
        self.position_qty = 0
        self.position_entry_price = 0.0

    # ==================================================================
    # 내부 메서드
    # ==================================================================

    def _has_today_entry(self) -> bool:
        """오늘 모닝 세션에서 이미 진입했는지 확인"""
        if not self.db:
            return False

        now = datetime.now()
        today_start = datetime(now.year, now.month, now.day, 8, 45, 0)

        try:
            recent_trades = self.db.get_recent_trades(limit=10)
            for t in recent_trades:
                exit_time = t.get("exit_time")
                if isinstance(exit_time, str):
                    exit_time = datetime.strptime(exit_time, "%Y-%m-%d %H:%M:%S")
                if exit_time and exit_time >= today_start:
                    return True
        except Exception as e:
            logger.error(f"최근 거래 이력 조회 실패: {e}")
        return False
