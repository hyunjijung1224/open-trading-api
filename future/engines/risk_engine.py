import logging
from datetime import datetime
from typing import Dict, Any, List, Optional

logger = logging.getLogger("RiskEngine")

class RiskEngine:
    """
    트레이딩 시스템의 최종 가디언 (Risk Engine)
    - 일간/주간 손실 한도 검증
    - 최대 보유 계약수(5계약) 검증
    - 포지션 사이징 공식 적용: 
      최종 계약 수 = 기본수량(1회 리스크 한도 대비 ATR 손절액 비율) * 레짐 가중치 * 변동성 가중치 * 성과 가중치
    - 변동성 기반 동적 손절가(Stop Loss) 및 익절가(Take Profit) 결정
    """
    def __init__(self, 
                 total_capital: int = 100_000_000, 
                 single_trade_risk: float = 0.01,  # 1회 최대 리스크 1%
                 daily_loss_limit: float = 0.02,   # 일 최대 손실 2%
                 max_contracts: int = 5):
        self.total_capital = total_capital
        self.single_trade_risk = single_trade_risk
        self.daily_loss_limit = daily_loss_limit
        self.max_contracts = max_contracts
        
        # 일간 누적 손실액 추적용 캐시
        self.daily_pnl = 0.0

    def set_daily_pnl(self, current_daily_pnl: float):
        """오늘 마감된 누적 손익 셋팅"""
        self.daily_pnl = current_daily_pnl

    def validate(self, 
                 signal: Dict[str, Any], 
                 vol_state: Dict[str, Any], 
                 perf_metrics: Dict[str, Any], 
                 active_positions: List[Dict[str, Any]],
                 total_capital: Optional[float] = None,
                 current_price: Optional[float] = None) -> Dict[str, Any]:
        """
        신호에 대해 최종 리스크 검증을 진행하고 거래 승인 여부 및 최종 수량, 손절/익절가 결정
        """
        direction = signal["direction"]
        if direction == "HOLD":
            return {"approved": False, "contracts": 0, "stop_loss": 0.0, "take_profit": 0.0, "reason": "신호 없음(HOLD)"}

        # 실시간 평가자산(total_capital) 적용 (지정되지 않았으면 초기값 폴백)
        cap = total_capital if total_capital is not None else self.total_capital
        atr = vol_state["atr"]

        # ── 예수금 변동에 따른 최대 계약 수 동적 계산 ──
        # 기준 자금 비율 (예: 초기 1억 / 최대 5계약 = 2,000만 원당 1계약)
        capital_per_contract = self.total_capital / max(1, self.max_contracts)
        calculated_max_contracts = max(1, int(cap / capital_per_contract))

        # 1. 일간 누적 손실 한도 검사
        limit_amount = cap * self.daily_loss_limit
        if self.daily_pnl <= -limit_amount:
            logger.warning(f"일간 손실 한도 초과! 거래 거절: 누적 손익 = {self.daily_pnl:+,}원 (한도 = -{limit_amount:,}원)")
            return {
                "approved": False, "contracts": 0, "stop_loss": 0.0, "take_profit": 0.0, 
                "reason": "일간 누적 손실 한도 초과"
            }

        # 2. 현재 보유 계약 수 체크 및 중복 진입 방지
        current_contracts = sum(pos["quantity"] for pos in active_positions)
        if current_contracts >= calculated_max_contracts:
            logger.warning(f"최대 계약 수 초과! 거래 거절: 보유 중 = {current_contracts}계약 (최대(동적) = {calculated_max_contracts}계약, 자산 = {cap:,}원)")
            return {
                "approved": False, "contracts": 0, "stop_loss": 0.0, "take_profit": 0.0, 
                "reason": f"동적 최대 계약 수 한도({calculated_max_contracts}계약) 도달"
            }

        # 이미 포지션이 있고 방향이 동일하다면 피라미딩(추가 매수) 조건 검증
        is_pyramiding = False
        if active_positions:
            existing_pos = active_positions[0]
            existing_side = existing_pos["side"]
            if existing_side == direction:
                # 1) 현재가 정보 및 버퍼 확인
                cur_p = current_price if current_price is not None else signal.get("score", atr)
                avg_price = existing_pos["avg_price"]
                
                # 2) 수익 중인지 판별 (불타기만 허용, 물타기 금지)
                is_profitable = False
                if existing_side == "LONG":
                    is_profitable = cur_p > avg_price
                elif existing_side == "SHORT":
                    is_profitable = cur_p < avg_price
                    
                if not is_profitable:
                    logger.info(f"동일 방향 포지션 보유 중이나 현재 손실 상태(물타기 금지): 평단={avg_price:.2f}, 현재가={cur_p:.2f}")
                    return {
                        "approved": False, "contracts": 0, "stop_loss": 0.0, "take_profit": 0.0,
                        "reason": "물타기 금지 (손실 상태 추가 진입 거부)"
                    }
                
                # 3) 1.5 * ATR 이상의 수익 버퍼 확보 여부 검증
                profit_buffer = abs(cur_p - avg_price)
                required_buffer = atr * 1.5
                
                if profit_buffer < required_buffer:
                    logger.info(f"동일 방향 포지션 보유 중이나 수익폭 부족: 평단={avg_price:.2f}, 현재가={cur_p:.2f}, 확보수익={profit_buffer:.2f} (필요 버퍼={required_buffer:.2f})")
                    return {
                        "approved": False, "contracts": 0, "stop_loss": 0.0, "take_profit": 0.0,
                        "reason": f"피라미딩 조건 미달 (수익 버퍼 부족: {profit_buffer:.2f} < {required_buffer:.2f})"
                    }
                
                is_pyramiding = True
                logger.info(f"[PYRAMID] 피라미딩 조건 충족! 추가 매수 승인 (평단={avg_price:.2f}, 현재가={cur_p:.2f}, 확보수익={profit_buffer:.2f} >= 버퍼={required_buffer:.2f})")

        # 3. 자금 관리 기반 적응형 포지션 사이징 적용
        if is_pyramiding:
            # 피라미딩 시에는 점진적 리스크 관리를 위해 무조건 1계약 단위 추가 매수
            final_contracts = 1
            logger.info("피라미딩 추가 진입 수량: 1계약 승인")
        else:
            # 1회 최대 감수할 손실액 (실시간 자산 기준 계산)
            risk_money = cap * self.single_trade_risk  # 예: 자산의 1%
            
            # KOSPI200 선물/미니선물 1포인트당 가치 (일반선물 25만원, 미니선물 5만원)
            futures_code = signal.get("futures_code", "")
            point_value = 50000.0 if "105" in futures_code else 250000.0
            
            # 1계약 당 감수할 손실액 = 고정 손절 Pt가 설정되어 있으면 고정값 사용, 없으면 ATR * 2.0 * 1포인트 가치
            from config import config
            fixed_sl = getattr(config, "FIXED_STOP_LOSS_PTS", 0.0)
            if fixed_sl > 0:
                risk_per_contract = fixed_sl * point_value
            else:
                risk_per_contract = atr * 2.0 * point_value
            
            # 기본 진입 수량 산출
            if risk_per_contract > 0:
                base_contracts = int(risk_money / risk_per_contract)
            else:
                base_contracts = 1
            base_contracts = max(1, base_contracts)

            # 각 엔진의 배수 가져오기
            regime_mult = signal.get("strength", 1.0)  # 신호 강도 또는 레짐 배수
            if "size_multiplier" in signal:
                regime_mult = signal["size_multiplier"]
                
            vol_mult = vol_state.get("size_multiplier", 1.0)
            perf_mult = perf_metrics.get("size_multiplier", 1.0)

            # 적응형 최종 수량 계산
            suggested_contracts = int(base_contracts * regime_mult * vol_mult * perf_mult)
            # 최소 1계약 보장, 최대 계약수 한도에서 기존 계약수를 뺀 만큼만 허용
            allowed_contracts = calculated_max_contracts - current_contracts
            final_contracts = min(suggested_contracts, allowed_contracts)
            final_contracts = max(1, final_contracts)

        # 4. 동적 손절가(Stop Loss) 및 익절가(Take Profit) 계산
        cur_p = current_price if current_price is not None else signal.get("score", atr)
        
        from config import config
        fixed_sl = getattr(config, "FIXED_STOP_LOSS_PTS", 0.0)
        fixed_tp = getattr(config, "FIXED_TAKE_PROFIT_PTS", 0.0)
        
        if fixed_sl > 0:
            sl_delta = fixed_sl
        else:
            sl_delta = atr * vol_state["stop_loss_multiplier"]
            
        if fixed_tp > 0:
            tp_delta = fixed_tp
        else:
            tp_delta = atr * vol_state["take_profit_multiplier"]

        # 실제 진입가 기준의 절대 손절/익절가 산출
        if direction == "BUY":
            sl_price = cur_p - sl_delta
            tp_price = cur_p + tp_delta
        else:
            sl_price = cur_p + sl_delta
            tp_price = cur_p - tp_delta

        logger.info(f"리스크 검증 승인: 피라미딩여부={is_pyramiding}, 최종 {final_contracts}계약 승인 (손절가={sl_price:.2f}, 익절가={tp_price:.2f})")
        
        return {
            "approved": True,
            "contracts": final_contracts,
            "stop_loss": sl_price,
            "take_profit": tp_price,
            "stop_loss_delta": sl_delta,
            "take_profit_delta": tp_delta,
            "reason": "리스크 필터 통과"
        }
