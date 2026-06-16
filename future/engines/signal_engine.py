import logging
from datetime import datetime
from typing import Dict, Any, List
from config import config

logger = logging.getLogger("SignalEngine")

class SignalEngine:
    """
    3단계 게이트(Filter-Trigger) 매매 신호 생성 엔진
    - 1단계: 시장 국면 필터 (regime != ranging)
    - 2단계: 수급 검증 필터 (flow_direction, foreign_zscore)
    - 3단계: 가격 모멘텀 트리거 (MACD 크로스)
    - 가격 정보 다중공선성 제거 및 외국인/미결제약정 검증 타점 적용
    """
    def __init__(self, score_threshold: int = 0):
        # 3단계 게이트로 개편되어 기존 score_threshold는 사용되지 않으나 하위 호환성 유지
        self.score_threshold = score_threshold

    def generate(self, code: str, regime: str, flow_direction: str, foreign_zscore: float, indicators: Dict[str, Any]) -> Dict[str, Any]:
        """
        시장 국면, 수급 방향, 기술적 지표 트리거를 융합하여 최종 매수/매도/관망 신호 생성
        """
        # 아침 브리핑 모드인지 체크
        if indicators.get("is_morning_mode"):
            morning_dir = indicators["morning_direction"]
            morning_score = indicators["morning_score"]
            direction = "BUY" if morning_dir == "BUY" else "SELL"
            reasons = [f"아침 개장 직후 모닝브리핑 데이터 기반 즉시 진입 격발 (방향: {direction}, 점수: {morning_score:+.2f})"]
            logger.info(f"[MORNING MODE] 신호 발생 성공: {direction} ({reasons[0]})")
            return {
                "timestamp": datetime.now(),
                "futures_code": code,
                "direction": direction,
                "strength": 1.0,
                "score": 100 if direction == "BUY" else -100,
                "reasons": reasons,
                "regime": regime,
                "flow_direction": "MORNING_MODE",
                "foreign_zscore": morning_score,
                "option_call_net": indicators.get("option_call_net", 0),
                "option_put_net": indicators.get("option_put_net", 0),
                "net_option_flow": indicators.get("option_call_net", 0) - indicators.get("option_put_net", 0)
            }

        reasons = []
        direction = "HOLD"
        
        # 외국인 옵션 수급 분석 (Call - Put)
        option_call_net = indicators.get("option_call_net", 0)
        option_put_net = indicators.get("option_put_net", 0)
        net_option_flow = option_call_net - option_put_net
        
        # 1. 3단계: 가격 기반 기술적 트리거 판정 (MACD 크로스)
        macd = indicators.get("macd", 0.0)
        prev_macd = indicators.get("prev_macd", 0.0)
        macd_signal = indicators.get("macd_signal", 0.0)
        prev_macd_signal = indicators.get("prev_macd_signal", 0.0)
        
        macd_trigger = None
        if macd > macd_signal and prev_macd <= prev_macd_signal:
            macd_trigger = "LONG"
            reasons.append("MACD 골든크로스 트리거 격발 (LONG)")
        elif macd < macd_signal and prev_macd >= prev_macd_signal:
            macd_trigger = "SHORT"
            reasons.append("MACD 데드크로스 트리거 격발 (SHORT)")
            
        # 2. 1단계: 시장 국면(Regime) 필터 검증
        if regime == "ranging":
            direction = "HOLD"
            reasons.append("1단계 시장 국면 필터: 횡보(ranging) 국면으로 진입 원천 차단 (HOLD)")
            if macd_trigger:
                reasons.append(f"차단된 신호: {macd_trigger}")
                
        # 3. 2단계: 수급 검증 필터 및 가격 트리거 결합
        else:
            if macd_trigger == "LONG":
                # 롱 진입 조건
                # 옵션 필터 추가: net_option_flow가 음수(풋 우위)이면 롱 진입을 차단한다.
                is_option_ok = True
                if net_option_flow < 0:
                    is_option_ok = False
                    
                if not is_option_ok:
                    direction = "HOLD"
                    reasons.append(f"2단계 옵션 수급 필터 차단: 외인 옵션 하방 우위 (Net Option Flow={net_option_flow:+,} 계약, 콜={option_call_net:+,}, 풋={option_put_net:+,})")
                elif flow_direction == "LONG_ONLY":
                    direction = "BUY"
                    reasons.append(f"2단계 수급 필터 통과: 신규 롱 수급 검증 완료 (Z-Score={foreign_zscore:+.2f}, Net Option Flow={net_option_flow:+,})")
                elif regime == "trending" and foreign_zscore >= -getattr(config, "ZSCORE_THRESHOLD", 0.2) and flow_direction != "SHORT_COVERING":
                    direction = "BUY"
                    reasons.append(f"2단계 수급 필터 통과: 강한 추세장 진입 허용 (Z-Score={foreign_zscore:+.2f}, 수급방향={flow_direction}, Net Option Flow={net_option_flow:+,})")
                else:
                    direction = "HOLD"
                    reasons.append(f"2단계 수급 필터 차단: 롱 진입 요건 미달 (Z-Score={foreign_zscore:+.2f}, 수급방향={flow_direction})")
                    
            elif macd_trigger == "SHORT":
                # 숏 진입 조건
                # 옵션 필터 추가: net_option_flow가 양수(콜 우위)이면 숏 진입을 차단한다.
                is_option_ok = True
                if net_option_flow > 0:
                    is_option_ok = False
                    
                if not is_option_ok:
                    direction = "HOLD"
                    reasons.append(f"2단계 옵션 수급 필터 차단: 외인 옵션 상방 우위 (Net Option Flow={net_option_flow:+,} 계약, 콜={option_call_net:+,}, 풋={option_put_net:+,})")
                elif flow_direction == "SHORT_ONLY":
                    direction = "SELL"
                    reasons.append(f"2단계 수급 필터 통과: 신규 숏 수급 검증 완료 (Z-Score={foreign_zscore:+.2f}, Net Option Flow={net_option_flow:+,})")
                elif regime == "trending" and foreign_zscore <= getattr(config, "ZSCORE_THRESHOLD", 0.2) and flow_direction != "LONG_LIQUIDATION":
                    direction = "SELL"
                    reasons.append(f"2단계 수급 필터 통과: 강한 추세장 진입 허용 (Z-Score={foreign_zscore:+.2f}, 수급방향={flow_direction}, Net Option Flow={net_option_flow:+,})")
                else:
                    direction = "HOLD"
                    reasons.append(f"2단계 수급 필터 차단: 숏 진입 요건 미달 (Z-Score={foreign_zscore:+.2f}, 수급방향={flow_direction})")
        
        if direction != "HOLD":
            logger.info(f"신호 발생 성공: {direction} (이유: {', '.join(reasons)})")
        else:
            if macd_trigger:
                logger.info(f"신호 진입 차단: {macd_trigger} -> HOLD (이유: {', '.join(reasons)})")
                
        return {
            "timestamp": datetime.now(),
            "futures_code": code,
            "direction": direction,
            "strength": 1.0 if direction != "HOLD" else 0.0,
            "score": 100 if direction == "BUY" else (-100 if direction == "SELL" else 0),
            "reasons": reasons,
            "regime": regime,
            "flow_direction": flow_direction,
            "foreign_zscore": foreign_zscore,
            "option_call_net": option_call_net,
            "option_put_net": option_put_net,
            "net_option_flow": net_option_flow
        }
