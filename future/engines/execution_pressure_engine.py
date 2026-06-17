import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from collections import deque

logger = logging.getLogger("ExecutionPressureEngine")

class ExecutionPressureEngine:
    """
    체결강도 및 매수/매도 체결건수 기반 실행 압력 추적 엔진
    - 체결강도 (Execution Strength): KIS 제공 (매수체결건수/매도체결건수)*100
    - Net Buy Count: 순매수체결건수 윈도우 합계
    - 실행 압력 방향: 강한 매수압력(>1.3) / 강한 매도압력(<0.7)
    """
    def __init__(self, max_ticks: int = 3000):
        self.max_ticks = max_ticks
        self._tick_buffers: Dict[str, deque] = {}
        self._last_exec_strength: Dict[str, float] = {}
        self._last_net_buy_count: Dict[str, int] = {}

    def update(self, code: str, price: float, exec_strength: float,
               net_buy_count: int, buy_ratio: float,
               last_volume: int, timestamp: Optional[datetime] = None):
        """
        체결 데이터 1틱 수신 시 호출
        """
        if code not in self._tick_buffers:
            self._tick_buffers[code] = deque(maxlen=self.max_ticks)

        now = timestamp or datetime.now()
        tick = {
            "time": now,
            "price": price,
            "exec_strength": exec_strength,
            "net_buy_count": net_buy_count,
            "buy_ratio": buy_ratio,
            "last_volume": last_volume,
        }
        self._tick_buffers[code].append(tick)
        self._last_exec_strength[code] = exec_strength
        self._last_net_buy_count[code] = net_buy_count

    def _window_ticks(self, code: str, seconds: int) -> List[Dict]:
        if code not in self._tick_buffers:
            return []
        cutoff = datetime.now() - timedelta(seconds=seconds)
        return [t for t in self._tick_buffers[code] if t["time"] >= cutoff]

    def get_exec_strength(self, code: str) -> float:
        """최신 체결강도 (130↑ = 강한 매수압력, 70↓ = 강한 매도압력)"""
        return self._last_exec_strength.get(code, 100.0)

    def get_avg_exec_strength(self, code: str, seconds: int = 60) -> float:
        """특정 윈도우 평균 체결강도"""
        ticks = self._window_ticks(code, seconds)
        if not ticks:
            return 100.0
        return float(sum(t["exec_strength"] for t in ticks)) / len(ticks)

    def get_net_buy_count(self, code: str, seconds: int = 60) -> int:
        """특정 윈도우 내 순매수체결건수 합계"""
        ticks = self._window_ticks(code, seconds)
        if not ticks:
            return 0
        first = ticks[0]
        last = ticks[-1]
        return last["net_buy_count"] - first["net_buy_count"]

    def get_pressure(self, code: str) -> str:
        """실행 압력 방향 (buy_pressure/sell_pressure/neutral)"""
        strength = self.get_exec_strength(code)
        if strength >= 130.0:
            return "buy_pressure"
        elif strength <= 70.0:
            return "sell_pressure"
        return "neutral"
