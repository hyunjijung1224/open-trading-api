import logging
import numpy as np
from datetime import datetime
from typing import Dict, Any
from config import config

logger = logging.getLogger("ForeignFlowEngine")

class ForeignFlowEngine:
    """
    외국인 선물 수급 분석 및 방향 필터 엔진 (스캘핑 전용)
    - 롤링 Z-Score 기반 외국인 선물 매매 동향 분석
    - Z-Score 극단값에서 FOREIGN_BLOCK 반환 (진입 제한)
    - 그 외 FOREIGN_OK 반환 (진입 허용 - 방향 강제 없음)
    """
    def __init__(self, window_size: int = 120):
        self.window_size = window_size
        self.flow_history = []
        self.latest_flow = {
            "fetched_at": datetime.now(),
            "foreign_net_buy": 0,
            "institution_net_buy": 0,
            "individual_net_buy": 0,
            "foreign_oi_change": 0,
            "foreign_zscore": 0.0,
            "flow_strength": 0.5
        }

    def update_flow(self, raw_investor_data: Dict[str, Any]):
        foreign = raw_investor_data.get("foreign", 0)
        inst = raw_investor_data.get("institution", 0)
        indiv = raw_investor_data.get("individual", 0)
        oi_change = raw_investor_data.get("foreign_oi", 0)

        self.flow_history.append(foreign)
        if len(self.flow_history) > self.window_size:
            self.flow_history.pop(0)

        if len(self.flow_history) >= 5:
            arr = np.array(self.flow_history, dtype=float)
            mean = np.mean(arr)
            std = np.std(arr)
            zscore = (foreign - mean) / std if std > 0.0 else 0.0
        else:
            zscore = 0.0

        normalized = (zscore + 2.0) / 4.0
        flow_strength = float(max(0.0, min(1.0, normalized)))

        self.latest_flow = {
            "fetched_at": datetime.now(),
            "foreign_net_buy": foreign,
            "institution_net_buy": inst,
            "individual_net_buy": indiv,
            "foreign_oi_change": oi_change,
            "foreign_zscore": zscore,
            "flow_strength": flow_strength
        }
        logger.info(f"외국인 수급 업데이트: {foreign:+,}계약 (Z-Score: {zscore:+.2f})")

    def get_latest_flow(self) -> Dict[str, Any]:
        return self.latest_flow

    def get_flow_direction(self, price_change: float, oi_change: float) -> str:
        """
        필터 전용: Z-Score 극단값이면 FOREIGN_BLOCK, 정상 범위면 FOREIGN_OK
        - 스캘핑은 외국인 수급에 방향을 의존하지 않으나,
          극단적 이탈(Z-Score ±2.0 초과) 시 시장 왜곡으로 간주하여 진입 제한
        """
        zscore = self.latest_flow.get("foreign_zscore", 0.0)
        z_thr = getattr(config, "ZSCORE_THRESHOLD", 0.2) * 5  # ±1.0 차단 기준

        if zscore > z_thr or zscore < -z_thr:
            logger.info(f"[FOREIGN FILTER] BLOCK: Z-Score={zscore:+.2f} (임계 ±{z_thr:.2f})")
            return "FOREIGN_BLOCK"
        return "FOREIGN_OK"
