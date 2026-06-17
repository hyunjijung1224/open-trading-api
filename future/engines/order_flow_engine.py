import logging
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
from collections import deque

logger = logging.getLogger("OrderFlowEngine")

class OrderFlowEngine:
    """
    실시간 체결 데이터 기반 매수-매도 압력(CVD/Delta) 추적 엔진
    - CVD: 누적 Volume Delta (총매수수량 - 총매도수량)
    - Delta: 특정 윈도우(30s/60s/180s) 내 순매수-매도 차이
    - Buy Ratio: 특정 윈도우 내 매수 비율
    """
    def __init__(self, max_ticks: int = 3000):
        self.max_ticks = max_ticks
        self._tick_buffers: Dict[str, deque] = {}
        self._cvd: Dict[str, float] = {}
        self._last_cum_buy: Dict[str, int] = {}
        self._last_cum_sell: Dict[str, int] = {}

    def update(self, code: str, price: float, last_volume: int,
               total_buy_vol: int, total_sell_vol: int,
               buy_ratio: float, exec_strength: float,
               timestamp: Optional[datetime] = None):
        """
        체결 데이터 1틱 수신 시 호출 (ws_manager 체결 콜백에서 feed)
        """
        if code not in self._tick_buffers:
            self._tick_buffers[code] = deque(maxlen=self.max_ticks)
            self._cvd[code] = 0.0
            self._last_cum_buy[code] = 0
            self._last_cum_sell[code] = 0

        now = timestamp or datetime.now()
        last_buy = self._last_cum_buy[code]
        last_sell = self._last_cum_sell[code]

        vol_buy = total_buy_vol - last_buy if last_buy > 0 else 0
        vol_sell = total_sell_vol - last_sell if last_sell > 0 else 0

        self._last_cum_buy[code] = total_buy_vol
        self._last_cum_sell[code] = total_sell_vol

        tick = {
            "time": now,
            "price": price,
            "last_volume": last_volume,
            "vol_buy": vol_buy,
            "vol_sell": vol_sell,
            "delta": vol_buy - vol_sell,
            "buy_ratio": buy_ratio,
            "exec_strength": exec_strength,
            "total_buy_vol": total_buy_vol,
            "total_sell_vol": total_sell_vol,
        }
        self._tick_buffers[code].append(tick)
        self._cvd[code] = float(total_buy_vol - total_sell_vol)

    def _window_ticks(self, code: str, seconds: int) -> List[Dict]:
        if code not in self._tick_buffers:
            return []
        cutoff = datetime.now() - timedelta(seconds=seconds)
        return [t for t in self._tick_buffers[code] if t["time"] >= cutoff]

    def get_delta(self, code: str, seconds: int = 60) -> float:
        """특정 윈도우(초) 동안 순 Volume Delta"""
        ticks = self._window_ticks(code, seconds)
        return float(sum(t["delta"] for t in ticks))

    def get_cvd(self, code: str) -> float:
        """전체 누적 CVD (from total_buy_vol - total_sell_vol)"""
        return self._cvd.get(code, 0.0)

    def get_buy_ratio(self, code: str, seconds: int = 60) -> float:
        """특정 윈도우 내 매수비율 (0~100)"""
        ticks = self._window_ticks(code, seconds)
        total_buy = sum(t["vol_buy"] for t in ticks)
        total_sell = sum(t["vol_sell"] for t in ticks)
        total = total_buy + total_sell
        return (total_buy / total * 100) if total > 0 else 50.0

    def get_cvd_trend(self, code: str) -> str:
        """CVD 방향성 (rising/falling/neutral)"""
        delta_30s = self.get_delta(code, 30)
        if delta_30s > 0:
            return "rising"
        elif delta_30s < 0:
            return "falling"
        return "neutral"
