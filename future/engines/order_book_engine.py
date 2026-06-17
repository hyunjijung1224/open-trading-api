import logging
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional, Tuple
from collections import deque

logger = logging.getLogger("OrderBookEngine")

class OrderBookEngine:
    """
    실시간 호가 데이터 기반 Bid/Ask Imbalance 및 OFI(Order Flow Imbalance) 추적 엔진
    - 호가불균형(Bid/Ask Imbalance): 총매수호가잔량 / 총매도호가잔량
    - OFI (Order Flow Imbalance): 총호가잔량 변화량 기반 압력 방향
    """
    def __init__(self, max_snapshots: int = 300):
        self.max_snapshots = max_snapshots
        self._snapshots: Dict[str, deque] = {}
        self._last_total_ask: Dict[str, int] = {}
        self._last_total_bid: Dict[str, int] = {}
        self._has_snapshot: Dict[str, bool] = {}

    def update(self, code: str, total_ask_vol: int, total_bid_vol: int,
               timestamp: Optional[datetime] = None):
        """
        호가 데이터 1회 수신 시 호출
        """
        now = timestamp or datetime.now()
        imbalance = total_bid_vol / total_ask_vol if total_ask_vol > 0 else 1.0

        if code not in self._snapshots:
            self._snapshots[code] = deque(maxlen=self.max_snapshots)
            self._has_snapshot[code] = False

        # 첫 틱 가드: OFI 스파이크 방지
        if not self._has_snapshot[code]:
            self._last_total_ask[code] = total_ask_vol
            self._last_total_bid[code] = total_bid_vol
            self._has_snapshot[code] = True
            snap = {
                "time": now,
                "total_ask_vol": total_ask_vol,
                "total_bid_vol": total_bid_vol,
                "imbalance": imbalance,
                "ask_diff": 0,
                "bid_diff": 0,
                "ofi": 0.0,
            }
            self._snapshots[code].append(snap)
            return

        ask_diff = total_ask_vol - self._last_total_ask[code]
        bid_diff = total_bid_vol - self._last_total_bid[code]
        ofi = float(bid_diff - ask_diff)

        self._last_total_ask[code] = total_ask_vol
        self._last_total_bid[code] = total_bid_vol

        snap = {
            "time": now,
            "total_ask_vol": total_ask_vol,
            "total_bid_vol": total_bid_vol,
            "imbalance": imbalance,
            "ask_diff": ask_diff,
            "bid_diff": bid_diff,
            "ofi": ofi,
        }
        self._snapshots[code].append(snap)

    def _window(self, code: str, seconds: int) -> List[Dict]:
        if code not in self._snapshots:
            return []
        cutoff = datetime.now() - timedelta(seconds=seconds)
        return [s for s in self._snapshots[code] if s["time"] >= cutoff]

    def get_imbalance(self, code: str) -> float:
        """최신 호가불균형 (1.5↑ = 매수우위, 0.67↓ = 매도우위)"""
        snaps = self._snapshots.get(code)
        if not snaps:
            return 1.0
        return snaps[-1]["imbalance"]

    def get_ofi(self, code: str, seconds: int = 60) -> float:
        """특정 윈도우 누적 OFI (양수 = 매수압력, 음수 = 매도압력)"""
        snaps = self._window(code, seconds)
        return float(sum(s["ofi"] for s in snaps))

    def get_ofi_trend(self, code: str) -> str:
        """OFI 방향성 (positive/negative/neutral)"""
        ofi_30s = self.get_ofi(code, 30)
        if ofi_30s > 0:
            return "positive"
        elif ofi_30s < 0:
            return "negative"
        return "neutral"

    def get_spread(self, code: str) -> Optional[float]:
        """최신 호가 스프레드 계산 (총호가잔량 기반 근사)"""
        snaps = self._snapshots.get(code)
        if not snaps:
            return None
        return None  # 스프레드는 별도 스프레드 필드가 없으므로 미사용
