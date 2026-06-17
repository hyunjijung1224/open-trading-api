import logging
import numpy as np
from datetime import datetime
from typing import Dict, Any, List

logger = logging.getLogger("RegimeEngine")

class RegimeEngine:
    """
    시장 상태(국면) 판별 엔진 (스캘핑 튜닝)
    - ADX 기반 추세 강도 판별
    - ATR 기반 변동성 수준 판별
    - Bollinger Band Width (BBW) 기반 횡보 수축기 판별
    - tick_volatility: 최근 5캔들 평균 캔들레인지 %
    - volume_spike: 현재 거래량 / 20캔들 평균 거래량 비율
    """
    def __init__(self, adx_threshold: float = 20.0, bbw_threshold: float = 0.005):
        self.adx_threshold = adx_threshold
        self.bbw_threshold = bbw_threshold

    def detect(self, current_price: float, history_candles: List[Dict[str, Any]]) -> Dict[str, Any]:
        if len(history_candles) < 20:
            return {
                "detected_at": datetime.now(),
                "regime": "weak_trend",
                "adx": 20.0,
                "atr": 1.0,
                "volatility_level": "normal",
                "trend_strength": "weak",
                "tick_volatility": 0.0,
                "volume_spike": 1.0,
                "action": "데이터 부족",
                "signal_allowed": 1,
                "size_multiplier": 0.5
            }

        closes = np.array([c["close"] for c in history_candles], dtype=float)
        highs = np.array([c["high"] for c in history_candles], dtype=float)
        lows = np.array([c["low"] for c in history_candles], dtype=float)
        volumes = np.array([c.get("volume", 0) for c in history_candles], dtype=float)

        # 1. ATR
        tr = np.zeros(len(closes) - 1)
        for i in range(len(closes) - 1):
            h_l = highs[i+1] - lows[i+1]
            h_pc = abs(highs[i+1] - closes[i])
            l_pc = abs(lows[i+1] - closes[i])
            tr[i] = max(h_l, h_pc, l_pc)
        atr = float(np.mean(tr[-14:]))
        atr_ma = float(np.mean(tr[-20:]))

        # 2. BBW
        recent_closes = closes[-20:]
        sma = np.mean(recent_closes)
        std = np.std(recent_closes)
        upper_bb = sma + (2 * std)
        lower_bb = sma - (2 * std)
        bbw = float((upper_bb - lower_bb) / sma if sma > 0 else 0)

        # 3. ADX
        price_diff = np.diff(closes)
        up_moves = np.sum(price_diff[price_diff > 0])
        down_moves = np.sum(np.abs(price_diff[price_diff < 0]))
        total_moves = up_moves + down_moves
        adx = float((abs(up_moves - down_moves) / total_moves * 100) if total_moves > 0 else 10.0)

        # 4. tick_volatility (최근 5캔들 평균 캔들레인지 %)
        ranges_5 = (highs[-5:] - lows[-5:]) / closes[-5:] * 100
        tick_volatility = float(np.mean(ranges_5))

        # 5. volume_spike (최근 1캔들 / 20캔들 평균)
        vol_ma = float(np.mean(volumes[-20:]))
        volume_spike = float(volumes[-1] / vol_ma) if vol_ma > 0 else 1.0

        # ── 변동성 수준 ──
        if atr > atr_ma * 2.0:
            volatility_level = "extreme"
        elif atr > atr_ma * 1.5:
            volatility_level = "high"
        else:
            volatility_level = "normal"

        # ── 추세 강도 ──
        if adx >= 25.0:
            trend_strength = "strong"
        elif adx >= 15.0:
            trend_strength = "weak"
        else:
            trend_strength = "none"

        # ── 종합 레짐 ──
        if volatility_level == "extreme":
            regime = "volatile"
            action = "고변동성: 사이즈 50% 축소"
            signal_allowed = 1
            size_multiplier = 0.5
        elif trend_strength == "strong":
            regime = "trending"
            action = "강한 추세장: 정상 사이즈"
            signal_allowed = 1
            size_multiplier = 1.0
        elif trend_strength == "none" and bbw < self.bbw_threshold:
            regime = "ranging"
            action = "횡보 스퀴즈: 진입 차단"
            signal_allowed = 0
            size_multiplier = 0.0
        elif trend_strength == "none":
            regime = "weak_trend"
            action = "방향성 부족: 사이즈 50% 축소"
            signal_allowed = 1
            size_multiplier = 0.5
        else:
            regime = "weak_trend"
            action = "약한 추세장: 사이즈 30% 축소"
            signal_allowed = 1
            size_multiplier = 0.7

        return {
            "detected_at": datetime.now(),
            "regime": regime,
            "adx": round(adx, 2),
            "atr": round(atr, 2),
            "volatility_level": volatility_level,
            "trend_strength": trend_strength,
            "tick_volatility": round(tick_volatility, 4),
            "volume_spike": round(volume_spike, 2),
            "action": action,
            "signal_allowed": signal_allowed,
            "size_multiplier": size_multiplier
        }
