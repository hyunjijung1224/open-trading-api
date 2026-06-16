import logging
from datetime import datetime
from typing import Dict, Any

logger = logging.getLogger("VolatilityEngine")

class VolatilityEngine:
    """
    시장 변동성 상태 분석 엔진
    - ATR을 기반으로 시장의 동적 리스크 측정
    - 변동성이 과도하게 확대될 경우 거래 수량(사이징)을 축소하고 손절폭을 확장
    """
    def __init__(self):
        self.atr = 1.0
        self.atr_ma = 1.0

    def update_indicators(self, atr: float, atr_ma: float):
        """실시간 계산된 ATR 및 ATR 이동평균 갱신"""
        self.atr = atr
        self.atr_ma = atr_ma if atr_ma > 0 else atr

    def analyze(self) -> Dict[str, Any]:
        """현재 변동성 상태 분석 및 조율 가중치 산출"""
        atr_ratio = self.atr / self.atr_ma if self.atr_ma > 0 else 1.0
        
        # ── 변동성 국면 판단 및 사이징/손절 배수 산출 ──
        if atr_ratio >= 2.0:
            level = "extreme"
            size_multiplier = 0.3          # 수량 70% 축소 (보수적 방어)
            stop_loss_multiplier = 3.0     # 노이즈 손절 방지를 위해 넓게 설정
            take_profit_multiplier = 4.5
        elif atr_ratio >= 1.5:
            level = "high"
            size_multiplier = 0.5          # 수량 50% 축소
            stop_loss_multiplier = 2.5
            take_profit_multiplier = 3.75
        elif atr_ratio >= 0.7:
            level = "normal"
            size_multiplier = 1.0          # 표준 사이즈
            stop_loss_multiplier = 2.0     # 표준 2*ATR 손절폭
            take_profit_multiplier = 3.0
        else:
            level = "low"
            size_multiplier = 1.0          # 표준 사이즈
            stop_loss_multiplier = 1.5     # 좁은 변동성 감안 타이트한 손절
            take_profit_multiplier = 2.25

        logger.debug(f"변동성 분석 완료: level={level}, atr_ratio={atr_ratio:.2f}, size_mult={size_multiplier}")
        
        return {
            "timestamp": datetime.now(),
            "level": level,
            "atr": self.atr,
            "atr_ratio": atr_ratio,
            "size_multiplier": size_multiplier,
            "stop_loss_multiplier": stop_loss_multiplier,
            "take_profit_multiplier": take_profit_multiplier
        }
