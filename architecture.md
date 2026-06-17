# Architecture — KOSPI200선물 AI 자동매매 시스템 (스캘핑)

> **Version**: 5.0
> **Last Updated**: 2026-06-16
> **Target**: KOSPI200선물 (주간 정규장)
> **Primary Runtime**: Google Compute Engine (Ubuntu 24.04 e2-micro, 무료 티어) — WebSocket 상시 연결
> **Backup Runtime**: Google Cloud Run — 5분 간격 헬스체크 및 비상 복구

---

## 1. System Overview

### 1.1 Goal

한국투자증권 Open API 기반 **KOSPI200선물 스캘핑 자동매매 시스템**.
MACD-based 스윙→ CVD/OFI/호가불균형/체결강도 기반 **초단기 스캘핑**으로 전환.

### 1.2 Design Principles

| 원칙 | 설명 |
|------|------|
| **Rule-Based Core** | AI 예측 모델이 아닌 검증 가능한 규칙 기반 엔진이 실제 매매를 수행 |
| **Dual Session** | **모닝 엔진(08:45~09:30)** + **스캘핑 엔진(09:30~15:45)** 별도 운영 |
| **Order Flow First** | **MACD → CVD/OFI/호가/체결강도** 4조건 AND로 신호 생성 |
| **Regime Guard** | Regime Engine은 신호 직후 검증 (선필터 → 후검증) |
| **AI-Assisted Risk** | AI는 뉴스/위험도/모니터링을 보조하며, 매매 결정권은 없음 |
| **Risk-First** | 수익은 전략보다 리스크 관리가 결정. 손절 + 포지션 사이징이 핵심 |
| **WebSocket Primary** | GCE에서 WebSocket 상시 연결 → 실시간 체결/호가/손절 즉시 반응 |
| **Cloud Run Backup** | 5분마다 GCE 상태를 체크, GCE 장애 시 비상 매매(손절/청산) 수행 |
| **Scalping Sizing** | 10~20회/일, 1계약 단위, Anti-Martingale 적응형 사이징 |
| **Long/Short 양방향** | 선물 특성상 매수·매도 양방향 진입 지원 |

### 1.3 Infrastructure — Dual Runtime

#### Primary: GCE (Ubuntu 24.04 e2-micro)

GCE 상시 WebSocket 연결로 실시간 체결/호가 데이터를 수신하고, 모든 엔진이 1초 단위로 신호를 처리.

#### Backup: Cloud Run (무료 티어)

5분 간격 GCE 헬스체크, 장애 시 Google Sheets 백업 기반 비상 청산.

---

## 2. High-Level Architecture

### 2.1 Core Trading Flow (Dual Session)

```
┌─────────────────────────────────────────────────────────────────┐
│  KIS WebSocket (H0IFCNT0 체결, H0IFASP0 호가)                   │
│       │                                                         │
│       ├──▶ Order Flow Engine (CVD/Delta, 30s/60s/180s 윈도우)   │
│       ├──▶ Order Book Engine (호가불균형, OFI)                    │
│       └──▶ Execution Pressure Engine (체결강도, 순매수체결건수)    │
│       │                                                         │
│       ▼                                                         │
│  ┌─ 08:45 ~ 09:30 ────────────────────────────────────────────┐ │
│  │  MORNING ENGINE (5개 전략 + 점수 시스템)                     │ │
│  │  ├─ Strategy #1: Overnight Score (CME/S&P/NASDAQ/환율/OI)   │ │
│  │  ├─ Strategy #2: Gap Analysis (갭 방향 추종)                │ │
│  │  ├─ Strategy #3: Opening Range Breakout (ORB)               │ │
│  │  ├─ Strategy #4: Foreign Open Attack (CVD+OI+Basis)         │ │
│  │  └─ Strategy #5: Gap Fill (갭 과대 역방향)                   │ │
│  │                                                              │ │
│  │  bull_score / bear_score (0~10) → 7점 이상 진입              │ │
│  └──────────────────────────────────────────────────────────────┘ │
│       │                                                         │
│       ▼ 09:30 handoff                                           │
│  ┌─ 09:30 ~ 15:45 ────────────────────────────────────────────┐ │
│  │  SCALPING ENGINE (4조건 AND 게이트)                          │ │
│  │  ├─ Signal Engine (CVD ∧ OFI ∧ 호가불균형 ∧ 체결강도)       │ │
│  │  ├─ Regime Engine (시장 상태 검증)                           │ │
│  │  ├─ Foreign Flow Filter (FOREIGN_OK / FOREIGN_BLOCK)        │ │
│  │  └─ Volatility → Performance → Risk → Execution             │ │
│  └──────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 System Architecture Diagram

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                    GCE (Ubuntu 24.04 e2-micro, 상시 실행)                    │
│                                                                              │
│  KIS WebSocket ──▶ WS Manager ──▶ _on_realtime_execution ──▶ Engine Feed    │
│       H0IFCNT0    (체결 파싱)     (CVD/Delta/체결강도)    (OrderFlow +       │
│       H0IFASP0                     _on_realtime_orderbook    ExecutionPressure)│
│       (호가)                       (호가불균형/OFI)      (OrderBook)          │
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────────┐ │
│  │  Supervisor Main Loop (1초 주기)                                        │ │
│  │                                                                         │ │
│  │  ① _manage_morning_session (08:45~09:30 모닝 엔진 관리)               │ │
│  │  ② _process_morning_trading (모닝 5개 전략 점수 평가)                  │ │
│  │  ③ _process_trading_logic (정상 스캘핑 4조건 AND)                       │ │
│  │  ④ _check_and_force_close_at_session_end (장 마감 강제청산)            │ │
│  └─────────────────────────────────────────────────────────────────────────┘ │
│                                                                              │
│  ┌─────────────┐    ┌──────────────┐    ┌──────────────────┐               │
│  │  MariaDB     │    │ Google Sheets │    │  Telegram Agent  │               │
│  │  (로컬 저장)  │    │  (백업 동기화) │    │  (알림 발송)      │               │
│  └─────────────┘    └──────────────┘    └──────────────────┘               │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Core Components

### 3.1 Trading Supervisor (`future/supervisor.py`)

시스템의 **중앙 오케스트레이터**. GCE에서 상시 실행.

**주요 메서드**:
- `_manage_morning_session()`: 08:45~09:30 모닝 엔진 활성화/비활성화
- `_process_morning_trading()`: 모닝 엔진 5개 전략 점수 평가 및 주문
- `_process_trading_logic()`: 정상 스캘핑 4조건 AND 게이트
- `_check_and_force_close_at_session_end()`: 장 마감 전 강제청산

### 3.2 Morning Engine (`future/engines/morning_engine.py`) — 신규

**08:45~09:30 구간 전용 모닝 트레이딩 엔진**.

5개 전략 기반 bull_score / bear_score (0~10) 산출, 7점 이상 진입.

| 전략 | 설명 | 최대점수 |
|------|------|---------|
| **Overnight Score** | 야간 시장(CME/S&P/NASDAQ/환율/OI) 기반 사전 점수 | 4점 |
| **Gap Analysis** | 시초 갭 방향 추종 (갭 > 0.5% → LONG) | 3점 |
| **Opening Range Breakout** | 08:45~08:50 범위 돌파 매매 | 3점 |
| **Foreign Open Attack** | 외국인 첫 진입 분석 (CVD + OI + 베이시스) | 3점 |
| **Gap Fill** | 갭 과대 시 역방향 (갭 > 1.5% → SHORT) | 2점 |

**수명 주기**:
1. `set_overnight_context()`: 장 시작 전(08:00~08:45) 야간 데이터 주입
2. `activate()`: 08:45 도달 시 활성화
3. `evaluate()`: 매 틱마다 호출, 5개 전략 점수 합산
4. `deactivate()`: 09:30 도달 시 비활성화, handoff 반환

### 3.3 Order Flow Engine (`future/engines/order_flow_engine.py`)

**실시간 체결 데이터 기반 CVD(Cumulative Volume Delta) 및 Delta 추적**.

| 메서드 | 설명 |
|--------|------|
| `update(code, price, last_volume, total_buy_vol, total_sell_vol, ...)` | WS 체결 틱 1건 수신 |
| `get_delta(code, seconds=60)` | 지정 윈도우(30s/60s/180s) 순 Volume Delta |
| `get_cvd(code)` | 전체 누적 CVD |
| `get_buy_ratio(code, seconds=60)` | 특정 윈도우 내 매수비율(0~100) |
| `get_cvd_trend(code)` | CVD 방향성 (rising/falling/neutral) |

### 3.4 Order Book Engine (`future/engines/order_book_engine.py`)

**실시간 호가 데이터 기반 Bid/Ask Imbalance 및 OFI(Order Flow Imbalance) 추적**.

| 메서드 | 설명 |
|--------|------|
| `update(code, total_ask_vol, total_bid_vol, ...)` | WS 호가 스냅샷 1건 수신 |
| `get_imbalance(code)` | 최신 호가불균형 (total_bid / total_ask) |
| `get_ofi(code, seconds=60)` | 특정 윈도우 누적 OFI |
| `get_ofi_trend(code)` | OFI 방향성 (positive/negative/neutral) |

### 3.5 Execution Pressure Engine (`future/engines/execution_pressure_engine.py`)

**체결강도 및 매수/매도 체결건수 기반 실행 압력 추적**.

| 메서드 | 설명 |
|--------|------|
| `update(code, price, exec_strength, net_buy_count, ...)` | WS 체결 틱 1건 수신 |
| `get_exec_strength(code)` | 최신 체결강도 (KIS제공, (매수체결건수/매도체결건수)*100) |
| `get_avg_exec_strength(code, seconds=60)` | 지정 윈도우 평균 체결강도 |
| `get_net_buy_count(code, seconds=60)` | 지정 윈도우 순매수체결건수 |
| `get_pressure(code)` | 실행 압력 방향 (buy_pressure/sell_pressure/neutral) |

### 3.6 Signal Engine (`future/engines/signal_engine.py`)

**스캘핑 4조건 AND 신호 생성**. MACD 제거됨.

**LONG 조건 (전부 충족)**:
1. CVD trend = `rising`
2. OFI (30s) > 0
3. 호가불균형 (bid/total_ask) > 1.5
4. 체결강도 >= 130

**SHORT 조건 (전부 충족)**:
1. CVD trend = `falling`
2. OFI (30s) < 0
3. 호가불균형 (bid/total_ask) < 0.67
4. 체결강도 <= 70

### 3.7 Regime Engine (`future/engines/regime_engine.py`)

**시장 상태 검증 엔진** (신호 발행 후 검증).

**추가된 지표**:
- `tick_volatility`: 최근 5캔들 평균 캔들레인지 %
- `volume_spike`: 현재 거래량 / 20캔들 평균 거래량 비율

### 3.8 Foreign Flow Engine (`future/engines/foreign_flow_engine.py`)

**외국인 수급 방향 필터** (스캘핑용으로 단순화).

| 반환값 | 조건 |
|--------|------|
| `FOREIGN_OK` | Z-Score가 정상 범위 (±1.0) |
| `FOREIGN_BLOCK` | Z-Score가 ±1.0 초과 (시장 왜곡 의심 → 진입 차단) |

### 3.9 Volatility Engine → Performance Engine → Risk Engine → Execution Engine

기존과 동일. ATR 기반 변동성 사이즈 조정 → Anti-Martingale 성과 사이징 → 리스크 검증 → KIS 주문 실행.

---

## 4. Trading Schedule

### 4.1 Session Definition

| 세션 | 시간 | 설명 |
|------|------|------|
| pre_market | 08:00~08:45 | 장전 준비 (야간 컨텍스트 수집) |
| **day_market** | **08:45~15:45** | **주간 매매** |
| gap | 16:00~18:00 | 휴식 |

### 4.2 Day Market Sub-Sessions

| 세션 | 시간 | 엔진 | 설명 |
|------|------|------|------|
| **Morning** | **08:45~09:30** | **MorningEngine** | 5개 전략 + 점수 시스템 |
| **Scalping** | **09:30~15:30** | **ScalpingEngine** | 4조건 AND 게이트 |
| **Force Close** | **15:30~15:45** | **강제청산** | 모든 포지션 시장가 청산 |

### 4.3 Data Pipeline Timeline

```
T+0s   08:45:00  세션 day_market 전환, WebSocket 구독 시작
       │
       ├─ 첫 틱 수신 → OrderFlowEngine, ExecutionPressureEngine 시작
       ├─ 첫 호가 수신 → OrderBookEngine 시작
       ├─ MorningEngine.activate() → 야간 컨텍스트 로드, 갭 계산
       │
       ├─ [모닝 엔진] 5개 전략 점수 평가 시작
       │   ├─ Overnight Score: 야간 CME/S&P/NASDAQ/환율/OI
       │   ├─ Gap Analysis: 시초 갭 방향 추종
       │   ├─ ORB: 08:45~08:50 범위 수집
       │   ├─ Foreign Open Attack: CVD+OI+Basis
       │   └─ Gap Fill: 갭 과대 역방향
       │
T+5m   08:50:00  ORB 5분 범위 확정, 이후 돌파 매매 가능
       │
T+45m  09:30:00  MorningEngine deactivate() → ScalpingEngine handoff
       │
T+59m  09:44:00  59개 캔들 확보 → 정상 스캘핑 4조건 AND 게이트 가동
       │
T+6h   15:30:00  강제청산 윈도우 시작
       │
T+6h15 15:45:00  장 마감
```

---

## 5. Folder Structure

```
future/
├── __init__.py
├── main.py                        # FastAPI 진입점
├── supervisor.py                  # Trading Supervisor
├── ws_manager.py                  # WebSocket 관리자
│
├── engines/
│   ├── __init__.py
│   ├── morning_engine.py          # 모닝 엔진 (5개 전략 + 점수) (신규)
│   ├── order_flow_engine.py       # CVD/Delta (신규)
│   ├── order_book_engine.py       # 호가불균형/OFI (신규)
│   ├── execution_pressure_engine.py # 체결강도 (신규)
│   ├── signal_engine.py           # 4조건 AND 스캘핑 신호
│   ├── regime_engine.py           # tick_volatility/volume_spike 추가
│   ├── foreign_flow_engine.py     # FOREIGN_OK/BLOCK 필터
│   ├── volatility_engine.py       # 변동성 분석
│   ├── performance_engine.py      # Anti-Martingale 사이징
│   ├── risk_engine.py             # 리스크 검증
│   ├── execution_engine.py        # KIS 주문 실행
│   └── telegram_agent.py          # 텔레그램 알림
│
├── store/
│   ├── mariadb_store.py           # MariaDB CRUD
│   └── sheets_store.py            # Google Sheets 백업
│
├── trash/                         # 미사용 파일 (Kiwoom, data_collector 등)
└── tools/                         # 유틸리티 스크립트
```
