import logging
import numpy as np
from datetime import datetime
from typing import Dict, Any
from config import config

logger = logging.getLogger("ForeignFlowEngine")

class ForeignFlowEngine:
    """
    외국인 선물 수급 분석 및 신호 필터링 엔진
    - 롤링 Z-Score 기반 외국인 선물 매매 동향 분석
    - 미결제약정(OI) 및 가격 변동량과 결합하여 2단계 수급 검증 필터 판정
    """
    def __init__(self, window_size: int = 120):
        self.window_size = window_size
        self.flow_history = []  # 외국인 누적 순매수 계약 수 이력 보관용
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
        """
        KIS REST API로부터 취득한 투자자별 매매동향 데이터를 갱신하고 Z-Score를 계산
        """
        foreign = raw_investor_data.get("foreign", 0)
        inst = raw_investor_data.get("institution", 0)
        indiv = raw_investor_data.get("individual", 0)
        oi_change = raw_investor_data.get("foreign_oi", 0)
        
        # 롤링 윈도우 적재
        self.flow_history.append(foreign)
        if len(self.flow_history) > self.window_size:
            self.flow_history.pop(0)
            
        # Z-Score 계산 (최소 5개 데이터 확보 시 가동)
        if len(self.flow_history) >= 5:
            arr = np.array(self.flow_history, dtype=float)
            mean = np.mean(arr)
            std = np.std(arr)
            zscore = (foreign - mean) / std if std > 0.0 else 0.0
        else:
            zscore = 0.0
            
        # 수급 강도 정규화 (Z-Score 기준 -2.0 ~ +2.0 범위를 0.0 ~ 1.0으로 매핑)
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
        logger.info(f"외국인 수급 업데이트: {foreign:+,}계약 (Z-Score: {zscore:+.2f}, 강도: {flow_strength:.2f})")

    def get_latest_flow(self) -> Dict[str, Any]:
        """최근 갱신된 수급 통계값 반환"""
        return self.latest_flow

    def get_flow_direction(self, price_change: float, oi_change: float) -> str:
        """
        가격 변동량, 미결제약정 변동량 및 외국인 수급 Z-Score를 결합하여 수급 필터 방향 판정
        - LONG_ONLY: 롱 진입 검증 완료
        - SHORT_ONLY: 숏 진입 검증 완료
        - SHORT_COVERING: 숏커버링 청산 국면 (신규 롱 진입 금지)
        - LONG_LIQUIDATION: 롱청산 국면 (신규 숏 진입 금지)
        - NEUTRAL: 수급 보합 또는 방향 혼조세
        """
        zscore = self.latest_flow.get("foreign_zscore", 0.0)
        
        # 1. 숏커버링 및 롱청산 판단 (미결제약정이 크게 감소하며 가격이 반대로 튀는 경우)
        oi_thr = getattr(config, "OI_CHANGE_THRESHOLD", 5)
        z_thr = getattr(config, "ZSCORE_THRESHOLD", 0.2)
        
        if price_change > 0 and oi_change <= -oi_thr:
            return "SHORT_COVERING"
        elif price_change < 0 and oi_change <= -oi_thr:
            return "LONG_LIQUIDATION"
            
        # 2. 신규 진입을 위한 수급 모멘텀 검증 (외국인 Z-Score와 미결제약정 동반 증가)
        if price_change > 0 and oi_change >= oi_thr and zscore > z_thr:
            return "LONG_ONLY"
        elif price_change < 0 and oi_change >= oi_thr and zscore < -z_thr:
            return "SHORT_ONLY"
            
        return "NEUTRAL"
