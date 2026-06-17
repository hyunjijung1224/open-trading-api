# Schema — KOSPI200선물 AI 자동매매 시스템 데이터 모델 (스캘핑)

> **Version**: 5.0
> **Last Updated**: 2026-06-16
> **Target**: GCE (상시 실행, WebSocket 기반) + Cloud Run (비상 백업)

---

## 1. Overview

본 문서는 `future/engines/` 하위 엔진들의 입출력 데이터 구조를 정의한다.
Python `dict` 기반으로, 타입 안전성은 런타임 로깅으로 보장한다.

---

## 2. Engine States

### 2.1 OrderFlowState (신규)

```python
# Signal Engine indicators dict 내 포함
{
    "cvd_trend": str,          # "rising" | "falling" | "neutral"
    "delta_30s": float,        # 30초 Volume Delta
    "delta_60s": float,        # 60초 Volume Delta
    "buy_ratio_30s": float,    # 30초 매수비율 (0~100)
}
```

### 2.2 OrderBookState (신규)

```python
{
    "imbalance": float,        # total_bid_vol / total_ask_vol (1.5↑ 매수우위, 0.67↓ 매도우위)
    "ofi_30s": float,          # 30초 누적 OFI (양수=매수압력, 음수=매도압력)
}
```

### 2.3 ExecutionPressureState (신규)

```python
{
    "exec_strength": float,     # 최신 체결강도 (130↑ 강한매수, 70↓ 강한매도)
    "exec_avg_strength_30s": float,  # 30초 평균 체결강도
    "net_buy_count_30s": int,   # 30초 순매수체결건수
}
```

### 2.4 MorningEngineState (신규)

```python
# MorningEngine.evaluate() return dict
{
    "direction": str,          # "BUY" | "SELL" | "HOLD"
    "strength": float,         # 0.0 ~ 1.0
    "reasons": list[str],      # 신호 발생/차단 사유
    "bull_score": int,         # 0 ~ 10 (7점 이상 진입)
    "bear_score": int,         # 0 ~ 10 (7점 이상 진입)
    "strategy_scores": {       # 전략별 점수 상세
        "overnight": {"bull": int, "bear": int, "detail": str},
        "gap_analysis": {"bull": int, "bear": int, "detail": str},
        "orb": {"bull": int, "bear": int, "detail": str},
        "foreign_attack": {"bull": int, "bear": int, "detail": str},
        "gap_fill": {"bull": int, "bear": int, "detail": str},
    },
}
```

### 2.5 SignalState (변경)

```python
# SignalEngine.generate() return dict
{
    "timestamp": datetime,
    "futures_code": str,
    "direction": str,          # "BUY" | "SELL" | "HOLD"
    "strength": float,         # 1.0 (진입) | 0.0 (HOLD)
    "score": int,              # 100 (BUY) | -100 (SELL) | 0 (HOLD)
    "reasons": list[str],      # 신호 발생/차단 사유
    "regime": str,             # "trending" | "weak_trend" | "ranging" | "volatile"
    "flow_direction": str,     # "FOREIGN_OK" | "FOREIGN_BLOCK"
    "foreign_zscore": float,   # 외국인 Z-Score
    "option_call_net": int,
    "option_put_net": int,
    "net_option_flow": int,
}
```

### 2.6 RegimeState (tick_volatility/volume_spike 추가)

```python
{
    "detected_at": datetime,
    "regime": str,             # "trending" | "weak_trend" | "ranging" | "volatile"
    "adx": float,
    "atr": float,
    "volatility_level": str,   # "extreme" | "high" | "normal"
    "trend_strength": str,     # "strong" | "weak" | "none"
    "tick_volatility": float,  # 최근 5캔들 평균 캔들레인지 %
    "volume_spike": float,     # 현재거래량 / 20캔들 평균거래량
    "action": str,
    "signal_allowed": int,     # 1 or 0
    "size_multiplier": float,  # 0.0 ~ 1.0
}
```

### 2.7 ForeignFlowState (단순화)

```python
{
    "fetched_at": datetime,
    "foreign_net_buy": int,
    "institution_net_buy": int,
    "individual_net_buy": int,
    "foreign_oi_change": int,
    "foreign_zscore": float,      # 롤링 Z-Score
    "flow_strength": float,       # 0.0 ~ 1.0
}
```

---

## 3. MariaDB DDL

### 3.1 Regime States (tick_volatility/volume_spike 추가)

```sql
ALTER TABLE regime_states
    ADD COLUMN tick_volatility DECIMAL(8,4) DEFAULT 0.0000 AFTER trend_strength,
    ADD COLUMN volume_spike DECIMAL(5,2) DEFAULT 1.00 AFTER tick_volatility;
```

### 3.2 기존 테이블

- `active_positions`: 실시간 포지션
- `orders`: 주문 내역
- `trades`: 청산 거래 내역
- `market_candles`: 1분봉 시세
- `foreign_flows`: 외국인 수급 이력
- `performance_metrics`: 성과 지표
- `morning_briefing_scores`: 모닝브리핑 (모닝 엔진에서 야간 컨텍스트로 활용)
