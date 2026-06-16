import logging
from datetime import datetime
from typing import List, Dict, Any

logger = logging.getLogger("PerformanceEngine")

class PerformanceEngine:
    """
    성과 기반 동적 포지션 사이징 엔진 (Performance Engine)
    - Anti-Martingale 원칙: 연승/고승률 시 사이즈 확대, 연패/저승률 시 사이즈 축소
    - 최근 20거래 이력을 참조하여 가중치 배수 산출
    """
    def __init__(self):
        self.trades: List[Dict[str, Any]] = []

    def update_trades_history(self, recent_db_trades: List[Dict[str, Any]]):
        """MariaDB에서 불러온 거래 이력 데이터를 갱신"""
        self.trades = recent_db_trades
        logger.info(f"Performance Engine 거래 이력 업데이트 완료: {len(self.trades)}건 로드")

    def calculate_multiplier(self, total_capital: float = 100_000_000.0) -> Dict[str, Any]:
        """최근 성과 기반 최종 포지션 사이즈 승수(0.25 ~ 1.5) 산출"""
        recent = self.trades[-20:]  # 최근 최대 20건 분석
        
        # 분석 대상 거래 이력이 부족한 경우 기본 배수 1.0 반환
        if len(recent) < 5:
            return {
                "timestamp": datetime.now(),
                "recent_win_rate": 0.50,
                "recent_avg_pnl": 0.0,
                "recent_mdd": 0.0,
                "consecutive_losses": 0,
                "size_multiplier": 1.0
            }

        # 1. 승률 및 평균 손익 계산
        wins = [t for t in recent if float(t.get("net_pnl", 0.0)) > 0]
        win_rate = len(wins) / len(recent)
        avg_pnl = sum(float(t.get("net_pnl", 0.0)) for t in recent) / len(recent)

        # 2. 연속 손실(Consecutive Losses) 계산
        consecutive_losses = 0
        # 최근 완결된 거래부터 거꾸로 스캔
        sorted_recent = sorted(recent, key=lambda x: x.get("exit_time", datetime.min), reverse=True)
        for t in sorted_recent:
            if float(t.get("net_pnl", 0.0)) <= 0:
                consecutive_losses += 1
            else:
                break

        # 3. 최근 구간 MDD 간이 계산
        # 손익 누적선에서의 낙폭 확인
        cumulative_pnl = 0.0
        peak = 0.0
        max_drawdown = 0.0
        for t in recent:
            cumulative_pnl += float(t.get("net_pnl", 0.0))
            if cumulative_pnl > peak:
                peak = cumulative_pnl
            drawdown = peak - cumulative_pnl
            if drawdown > max_drawdown:
                max_drawdown = drawdown
                
        # 가상 자본 대비 MDD 비율 (주입된 실시간 평가자산 기준)
        mdd_ratio = max_drawdown / total_capital if total_capital > 0 else 0.0

        # 4. 안티 마틴게일 사이징 승수 계산
        multiplier = 1.0

        # 승률 기반 조율
        if win_rate < 0.30:
            multiplier *= 0.25       # 매매 부진 극심 -> 사이즈 최저 축소
        elif win_rate < 0.35:
            multiplier *= 0.50       # 50% 축소
        elif win_rate < 0.40:
            multiplier *= 0.70       # 30% 축소
        elif win_rate >= 0.55:
            multiplier *= 1.20       # 연승/고승률 -> 20% 확대
        elif win_rate >= 0.60:
            multiplier *= 1.50       # 극도의 호조 -> 50% 확대 (최대 한도)

        # 연속 손실 기반 조율 (패널티 누적)
        if consecutive_losses >= 3:
            multiplier *= 0.50       # 3연패 이상 -> 추가 50% 반감
        elif consecutive_losses >= 2:
            multiplier *= 0.70       # 2연패 -> 추가 30% 감쇄

        # MDD 기반 조율
        if mdd_ratio > 0.05:          # 최근 낙폭이 5%를 초과한 경우 리스크 제어용 수량 50% 감축
            multiplier *= 0.50
        elif mdd_ratio > 0.03:
            multiplier *= 0.70

        # 최저/최고 배수 클리핑 (0.25배 ~ 1.5배)
        size_multiplier = float(max(0.25, min(1.5, multiplier)))
        
        logger.info(f"성과 분석: 승률={win_rate*100:.1f}%, 연패={consecutive_losses}회, MDD={mdd_ratio*100:.2f}%, 최종사이징배수={size_multiplier:.2f}")

        return {
            "timestamp": datetime.now(),
            "recent_win_rate": win_rate,
            "recent_avg_pnl": avg_pnl,
            "recent_mdd": mdd_ratio,
            "consecutive_losses": consecutive_losses,
            "size_multiplier": size_multiplier
        }
