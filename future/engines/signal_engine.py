import logging
from datetime import datetime
from typing import Dict, Any, List, Optional

logger = logging.getLogger("SignalEngine")

class SignalEngine:
    """
    스캘핑 신호 생성 엔진 — CVD/OFI/호가불균형/체결강도 4조건 AND 기반

    LONG 조건 (전부 충족):
      1) CVD direction = rising
      2) OFI (30s) > 0
      3) 호가불균형 (bid/ask) > 1.5
      4) 체결강도 >= 130

    SHORT 조건 (전부 충족):
      1) CVD direction = falling
      2) OFI (30s) < 0
      3) 호가불균형 (bid/ask) < 0.67
      4) 체결강도 <= 70
    """
    def __init__(self):
        pass

    def generate(self, code: str, regime: str, flow_direction: str, foreign_zscore: float,
                 indicators: Dict[str, Any]) -> Dict[str, Any]:
        """indicators에 order_flow / order_book / execution_pressure 데이터가 포함되어 넘어옴"""

        reasons = []
        direction = "HOLD"
        strength = 0.0

        cvd_trend = indicators.get("cvd_trend", "neutral")
        ofi = indicators.get("ofi_30s", 0.0)
        imbalance = indicators.get("imbalance", 1.0)
        exec_strength = indicators.get("exec_strength", 100.0)
        delta_30s = indicators.get("delta_30s", 0.0)
        buy_ratio = indicators.get("buy_ratio_30s", 50.0)

        # 레짐 필터 (ranging 차단)
        if regime == "ranging":
            reasons.append("ranging 레짐: 진입 차단")
            return self._result(code, "HOLD", 0.0, 0, reasons, regime, flow_direction, foreign_zscore, indicators)

        # 외국인 필터 (FOREIGN_BLOCK 시 HOLD)
        if flow_direction == "FOREIGN_BLOCK":
            reasons.append("외국인 수급 필터 차단 (FOREIGN_BLOCK)")
            return self._result(code, "HOLD", 0.0, 0, reasons, regime, flow_direction, foreign_zscore, indicators)

        # 4조건 AND 평가
        long_cond = (
            cvd_trend == "rising"
            and ofi > 0
            and imbalance > 1.5
            and exec_strength >= 130.0
        )
        short_cond = (
            cvd_trend == "falling"
            and ofi < 0
            and imbalance < 0.67
            and exec_strength <= 70.0
        )

        if long_cond:
            direction = "BUY"
            strength = 1.0
            reasons.append(
                f"CVD={cvd_trend} OFI={ofi:+.0f} 호가불균형={imbalance:.2f} 체결강도={exec_strength:.0f} → LONG"
            )
        elif short_cond:
            direction = "SELL"
            strength = 1.0
            reasons.append(
                f"CVD={cvd_trend} OFI={ofi:+.0f} 호가불균형={imbalance:.2f} 체결강도={exec_strength:.0f} → SHORT"
            )
        else:
            reasons.append(
                f"CVD={cvd_trend} OFI={ofi:+.0f} 호가불균형={imbalance:.2f} 체결강도={exec_strength:.0f} → 조건 미충족 HOLD"
            )

        score = 100 if direction == "BUY" else (-100 if direction == "SELL" else 0)

        if direction != "HOLD":
            logger.info(f"[SIGNAL] {direction} 이유: {reasons[-1]}")
        else:
            logger.debug(f"[SIGNAL] HOLD {reasons[-1]}")

        return self._result(code, direction, strength, score, reasons, regime, flow_direction, foreign_zscore, indicators)

    def _result(self, code: str, direction: str, strength: float, score: int,
                reasons: List[str], regime: str, flow_direction: str,
                foreign_zscore: float, indicators: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "timestamp": datetime.now(),
            "futures_code": code,
            "direction": direction,
            "strength": strength,
            "score": score,
            "reasons": reasons,
            "regime": regime,
            "flow_direction": flow_direction,
            "foreign_zscore": foreign_zscore,
            "option_call_net": indicators.get("option_call_net", 0),
            "option_put_net": indicators.get("option_put_net", 0),
            "net_option_flow": indicators.get("option_call_net", 0) - indicators.get("option_put_net", 0),
        }
