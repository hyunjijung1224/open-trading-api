import logging
import numpy as np
from datetime import datetime
from typing import Dict, Any, List

logger = logging.getLogger("RegimeEngine")

class RegimeEngine:
    """
    시장 상태(국면) 판별 엔진
    - ADX 기반 추세 강도 판별
    - ATR 기반 변동성 수준 판별
    - Bollinger Band Width (BBW) 기반 횡보 수축기 판별
    """
    def __init__(self, adx_threshold: float = 20.0, bbw_threshold: float = 0.005):
        self.adx_threshold = adx_threshold
        self.bbw_threshold = bbw_threshold

    def detect(self, current_price: float, history_candles: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        최근 분봉/일봉 역사적 캔들 데이터를 분석하여 현재 시장의 레짐 검출
        - history_candles: [{'close': float, 'high': float, 'low': float, 'volume': int}] 형태의 목록
        """
        # 데이터가 부족하면 기본 레짐 반환
        if len(history_candles) < 20:
            return {
                "detected_at": datetime.now(),
                "regime": "weak_trend",
                "adx": 20.0,
                "atr": 1.0,
                "volatility_level": "normal",
                "trend_strength": "weak",
                "action": "데이터 부족으로 기본 보수적 거래 모드 적용",
                "signal_allowed": 1, # 1: True, 0: False
                "size_multiplier": 0.5
            }

        # 캔들 데이터 기반 지표 산출
        closes = np.array([c["close"] for c in history_candles], dtype=float)
        highs = np.array([c["high"] for c in history_candles], dtype=float)
        lows = np.array([c["low"] for c in history_candles], dtype=float)
        
        # 1. ATR 계산 (기본 14 기간)
        tr = np.zeros(len(closes) - 1)
        for i in range(len(closes) - 1):
            h_l = highs[i+1] - lows[i+1]
            h_pc = abs(highs[i+1] - closes[i])
            l_pc = abs(lows[i+1] - closes[i])
            tr[i] = max(h_l, h_pc, l_pc)
        atr = float(np.mean(tr[-14:]))
        atr_ma = float(np.mean(tr[-20:]))  # ATR 20일 MA

        # 2. 볼린저 밴드 Width (BBW) 계산
        recent_closes = closes[-20:]
        sma = np.mean(recent_closes)
        std = np.std(recent_closes)
        upper_bb = sma + (2 * std)
        lower_bb = sma - (2 * std)
        bbw = float((upper_bb - lower_bb) / sma if sma > 0 else 0)

        # 3. ADX 계산 (간이 구현 - 방향성 이동 지표)
        # 퀀트 분석용 간소화된 추세 강도 측정 지표 (최근 20 캔들의 가격 변화 변동성 대비 순방향성 비율)
        price_diff = np.diff(closes)
        up_moves = np.sum(price_diff[price_diff > 0])
        down_moves = np.sum(np.abs(price_diff[price_diff < 0]))
        total_moves = up_moves + down_moves
        adx = float((abs(up_moves - down_moves) / total_moves * 100) if total_moves > 0 else 10.0)

        # ── 1차: 변동성 수준 판정 ──
        if atr > atr_ma * 2.0:
            volatility_level = "extreme"
        elif atr > atr_ma * 1.5:
            volatility_level = "high"
        else:
            volatility_level = "normal"

        # ── 2차: 추세 강도 판정 ──
        if adx >= 25.0:
            trend_strength = "strong"
        elif adx >= 15.0:
            trend_strength = "weak"
        else:
            trend_strength = "none"

        # ── 3차: 종합 레짐(Regime) 판정 ──
        # 고변동성 리스크 국면
        if volatility_level == "extreme":
            regime = "volatile"
            action = "고변동성 감지: 포지션 사이즈 50% 축소 및 신입 제한"
            signal_allowed = 1
            size_multiplier = 0.5
        # 강한 추세 국면
        elif trend_strength == "strong":
            regime = "trending"
            action = "강한 추세장: 정상 포지션 사이즈 적용"
            signal_allowed = 1
            size_multiplier = 1.0
        # 횡보 국면 (ADX가 매우 낮고 볼린저 밴드가 극도로 좁은 스퀴즈 상태 - 두 조건 동시 충족 필요)
        elif trend_strength == "none" and bbw < self.bbw_threshold:
            regime = "ranging"
            action = "횡보 국면 (스퀴즈): 신규 매매 차단 및 포지션 대기"
            signal_allowed = 0  # 진입 금지
            size_multiplier = 0.0
        # ADX가 낮지만 BBW가 충분하면 weak_trend로 분류 (ranging이 아님)
        elif trend_strength == "none":
            regime = "weak_trend"
            action = "방향성 부족이지만 변동성 있음: 포지션 사이즈 50% 축소"
            signal_allowed = 1
            size_multiplier = 0.5
        # 그 외 약한 추세 국면
        else:
            regime = "weak_trend"
            action = "약한 추세장: 포지션 사이즈 30% 축소 운영"
            signal_allowed = 1
            size_multiplier = 0.7

        return {
            "detected_at": datetime.now(),
            "regime": regime,
            "adx": adx,
            "atr": atr,
            "volatility_level": volatility_level,
            "trend_strength": trend_strength,
            "action": action,
            "signal_allowed": signal_allowed,
            "size_multiplier": size_multiplier
        }
