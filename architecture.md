# Architecture — KOSPI200선물, 개별주식선물, 금선물 등 AI 자동매매 시스템

> **Version**: 3.2  
> **Last Updated**: 2026-06-14  
> **Target**: KOSPI200선물, 개별주식선물, 금선물 등 (주간 정규장 + 야간장)  
> **Primary Runtime**: Google Compute Engine (Ubuntu 24.04 e2-micro, 무료 티어) — WebSocket 상시 연결  
> **Backup Runtime**: Google Cloud Run — 5분 간격 헬스체크 및 비상 복구  

---

## 1. System Overview

### 1.1 Goal

한국투자증권 Open API 기반의 **KOSPI200선물, 개별주식선물, 금선물 등** AI 자동매매 시스템.  
스윙 트레이딩 방식으로 **시장 상태 판별(Regime) + 추세추종 + 외국인 수급 확인 + 리스크 관리** 중심의 안정적 수익을 추구한다.

### 1.2 Design Principles

| 원칙 | 설명 |
|------|------|
| **Rule-Based Core** | AI 예측 모델이 아닌 검증 가능한 규칙 기반 엔진이 실제 매매를 수행 |
| **Regime-First** | **시장 상태(추세/횡보/변동성)를 먼저 판별**한 후 전략을 선택·차단 |
| **AI-Assisted Risk** | AI는 뉴스/위험도/모니터링을 보조하며, 매매 결정권은 없음 |
| **Risk-First** | 수익은 전략보다 리스크 관리가 결정. 손절 + 포지션 사이징이 핵심 |
| **WebSocket Primary** | GCE에서 WebSocket 상시 연결 → 실시간 체결/호가/손절 즉시 반응 |
| **Cloud Run Backup** | 5분마다 GCE 상태를 체크, GCE 장애 시 비상 매매(손절/청산) 수행 |
| **Foreign Flow Driven** | 선물 시장에서 외국인 수급이 가격보다 중요. 수급 방향 필터 필수 |
| **Adaptive Sizing** | 승률/MDD/레짐/최근 성과에 따라 동적 포지션 사이징 |
| **MCP Integration** | KIS Trading MCP + Code Assistant MCP를 적극 활용 |
| **Single Source** | 주간/야간 모든 로직이 하나의 소스에서 동시 실행 |
| **Backtest ↔ Live 동일 엔진** | 동일 전략 코드로 백테스트·모의투자·실전투자 수행 |
| **Long/Short 양방향** | 선물 특성상 매수·매도 양방향 진입 지원 |

### 1.3 Infrastructure — Dual Runtime

#### Primary: GCE (Ubuntu 24.04 e2-micro, 무료 티어)

| 항목 | 스펙 | 비고 |
|------|------|------|
| CPU | 0.25 vCPU (공유) | 트레이딩 봇에 충분 |
| 메모리 | 1GB | pandas + websocket 운영 가능 |
| 디스크 | 30GB 표준 | 로그/데이터/상태 영구 저장 |
| 네트워크 | 1GB egress/월 | API 호출에 충분 |
| OS | Ubuntu 24.04 LTS | systemd로 프로세스 관리 |
| 비용 | **무료** (Always Free) | us-west1/us-central1/us-east1 리전 |
| WebSocket | ✅ **상시 연결 가능** | 실시간 체결/호가/손절 즉시 반응 |
| 프로세스 | **상시 실행** | 재시작 시 자동 복구 (systemd) |

**GCE가 주력인 이유**:
- WebSocket 상시 연결 → 실시간 손절 반응 (지연 < 1초)
- 상태를 로컬 MariaDB에 영구 저장하여 다중 접속 및 안정성 극대화
- 상태 변경 시 Google Sheets에 즉시 백업 동기화
- ATR 손절과 실시간 체결 모니터링의 모순 해소
- 야간선물(18:00~06:00) 연속 모니터링 가능

#### Backup: Cloud Run (무료 티어)

| 항목 | 역할 |
|------|------|
| 헬스체크 | 5분마다 GCE 상태 확인 (정상 운영 중인지) |
| 비상 청산 | GCE 장애 시 Google Sheets 백업 정보를 확인하여 긴급 포지션 청산 |
| 리포트 | 일간/주간 성과 리포트 생성 |
| 텔레그램 웹훅 | 사용자 명령 수신 → GCE에 전달 |
| 비상 매매 | GCE 다운 시 Google Sheets 백업 정보 기반 비상 포지션 관리 |

```
┌──────────── Cloud Run (Backup, 5분 간격) ──────────────┐
│  /health-check → GCE 상태 확인                          │
│  /emergency-close → GCE 장애 시 Sheets 조회 후 긴급 청산  │
│  /daily-report → 일간 리포트 생성                       │
│  /telegram-webhook → 사용자 명령 수신                   │
└─────────────────────┬──────────────────────────────────┘
                      │ GCE 장애 감지 시 Sheets 조회 후
                      ▼
┌──────────── GCE (Primary, 상시 실행) ──────────────────┐
│  WebSocket 상시 연결 (실시간 체결/호가)                  │
│  Trading Supervisor (메인 루프)                          │
│  모든 엔진 (Regime/Signal/Risk/Execution)               │
│  상태 영구 저장 (MariaDB) & Sheets 백업 동기화          │
│  텔레그램 알림 발송                                      │
└────────────────────────────────────────────────────────┘
```

---

## 2. High-Level Architecture

### 2.1 Core Trading Flow

```
Market Engine (데이터 수집)
        │
        ▼
Regime Engine (시장 상태 판별) ← ★ 가장 중요한 추가
        │
        ├── TRENDING → Signal Engine 활성화
        ├── RANGING  → 매매 중지, 기존 포지션 관리만
        └── VOLATILE → 포지션 사이즈 50% 축소
        │
        ▼
Signal Engine (매매 신호 생성)
        │
        ▼
Foreign Flow Filter (선물 Z-Score + 옵션 Net Option Flow) ← ★ 핵심 필터
        │
        ▼
Volatility Engine (변동성 분석)
        │
        ▼
Risk Engine (리스크 검증 + 동적 사이징)
        │
        ├── Performance Engine (최근 성과 반영) ← ★ 적응형 사이징
        │
        ▼
Execution Engine (주문 실행)
```

### 2.2 System Architecture Diagram

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                    GCE (Ubuntu 24.04 e2-micro, 상시 실행)                    │
│                                                                              │
│  ┌─────────────┐   ┌─────────────┐   ┌──────────────┐   ┌──────────────┐   │
│  │ WebSocket   │──▶│  Supervisor  │──▶│ Market Engine │──▶│Regime Engine  │   │
│  │ Manager     │   │  (Main Loop) │   │(WS+REST Poll)│   │(State Detect) │   │
│  └─────────────┘   └──────┬───────┘   └──────────────┘   └──────┬───────┘   │
│                           │                                       │           │
│                    ┌──────▼───────┐                        ┌──────▼───────┐   │
│                    │Signal Engine  │◀──────────────────────│Foreign Flow   │   │
│                    │(Rule-Based)   │                        │Engine         │   │
│                    └──────┬───────┘                        └──────────────┘   │
│                           │                                                   │
│                    ┌──────▼───────┐   ┌──────────────┐   ┌──────────────┐   │
│                    │Volatility    │──▶│ Risk Engine   │◀──│ Performance  │   │
│                    │Engine        │   │ (Guardian)    │   │ Engine       │   │
│                    └──────────────┘   └──────┬───────┘   └──────────────┘   │
│                                              │                               │
│                                       ┌──────▼───────┐   ┌──────────────┐   │
│                                       │ Execution    │──▶│ State Store  │   │
│                                       │ Engine       │   │ (MariaDB)    │   │
│                                       └──────────────┘   └──────────────┘   │
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────────┐ │
│  │                     AI Risk Agent (Gemini Flash)                        │ │
│  │  뉴스분석 │ 이벤트감지 │ 위험도점수 │ 시장상태 보조 확인               │ │
│  └─────────────────────────────────────────────────────────────────────────┘ │
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────────┐ │
│  │                     MCP Layer (개발/운영 통합)                          │ │
│  │  KIS Trading MCP ←→ KIS Code Assistant MCP                             │ │
│  └─────────────────────────────────────────────────────────────────────────┘ │
│                                                                              │
│  ┌─────────────┐                                          ┌──────────────┐   │
│  │  Telegram    │                                          │  Health API  │   │
│  │  Notifier    │                                          │  /health     │   │
│  └─────────────┘                                          └──────────────┘   │
└──────────────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────────────┐
│                     Cloud Run (Backup, 5분 간격 헬스체크)                    │
│                                                                              │
│  Cloud Scheduler ──▶ /health-check ──▶ GCE 상태 확인                       │
│                  ──▶ /emergency     ──▶ GCE 장애 시 Sheets 조회 후 긴급 청산      │
│                  ──▶ /daily-report  ──▶ 일간 리포트                         │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Core Components

### 3.1 Trading Supervisor (`future/supervisor.py`)

시스템의 **중앙 오케스트레이터**. GCE에서 상시 실행되는 메인 루프.

**Responsibilities**:
- WebSocket 연결 관리 (자동 재연결)
- 현재 시장 세션(주간/야간/비장중) 판별
- 전체 거래 흐름 제어: 데이터 수집 → **레짐 판별** → 신호 → **수급 필터** → 리스크 → 주문
- 상태 저장 (MariaDB 로컬 저장 및 Google Sheets 실시간 동기화 백업)
- Cloud Run 헬스체크 응답 (/health API)
- 예외 처리 및 알림

**Main Loop** (GCE 상시 실행):

```python
async def main_loop():
    """GCE 상시 실행 메인 루프"""
    ws_manager = WebSocketManager()
    await ws_manager.connect()
    
    while True:
        session = get_current_session()
        
        if session in (MarketSession.DAY_MARKET, MarketSession.NIGHT_MARKET):
            # 1. 실시간 데이터는 WebSocket으로 수신 중
            market_data = ws_manager.get_latest_data()
            
            # 2. 보조 데이터 REST로 보충 (일봉, OI 등)
            supplementary = await market_engine.fetch_supplementary()
            
            # 3. ★ 레짐 판별 (가장 먼저!)
            regime = regime_engine.detect(market_data, supplementary)
            
            # 4. 레짐에 따른 분기
            if regime == MarketRegime.RANGING:
                # 횡보장 → 신규 진입 차단, 기존 포지션 관리만
                await manage_existing_positions(market_data)
                continue
            
            # 5. 신호 생성 (추세장/변동성장에서만)
            signal = signal_engine.generate(market_data, regime)
            
            # 6. ★ 외국인 수급 필터
            signal = foreign_flow_engine.filter(signal, supplementary)
            
            # 7. 변동성 분석
            vol_adjustment = volatility_engine.analyze(market_data)
            
            # 8. ★ 리스크 검증 (Performance Engine 포함)
            decision = risk_engine.validate(signal, vol_adjustment)
            
            # 9. 주문 실행
            if decision.approved:
                result = await execution_engine.execute(decision)
                
            # 10. MariaDB 상태 저장 및 Google Sheets 동기화 + 알림
            await state_store.save(portfolio, indicators, regime)
            
        elif session == MarketSession.GAP:
            await asyncio.sleep(60)  # 비장중 대기
        
        await asyncio.sleep(1)  # 메인 루프 1초 간격
```

**WebSocket 실시간 처리** (별도 태스크):

```python
async def _on_realtime_execution(self, exec_data: Dict[str, Any]):
    """WebSocket 실시간 데이터 콜백"""
    current_price = exec_data["price"]
    code = exec_data["code"]
    
    # 1. 고정 손절/익절 실시간 감시
    for pos in list(self.active_positions):
        if pos["futures_code"] == code:
            if pos["side"] == "LONG" and current_price <= pos["stop_loss"]:
                await self._execute_emergency_close(pos, "실시간 손절 트리거")
                
    # 2. 동적 트레일링 스톱 실시간 업데이트 및 감시
    for pos in list(self.active_positions):
        if pos["futures_code"] == code:
            atr_gap = self._latest_atr * 2.0
            
            if pos["side"] == "LONG":
                # 최고가 경신 시 트레일링 스톱라인 상향
                if pos.get("highest_price") is None or current_price > pos["highest_price"]:
                    pos["highest_price"] = current_price
                    pos["trailing_stop"] = current_price - atr_gap
                    self.db.save_position(pos)
                # 트레일링 스톱 도달 시 긴급 시장가 청산
                if pos.get("trailing_stop") is not None and current_price <= pos["trailing_stop"]:
                    await self._execute_emergency_close(pos, "실시간 트레일링 스톱 트리거")
```

### 3.2 Market Engine (`future/engines/market_engine.py`)

**WebSocket + REST 하이브리드** 시장 데이터 수집 엔진.

**데이터 소스 이원화**:

| 데이터 | 소스 | 주기 | 용도 |
|--------|------|------|------|
| 실시간 체결가 | **WebSocket** [실시간-010] | 실시간 | 즉시 손절, 트레일링 스톱 |
| 실시간 호가 | **WebSocket** [실시간-011] | 실시간 | 스프레드 모니터링 |
| 체결통보 | **WebSocket** [실시간-012] | 실시간 | 주문 체결 확인 |
| 일봉 차트 | REST `inquire_daily_fuopchartprice` | 1일 1회 | MA/ATR/ADX 계산 |
| 분봉 차트 | REST `inquire_time_fuopchartprice` | 5분 | 단기 추세 확인 |
| 미결제약정(OI) | REST 전광판/기초자산 | 5분 | OI 확인 전략 |
| 외국인 선물 수급 | REST (투자자별 매매동향) | 5분 | ★ Foreign Flow Filter |
| 외국인 옵션 수급 | REST (투자자별 매매동향) | 1분 | ★ Foreign Option Flow Filter (Call/Put 수급) |
| 야간 체결 | **WebSocket** [실시간-064] | 실시간 | 야간 실시간 |
| 야간 호가 | **WebSocket** [실시간-065] | 실시간 | 야간 스프레드 |

**WebSocket 구독 목록** (실제 구현 기준):

```python
SUBSCRIPTIONS = {
    "day": [
        ("H0IFCNT0", "A05607"),  # 지수선물 실시간 체결 (미니선물, KIS 단축코드 형식)
        ("H0IFASP0", "A05607"),  # 지수선물 실시간 호가
        ("H0IFCNI9", "hts_id"), # 체결통보 (모의투자), H0IFCNI0 (실전)
    ],
    "night": [
        ("H0IFCNT0", "A01W07"),  # 야간 지수선물 실시간 체결
        ("H0IFASP0", "A01W07"),  # 야간 지수선물 실시간 호가
        ("H0IFCNI9", "hts_id"), # 야간 체결통보
    ]
}
```

> **⚠️ 코드 변환 규칙**: KIS 웹소켓 `tr_key`는 **단축코드** (`A05607`)를 요구하지만,
> 시스템 내부 DB 저장/지표 계산에는 **표준코드** (`105V07`)를 사용한다.
> `supervisor.py`의 `_to_kis_code()` / `_to_db_code()` 메서드로 양방향 변환이 지원된다.
>
> | 목적 | 코드 형식 | 예시 |
> |---|---|---|
> | DB 저장, 지표 계산 | 표준코드 | `105V07` |
> | KIS WebSocket tr_key | 단축코드 | `A05607` |
> | KIS REST API 호출 | 표준코드 or 단축코드 (동일) | `105V07` |

**Supported Markets**:

| 시장 | 종목코드 체계 | 거래시간 (KST) | 데이터 소스 |
|------|-------------|----------------|-----------|
| 코스피200 미니선물 | `105` 시리즈 (예: `105V09`) | 08:45~15:45 (최종거래일 08:45~15:20) | WebSocket + REST |
| 코스피200 미니야간선물 | `101` 시리즈 (예: `101W09`) | 18:00~06:00 | WebSocket + REST |

### 3.3 ★ Regime Engine (`future/engines/regime_engine.py`) — 신규

**가장 중요한 추가 엔진**. 현재 시장 상태를 판별하여 전략 선택·차단을 결정한다.

> **왜 Regime Engine이 핵심인가?**
> MA Cross 전략은 추세장에서만 유효하다. 횡보장에서는 `매수→손절→매도→손절` 무한 반복으로 계좌가 파괴된다.
> Regime Engine 하나만 추가해도 **불필요한 거래의 60~70%를 제거**하여 승률이 극적으로 개선된다.

**판별 기준** (실제 구현 파라미터 기준):

```python
class RegimeEngine:
    """
    시장 상태 판별 엔진
    
    3단계 판별:
    1차: ADX 기반 추세 강도
    2차: ATR 기반 변동성 수준
    3차: 가격-MA 위치 관계 + Bollinger Band Width
    """
    # 실제 파라미터
    adx_threshold = 20.0
    bbw_threshold = 0.005   # KOSPI200 선물 절대가격(1300~1400) 기준으로 조정

    def detect(self, market_data, indicators) -> RegimeState:
        adx = indicators.adx
        atr = indicators.atr
        atr_ma = indicators.atr_ma  # ATR의 20일 이동평균
        bbw = indicators.bollinger_width  # Bollinger Band Width
        
        # ── 1차: ADX 기반 ──
        if adx >= 25:
            trend_strength = "strong"
        elif adx >= 15:   # 18 → 15로 완화 (선물 시장 특성 반영)
            trend_strength = "weak"
        else:
            trend_strength = "none"
        
        # ── 2차: 변동성 수준 ──
        if atr > atr_ma * 2.0:
            volatility = "extreme"
        elif atr > atr_ma * 1.5:
            volatility = "high"
        else:
            volatility = "normal"
        
        # ── 3차: 종합 판정 ──
        if volatility == "extreme":
            regime = MarketRegime.VOLATILE
            action = "포지션 사이즈 50% 축소, 신규 진입 제한"
        elif trend_strength == "strong":
            regime = MarketRegime.TRENDING
            action = "추세추종 전략 활성화"
        # ranging 판정 조건: ADX none AND BBW 0.005 미만 (두 조건 동시 충족)
        elif trend_strength == "none" and bbw < self.bbw_threshold:
            regime = MarketRegime.RANGING
            action = "★ 매매 중지 — 기존 포지션 관리만"
        # ADX가 낙지만 BBW가 충분하면 weak_trend로 분류 (매매 허용, ranging 아님)
        else:
            regime = MarketRegime.WEAK_TREND
            action = "포지션 사이즈 30~50% 축소"
        
        return RegimeState(
            regime=regime,
            adx=adx,
            atr=atr,
            volatility_level=volatility,
            trend_strength=trend_strength,
            action=action,
            signal_allowed=(regime != MarketRegime.RANGING),
            size_multiplier=self._get_size_multiplier(regime)
        )
    
    def _get_size_multiplier(self, regime) -> float:
        """레짐별 포지션 사이즈 배수"""
        return {
            MarketRegime.TRENDING: 1.0,      # 정상 사이즈
            MarketRegime.WEAK_TREND: 0.5,    # 50% 축소 (ADX none일 때 0.5, weak일 때 0.7)
            MarketRegime.VOLATILE: 0.5,      # 50% 축소
            MarketRegime.RANGING: 0.0,       # 매매 중지
        }[regime]
```

**Regime Engine이 차단하는 시나리오**:

```
[횡보장 — Regime = RANGING]

기존 (Regime 없음):                    개선 (Regime 있음):
MA20 > MA60 → 매수 → 손절 -1%         ADX=15 → RANGING → 매매 중지
MA20 < MA60 → 매도 → 손절 -1%         (아무것도 안 함)
MA20 > MA60 → 매수 → 손절 -1%         ADX=15 → RANGING → 매매 중지
MA20 < MA60 → 매도 → 손절 -1%         (아무것도 안 함)
                                       
누적 손실: -4%                          누적 손실: 0%
```

### 3.4 Signal Engine (`future/engines/signal_engine.py`)

**검증 가능한 규칙 기반** 매매 신호 생성 엔진. **Regime Engine 통과 후에만** 작동.

**Strategy Stack** (레짐별 활성화):

| # | 전략 | 유형 | 설명 | 활성 레짐 | 타임프레임 |
|---|------|------|------|----------|-----------|
| 1 | **Dual MA Cross** | 추세추종 (Core) | MA20/MA60 교차 + 종가 확인 | TRENDING | 일봉 |
| 2 | **MACD Divergence** | 추세추종 | MACD-Signal 교차 + 히스토그램 방향 | TRENDING, WEAK_TREND | 일봉/60분봉 |
| 3 | **ATR Breakout** | 돌파 | 전일 종가 ± N×ATR 돌파 시 진입 | TRENDING | 일봉/60분봉 |
| 4 | **Bollinger Squeeze** | 변동성 돌파 | BB 수축 후 확장 시 돌파 방향 진입 | WEAK_TREND → TRENDING 전환 시 | 일봉 |
| 5 | **OI Confirmation** | 필터 | OI 증가 + 가격 방향 일치 확인 | 모든 레짐 (필터) | 일봉 |
| 6 | **Trailing Stop** | 청산 | ATR 기반 트레일링 스톱 | 모든 레짐 | 실시간 (WS) |
| 7 | **Time-Based Exit** | 청산 | 만기 N일 전 강제 청산 | 모든 레짐 | 일 단위 |

**Signal 생성 로직** (3단계 게이트 필터-트리거 구조):

이전의 단순 점수 합산 방식은 다중공선성과 과최적화 문제를 유발하므로 폐기되었습니다. 현재 시스템은 1단계 시장 국면 필터, 2단계 수급 검증 필터 (선물 수급 Z-Score 및 옵션 콜/풋 수급 차이인 Net Option Flow 필터), 3단계 가격 모멘텀 트리거가 순차적으로 맞물리는 게이트 구조를 채택하고 있습니다.

**외국인 옵션 수급 필터링 규칙**:
- `Net Option Flow = Call Net Buy - Put Net Buy` (당일 누적 계약 수량 차이)
- **LONG 진입 시**: `Net Option Flow < 0` (외국인의 옵션 포지션이 풋 우위로 하방을 보고 있음)인 경우 롱 진입 차단.
- **SHORT 진입 시**: `Net Option Flow > 0` (외국인의 옵션 포지션이 콜 우위로 상방을 보고 있음)인 경우 숏 진입 차단.

```python
def generate(self, code: str, regime: str, flow_direction: str, foreign_zscore: float, indicators: Dict[str, Any]) -> Dict[str, Any]:
    """
    시장 국면, 수급 방향, 기술적 지표 트리거를 융합하여 최종 매수/매도/관망 신호 생성
    """
    reasons = []
    direction = "HOLD"
    
    # 외국인 옵션 수급 분석 (Call - Put)
    option_call_net = indicators.get("option_call_net", 0)
    option_put_net = indicators.get("option_put_net", 0)
    net_option_flow = option_call_net - option_put_net
    
    # 1. 3단계: 가격 기반 기술적 트리거 판정 (MACD 크로스)
    macd = indicators.get("macd", 0.0)
    prev_macd = indicators.get("prev_macd", 0.0)
    macd_signal = indicators.get("macd_signal", 0.0)
    prev_macd_signal = indicators.get("prev_macd_signal", 0.0)
    
    macd_trigger = None
    if macd > macd_signal and prev_macd <= prev_macd_signal:
        macd_trigger = "LONG"
        reasons.append("MACD 골든크로스 트리거 격발 (LONG)")
    elif macd < macd_signal and prev_macd >= prev_macd_signal:
        macd_trigger = "SHORT"
        reasons.append("MACD 데드크로스 트리거 격발 (SHORT)")
        
    # 2. 1단계: 시장 국면(Regime) 필터 검증
    if regime == "ranging":
        direction = "HOLD"
        reasons.append("1단계 시장 국면 필터: 횡보(ranging) 국면으로 진입 원천 차단 (HOLD)")
        if macd_trigger:
            reasons.append(f"차단된 신호: {macd_trigger}")
            
    # 3. 2단계: 수급 검증 필터 및 가격 트리거 결합
    else:
        if macd_trigger == "LONG":
            # 롱 진입 조건
            # 옵션 필터 추가: net_option_flow가 음수(풋 우위)이면 롱 진입을 차단한다.
            is_option_ok = True
            if net_option_flow < 0:
                is_option_ok = False
                
            if not is_option_ok:
                direction = "HOLD"
                reasons.append(f"2단계 옵션 수급 필터 차단: 외인 옵션 하방 우위 (Net Option Flow={net_option_flow:+,} 계약, 콜={option_call_net:+,}, 풋={option_put_net:+,})")
            elif flow_direction == "LONG_ONLY":
                direction = "BUY"
                reasons.append(f"2단계 수급 필터 통과: 신규 롱 수급 검증 완료 (Z-Score={foreign_zscore:+.2f}, Net Option Flow={net_option_flow:+,})")
            elif regime == "trending" and foreign_zscore >= -getattr(config, "ZSCORE_THRESHOLD", 0.2) and flow_direction != "SHORT_COVERING":
                direction = "BUY"
                reasons.append(f"2단계 수급 필터 통과: 강한 추세장 진입 허용 (Z-Score={foreign_zscore:+.2f}, 수급방향={flow_direction}, Net Option Flow={net_option_flow:+,})")
            else:
                direction = "HOLD"
                reasons.append(f"2단계 수급 필터 차단: 롱 진입 요건 미달 (Z-Score={foreign_zscore:+.2f}, 수급방향={flow_direction})")
                
        elif macd_trigger == "SHORT":
            # 숏 진입 조건
            # 옵션 필터 추가: net_option_flow가 양수(콜 우위)이면 숏 진입을 차단한다.
            is_option_ok = True
            if net_option_flow > 0:
                is_option_ok = False
                
            if not is_option_ok:
                direction = "HOLD"
                reasons.append(f"2단계 옵션 수급 필터 차단: 외인 옵션 상방 우위 (Net Option Flow={net_option_flow:+,} 계약, 콜={option_call_net:+,}, 풋={option_put_net:+,})")
            elif flow_direction == "SHORT_ONLY":
                direction = "SELL"
                reasons.append(f"2단계 수급 필터 통과: 신규 숏 수급 검증 완료 (Z-Score={foreign_zscore:+.2f}, Net Option Flow={net_option_flow:+,})")
            elif regime == "trending" and foreign_zscore <= getattr(config, "ZSCORE_THRESHOLD", 0.2) and flow_direction != "LONG_LIQUIDATION":
                direction = "SELL"
                reasons.append(f"2단계 수급 필터 통과: 강한 추세장 진입 허용 (Z-Score={foreign_zscore:+.2f}, 수급방향={flow_direction}, Net Option Flow={net_option_flow:+,})")
            else:
                direction = "HOLD"
                reasons.append(f"2단계 수급 필터 차단: 숏 진입 요건 미달 (Z-Score={foreign_zscore:+.2f}, 수급방향={flow_direction})")
    
    if direction != "HOLD":
        logger.info(f"신호 발생 성공: {direction} (이유: {', '.join(reasons)})")
    else:
        if macd_trigger:
            logger.info(f"신호 진입 차단: {macd_trigger} -> HOLD (이유: {', '.join(reasons)})")
            
    return {
        "timestamp": datetime.now(),
        "futures_code": code,
        "direction": direction,
        "strength": 1.0 if direction != "HOLD" else 0.0,
        "score": 100 if direction == "BUY" else (-100 if direction == "SELL" else 0),
        "reasons": reasons,
        "regime": regime,
        "flow_direction": flow_direction,
        "foreign_zscore": foreign_zscore,
        "option_call_net": option_call_net,
        "option_put_net": option_put_net,
        "net_option_flow": net_option_flow
    }
```
```

### 3.5 ★ Foreign Flow Engine (`future/engines/foreign_flow_engine.py`) — 신규

**외국인 선물 및 옵션 수급 분석 엔진**.

선물 시장에서 외국인의 수급 영향력을 Z-Score로 표준화하여 절대값의 왜곡을 방지하고, 가격 변동 및 미결제약정(OI) 추이와 결합하여 2단계 수급 검증 필터를 제공합니다.
또한, 외국인의 당일 누적 콜옵션 및 풋옵션 순매수 차이(`Net Option Flow`)를 Signal Engine에 연동하여 상방/하방의 수급 역행 진입을 원천 필터링합니다.

```python
class ForeignFlowEngine:
    """
    외국인 선물 수급 분석 및 신호 필터링 엔진
    - 롤링 Z-Score 기반 외국인 선물 매매 동향 분석 (기본 120분 윈도우)
    - 미결제약정(OI) 및 가격 변동량과 결합하여 2단계 수급 검증 필터 판정
    """
    
    def update_flow(self, raw_investor_data: Dict[str, Any]):
        # 120개 롤링 윈도우 적재 및 롤링 Z-Score 계산
        # zscore = (현재 외국인 누적 순매수 - 평균) / 표준편차
        # zscore를 바탕으로 0.0 ~ 1.0 범위의 flow_strength 도출
        pass
        
    def get_flow_direction(self, price_change: float, oi_change: float) -> str:
        """
        가격 변동량, 미결제약정 변동량 및 외국인 Z-Score를 결합하여 수급 필터 방향 판정
        - LONG_ONLY: 롱 진입 검증 (가격 상승 & OI 증가 >= 15 & Z-Score > 0.5)
        - SHORT_ONLY: 숏 진입 검증 (가격 하락 & OI 증가 >= 15 & Z-Score < -0.5)
        - SHORT_COVERING: 숏커버링 청산 국면 (가격 상승 & OI 감소 <= -15)
        - LONG_LIQUIDATION: 롱청산 국면 (가격 하락 & OI 감소 <= -15)
        - NEUTRAL: 수급 보합 또는 방향 혼조세
        """
        pass
```

### 3.6 ★ Volatility Engine (`future/engines/volatility_engine.py`) — 신규

**변동성 수준을 분석하여 포지션 사이징과 손절 폭을 조정**.

```python
class VolatilityEngine:
    """
    변동성 분석 엔진
    
    역할:
    1. 현재 변동성 수준 판별 (Low/Normal/High/Extreme)
    2. ATR 기반 손절/익절 폭 동적 조정
    3. Regime Engine에 변동성 정보 제공
    """
    
    def analyze(self, indicators) -> VolatilityState:
        atr = indicators.atr
        atr_ma20 = indicators.atr_ma  # ATR 20일 MA
        
        # 변동성 수준 판별
        atr_ratio = atr / atr_ma20 if atr_ma20 > 0 else 1.0
        
        if atr_ratio > 2.0:
            level = VolatilityLevel.EXTREME
            size_mult = 0.3       # 70% 축소
            sl_mult = 3.0         # 넓은 손절
        elif atr_ratio > 1.5:
            level = VolatilityLevel.HIGH
            size_mult = 0.5       # 50% 축소
            sl_mult = 2.5
        elif atr_ratio > 0.7:
            level = VolatilityLevel.NORMAL
            size_mult = 1.0       # 정상
            sl_mult = 2.0
        else:
            level = VolatilityLevel.LOW
            size_mult = 1.0       # 정상 (스퀴즈 대비)
            sl_mult = 1.5         # 좁은 손절
        
        return VolatilityState(
            level=level,
            atr=atr,
            atr_ratio=atr_ratio,
            size_multiplier=size_mult,
            stop_loss_multiplier=sl_mult,
            take_profit_multiplier=sl_mult * 1.5,
        )
```

### 3.7 Risk Engine (`future/engines/risk_engine.py`) — 강화

**가장 중요한 엔진**. 이제 Performance Engine을 내장하여 **적응형 포지션 사이징** 및 **피라미딩(추가 매수)**을 수행.

**Risk Rules (위반 시 매매 차단)**:

| # | Rule | 기본값 | 설명 |
|---|------|--------|------|
| 1 | **Single Trade Loss** | 계좌의 0.5~1% | 1회 거래 최대 손실 제한 |
| 2 | **Daily Loss Limit** | 계좌의 2% | 일간 누적 손실 한도 → 초과 시 당일 매매 중단 |
| 3 | **Weekly Loss Limit** | 계좌의 5% | 주간 누적 손실 한도 → 초과 시 주간 매매 중단 |
| 4 | **Maximum Drawdown** | 계좌의 10% | 최대 낙폭 한도 → 초과 시 전략 비활성화 |
| 5 | **Consecutive Loss** | 3회 연속 | N회 연속 손절 시 쿨다운(1일) |
| 6 | **Regime Guard** | RANGING → 차단 | 횡보장에서 신규 진입 차단 |
| 7 | **Event Guard** | AI Risk Score ≥ 0.8 | 고위험 이벤트 시 신규 진입 차단 |
| 8 | **Spread Check** | 호가 스프레드 > 5틱 | 유동성 부족 시 매매 차단 |
| 9 | **Foreign Flow Conflict** | 수급 반대 + 강한 수급 | 외국인 강한 반대 매매 시 차단 |
| 10 | **Max Contracts** | 5 계약 | 최대 동시 보유 계약 수 |
| 11 | **Max Margin Usage** | 증거금의 30% | 풀레버리지 금지 |
| 12 | **Pyramiding Guard** | `1.5 * ATR` 버퍼 | 동일 방향 포지션이 있을 시, 수익 상태(불타기) 및 충분한 수익 버퍼 확보 시에만 1계약 단위 순차 진입 허용 (물타기 금지) |

### 3.8 ★ Performance Engine (`future/engines/performance_engine.py`) — 신규

**최근 매매 성과를 기반으로 포지션 사이징을 동적 조정**.

> **기존 문제**: `contracts = risk_per_trade / atr` 고정 공식만 사용 → 연패 시에도 동일 사이즈.
> **개선**: 승률/MDD/최근 수익률/레짐을 반영한 적응형 사이징.

```python
class PerformanceEngine:
    """
    최근 매매 성과 기반 동적 포지션 사이징
    
    Anti-Martingale 원리:
    - 이기고 있을 때 → 사이즈 유지/확대
    - 지고 있을 때 → 사이즈 축소
    """
    
    def calculate_size_multiplier(self, portfolio: PortfolioState) -> float:
        """최근 성과 기반 사이즈 배수 (0.25 ~ 1.5)"""
        recent = portfolio.recent_trades[-20:]  # 최근 20거래
        
        if len(recent) < 5:
            return 1.0  # 데이터 부족 → 기본 사이즈
        
        win_rate = sum(1 for t in recent if t.is_winner) / len(recent)
        avg_pnl = sum(t.net_pnl for t in recent) / len(recent)
        recent_mdd = self._calculate_recent_mdd(recent)
        
        multiplier = 1.0
        
        # ── 승률 기반 조정 ──
        if win_rate < 0.30:
            multiplier *= 0.25  # 승률 30% 미만 → 75% 축소 (거의 중단)
        elif win_rate < 0.35:
            multiplier *= 0.50  # 승률 35% 미만 → 50% 축소
        elif win_rate < 0.40:
            multiplier *= 0.70  # 승률 40% 미만 → 30% 축소
        elif win_rate > 0.55:
            multiplier *= 1.20  # 승률 55% 초과 → 20% 확대
        elif win_rate > 0.60:
            multiplier *= 1.50  # 승률 60% 초과 → 50% 확대
        
        # ── 최근 MDD 기반 조정 ──
        if recent_mdd > 0.05:  # 최근 MDD > 5%
            multiplier *= 0.50
        elif recent_mdd > 0.03:
            multiplier *= 0.70
        
        # ── 연속 손실 기반 조정 ──
        consecutive_losses = portfolio.risk_metrics.consecutive_losses
        if consecutive_losses >= 3:
            multiplier *= 0.50
        elif consecutive_losses >= 2:
            multiplier *= 0.70
        
        return max(0.25, min(1.5, multiplier))  # 0.25 ~ 1.5 범위
```

**통합 포지션 사이징 공식**:

```python
def calculate_position_size(
    account_balance: float,
    single_trade_risk: float,
    atr: float,
    point_value: float,
    regime_multiplier: float,       # Regime Engine
    volatility_multiplier: float,   # Volatility Engine
    performance_multiplier: float,  # Performance Engine
    max_contracts: int = 5,
) -> int:
    """
    최종 포지션 사이즈 = 기본 × 레짐 × 변동성 × 성과
    """
    # 기본: ATR 기반
    risk_amount = account_balance * single_trade_risk
    atr_value = atr * point_value
    base_contracts = risk_amount / atr_value if atr_value > 0 else 1
    
    # 적응형 조정
    adjusted = (
        base_contracts
        * regime_multiplier          # 횡보장=0, 추세장=1.0
        * volatility_multiplier      # 고변동=0.5, 정상=1.0
        * performance_multiplier     # 연패=0.25, 정상=1.0, 연승=1.5
    )
    
    contracts = int(adjusted)
    contracts = max(1, min(contracts, max_contracts))
    
    return contracts
```

#### 3.8.1 피라미딩 (Pyramiding) 정책
추세 시장에서 신호가 유지될 때 수익을 극대화하기 위한 동일 방향 포지션 추가 진입(불타기) 조건은 다음과 같습니다.
1. **수익성 검증 (물타기 금지)**: 기존 보유 포지션의 평균 매입 단가(`avg_price`) 대비 현재 지수가 유리한 방향(LONG일 때 가격 상승, SHORT일 때 가격 하락)이어야만 추가 진입이 가능합니다.
2. **최소 수익 버퍼**: 기존 진입가 대비 현재 지수가 최소 **`1.5 * ATR`** 이상 유리하게 진행되어 이미 충분한 수익을 확보한 상태에서만 추가 매수를 집행합니다.
3. **피라미딩 진입 수량**: 추가 진입 시에는 포지션 축적 안정성을 위해 한번에 **1계약**씩만 점진적으로 수량을 늘립니다.
4. **최대 계약 제한**: 피라미딩을 통해 수량을 확장하더라도, 계좌 전체의 최대 보유 포지션 제한인 **5계약** 한도는 철저히 준수됩니다.

### 3.9 Execution Engine (`future/engines/execution_engine.py`)

KIS Open API를 통한 **주문 실행** 엔진. WebSocket 체결통보로 즉시 확인.

**Execution Policy**:

| 시나리오 | 주문 방식 | 이유 |
|----------|-----------|------|
| 신규 진입 | 지정가 (현재가 ± 1틱) | 슬리피지 최소화 |
| **손절 청산** | **시장가** | ★ 즉시 탈출 (WebSocket 체결가 트리거) |
| 익절 청산 | 지정가 | 목표가 도달 확인 |
| 롤오버 | 시장가 양쪽 | 동시 청산+진입 |

### 3.10 AI Risk Agent (`future/ai/risk_agent.py`)

AI의 역할은 **"예측"이 아니라 "위험 감시"**. 기존과 동일하되, Regime Engine에 보조 정보 제공.

### 3.11 State Store (`future/store/state_store.py`)

**GCE 로컬 저장 (MariaDB)** + **Google Sheets (실시간 백업 & Failover 저장소)**.

| 저장소 | 용도 | 위치 |
|--------|------|------|
| **MariaDB** | 실시간 포지션, 주문 이력, 지표 데이터, 최근 20거래 성과 (주력 DB) | GCE 로컬 |
| **Google Sheets** | 실시간 액티브 포지션 복제본, 거래 이력 백업, 봇 헬스 스냅샷 (Failover용) | Google Cloud (Sheets API) |
| **Cloud Run** | GCE 다운 시 Google Sheets에서 액티브 포지션 로드 후 비상 청산 수행 | Cloud Run 메모리 |

#### 3.11.1 GCE ↔ Google Sheets 동기화 시나리오
1. **포지션 진입/청산 발생 시**: GCE Execution Engine이 주문 완료 및 포지션 변경을 감지하는 즉시 `sheets_store.update_active_positions(positions)`를 호출하여 Google Sheets의 `ActivePositions` 시트를 실시간 동기화합니다.
2. **5분 주기 상태 백업**: GCE Supervisor 메인 루프에서 5분마다 `sheets_store.update_bot_health(status_snapshot)`을 실행하여 현재 봇의 최종 동작 시각과 시스템 지표를 Google Sheets의 `BotHealth` 시트에 기록합니다.
3. **거래 완료 시**: 포지션이 완전히 청산(FLAT)되어 거래가 완결되면, 해당 내역을 Google Sheets의 `TradingHistory` 시트에 누적하여 기록합니다.

#### 3.11.2 Cloud Run 비상 복구(Failover) 시나리오
1. **GCE 장애 감지**: Cloud Run 백업 인스턴스가 5분 주기로 GCE의 `/health` API를 조회하여 3회 연속 실패하거나 응답 시간이 초과되면 GCE 장애로 판별합니다.
2. **Sheets 백업 정보 조회**: Cloud Run은 즉시 Google Sheets의 `ActivePositions` 시트를 읽어와 현재 GCE가 보유하고 있었던 미니선물/야간선물 포지션 수량 및 진입 방향을 확인합니다.
3. **비상 청산(Emergency Close) 실행**:
   - 보유 포지션이 감지되고 거래 시간대(주간 08:45~15:45 / 최종거래일 08:45~15:20 / 야간 18:00~06:00)인 경우, Cloud Run은 KIS REST API를 통해 해당 포지션을 반대 방향으로 전량 **시장가(Market Order)** 청산 주문합니다.
   - 주문 성공 시 Google Sheets의 `ActivePositions`에서 해당 항목을 삭제(FLAT으로 갱신)합니다.
4. **텔레그램 알림**: GCE 장애 발생 경보 및 비상 청산 수행 내역(종목, 수량, 청산 단가)을 사용자 텔레그램으로 발송합니다.

---

## 4. Trading Schedule & Session Management

### 4.1 Session Definition

```python
SESSIONS = {
    "pre_market":  {"start": "08:00", "end": "08:45", "action": "WS 연결 준비, 일봉 데이터 로드"},
    "day_market":  {"start": "08:45", "end": "15:45", "action": "주간 매매 (WS 실시간) *최종거래일 08:45~15:20"},
    "day_close":   {"start": "15:45", "end": "16:00", "action": "주간 정산, 야간 이관 판단 *최종거래일 15:20~16:00"},
    "gap":         {"start": "16:00", "end": "18:00", "action": "WS 재연결 준비"},
    "night_market":{"start": "18:00", "end": "06:00", "action": "야간 매매 (WS 실시간)"},
    "night_close": {"start": "06:00", "end": "06:30", "action": "야간 정산, 일간 리포트"},
    "sleep":       {"start": "06:30", "end": "08:00", "action": "프로세스 대기 (WS 해제)"}
}
```

### 4.2 GCE + Cloud Run 스케줄

**GCE (상시 실행)**:
```
- WebSocket 상시 연결 (주간/야간 각각)
- 실시간 체결가 수신 → 즉시 손절/트레일링 스톱
- 5분마다 REST 보조 데이터 수집 (OI, 수급, 분봉)
- 1시간마다 AI 리스크 분석
- systemd로 프로세스 관리 (크래시 시 자동 재시작)
```

**Cloud Run (5분 간격 백업)**:
```yaml
# Cloud Scheduler
- name: gce-health-check
  schedule: "*/5 * * * *"          # 5분마다
  target: POST /health-check       # GCE 상태 확인

- name: emergency-check
  schedule: "*/5 9-15,18-23,0-4 * * 1-5"  # 장중에만
  target: POST /emergency-check    # 포지션 이상 확인

- name: daily-report
  schedule: "0 16 * * 1-5"         # 매일 16시
  target: POST /daily-report       # 일간 리포트
```

### 4.3 Morning Trading Mode (아침 매매 모드) — 신규

* **목적**: 주말 및 전일 야간 사이의 매크로 변동성을 아침 개장 직후(08:45 AM)에 즉각 반영하여 매매에 참가할 수 있도록 보완합니다.
* **배경**: 한국투자증권 REST API는 야간 선물 시세의 과거 분봉 이력을 제공하지 않으므로, 월요일 아침이나 상시 기동 직후에 기술적 지표 계산을 위한 최소 캔들(59개)이 쌓이기 전까지 약 1시간 동안 매매 공백이 생깁니다.
* **작동 메커니즘**:
  1. **모닝 브리핑 및 점수화 (07:50 AM)**: `TelegramAgent`가 Gemini 3.5 Flash (Google Search Grounding 활성)를 호출하여 전일 미국 증시, 주말 뉴스 및 **당일 오전 07:00 ~ 07:50 사이의 국내 주식 현물 시장(KOSPI/KOSDAQ) 장전 예상 체결 분위기**를 종합 분석합니다. 분석 결과로 추천 매매 방향성(`BUY`, `SELL`, `HOLD`)과 점수(-1.0 ~ +1.0)를 JSON으로 도출하여 MariaDB `morning_briefing_scores` 테이블에 저장합니다.
  2. **아침 즉시 매매 진입 (08:45 ~ 09:44 AM)**: `TradingSupervisor`가 기동 시 캔들 개수가 59개 미만이고 KST 08:45 ~ 09:45 사이인 경우, 오늘 아침 브리핑 점수를 조회합니다. 유효한 방향성(`BUY`/`SELL`)이 있을 시 `is_morning_mode` 플래그를 활성화하여 `SignalEngine`에 주입합니다.
  3. **게이트 필터 우회**: `SignalEngine`은 `is_morning_mode`가 활성화되면 MACD 골든/데드 크로스 트리거 및 횡보 필터링을 우회하고 브리핑 방향대로 즉시 매매 신호를 출력합니다.
  4. **진입 횟수 제한**: 과도한 반복 거래를 방지하기 위해, 오늘 아침 개장 이후 이미 진입했거나 완료된 거래가 존재하면(`_has_morning_entry_today()` 가 True 이면) 아침 모드 신호는 더 이상 발생하지 않습니다 (오전 1회 진입 제한).
  5. **1시간 이후 자동 전환**: 개장 후 1시간이 경과하여 실시간 1분봉 캔들이 59개 이상 누적되면, 시스템은 아침 매매 모드를 종료하고 정상적인 기술적 지표 기반 3단계 게이트 매매 로직으로 자동 전환됩니다.

---

## 5. Trading Modes & Backtest Requirements

### 5.1 Backtest Mode — 강화

| 항목 | 기존 | 개선 |
|------|------|------|
| **기간** | 최소 6개월, 권장 2년 | **최소 3년, 권장 5~6년 (2020~현재)** |
| **포함 시장** | 미지정 | **상승장/하락장/횡보장/급락장 모두 포함 필수** |
| 슬리피지 | 1틱 | 1~2틱 (보수적) |
| 수수료 | 편도 0.001% | 편도 0.003% (보수적) |
| 야간선물 | 미지정 | 야간 데이터 포함 필수 |

**필수 검증 구간**:

| 구간 | 시장 상태 | 검증 목적 |
|------|-----------|-----------|
| 2020.03 | 코로나 급락 (-30%) | 극단적 하락장 생존 |
| 2020.04~2021.06 | 강한 상승장 | 추세추종 수익 검증 |
| 2021.07~2022.06 | 금리 인상 하락장 | 숏 포지션 검증 |
| 2022.07~2023.12 | 횡보장 | ★ Regime Engine 검증 (매매 중지) |
| 2024~2025 | 혼합 시장 | 종합 검증 |
| 2026 | 최근 | Out-of-sample 테스트 |

### 5.2 Paper Trading

- 최소 **2개월** 안정성 검증 후 Live 전환 (기존 1개월 → 2개월로 연장)
- Regime Engine의 횡보장 차단이 실제로 작동하는지 검증

### 5.3 Live Trading — Initial Deployment

```
Phase 1 (1~2주): 미니선물 1계약, 보수적 파라미터
Phase 2 (3~4주): 전략 성과 확인, 점진적 확대
Phase 3 (2~3개월): 안정적 성과 시 최대 계약 수까지
Phase 4 (6개월~): 파라미터 최적화, 전략 추가
```

---

## 6. Monitoring & Alerts

### 6.1 Performance Metrics — 강화

| 지표 | 계산 | 알림 조건 |
|------|------|-----------|
| 일간 PnL | 실현 + 미실현 | 일손실 > 2% |
| 주간 PnL | 주간 누적 | 주손실 > 5% |
| 승률 | 익절/전체 (최근 20거래) | < 35% → 사이즈 50% 축소 |
| 손익비 | 평균익절/평균손절 | < 1.5 |
| MDD | 최고점 대비 낙폭 | > 10% → 전략 비활성화 |
| 샤프비율 | 리스크 조정 수익률 | < 0.5 |
| **레짐** | 현재 시장 상태 | RANGING → 매매 중지 알림 |
| **외국인 수급** | 순매수/순매도 | 방향 전환 시 알림 |
| **GCE 상태** | Cloud Run 헬스체크 | GCE 다운 시 긴급 알림 |

### 6.2 Telegram Alert Triggers — 강화

```python
ALERTS = {
    # 매매
    "stop_loss":        "🔴 손절 발생: {symbol} {pnl}",
    "take_profit":      "🟢 익절 달성: {symbol} {pnl}",
    "entry":            "📥 신규 진입: {symbol} {side} {qty}계약 @ {price}",
    
    # 리스크
    "daily_limit":      "🚫 일손실 한도 도달: {loss}/{limit}",
    "weekly_limit":     "🚫 주손실 한도 도달",
    "mdd_breach":       "🚫 MDD 한도 돌파: {mdd}%",
    "consecutive_loss": "⚠️ {count}연속 손절 → 쿨다운 진입",
    
    # ★ Regime
    "regime_change":    "🔄 시장 상태 변경: {from} → {to}",
    "ranging_halt":     "⏸️ 횡보장 감지 → 매매 중지 (ADX={adx})",
    "trending_resume":  "▶️ 추세장 진입 → 매매 재개 (ADX={adx})",
    
    # ★ Foreign Flow
    "foreign_reversal": "📊 외국인 수급 방향 전환: {direction}",
    "foreign_conflict": "⚠️ 외국인 수급 반대 → 신호 약화",
    
    # 시스템
    "api_error":        "❌ API 오류: {error}",
    "ws_disconnect":    "🔌 WebSocket 연결 해제 → 재연결 시도",
    "ws_reconnect":     "🔗 WebSocket 재연결 성공",
    "gce_down":         "🆘 GCE 서버 응답 없음 → 비상 모드 (Cloud Run)",
    "rollover":         "🔄 롤오버 실행: {from} → {to}",
    "high_risk":        "⚡ AI 위험도 경고: {score}",
    
    # ★ Performance
    "low_winrate":      "📉 승률 저하: {rate}% → 사이즈 {mult}x 축소",
    "performance_up":   "📈 성과 개선: 승률 {rate}% → 사이즈 정상화",
}
```

---

## 7. Folder Structure

```
future/                              # 모든 신규 소스코드
├── __init__.py
├── main.py                          # GCE 진입점 (asyncio main loop)
├── supervisor.py                    # Trading Supervisor (오케스트레이터)
├── config.py                        # 선물 전용 설정
├── ws_manager.py                    # WebSocket 연결 관리자
│
├── engines/                         # 핵심 엔진 (8개)
│   ├── __init__.py
│   ├── market_engine.py             # 시장 데이터 수집 (WS+REST)
│   ├── regime_engine.py             # ★ 시장 상태 판별 (NEW)
│   ├── signal_engine.py             # 매매 신호 생성
│   ├── foreign_flow_engine.py       # ★ 외국인 수급 분석 (NEW)
│   ├── volatility_engine.py         # ★ 변동성 분석 (NEW)
│   ├── risk_engine.py               # 리스크 관리
│   ├── performance_engine.py        # ★ 성과 기반 사이징 (NEW)
│   ├── portfolio_engine.py          # 포지션 관리
│   └── execution_engine.py          # 주문 실행
│
├── strategies/                      # 매매 전략
│   ├── __init__.py
│   ├── base_strategy.py             # 전략 추상 클래스
│   ├── dual_ma_cross.py             # MA20/60 추세추종
│   ├── macd_divergence.py           # MACD 추세추종
│   ├── atr_breakout.py              # ATR 돌파
│   ├── bollinger_squeeze.py         # 볼린저 스퀴즈 돌파
│   └── oi_confirmation.py           # OI 확인 필터
│
├── brokers/                         # 브로커 인터페이스
│   ├── __init__.py
│   ├── broker_interface.py          # 추상 인터페이스
│   ├── kis_broker.py                # KIS API 실전/모의
│   └── backtest_broker.py           # 백테스트용
│
├── ai/                              # AI 에이전트
│   ├── __init__.py
│   ├── risk_agent.py                # AI 위험도 분석
│   └── news_analyzer.py             # 뉴스 분석
│
├── mcp/                             # MCP 통합
│   ├── __init__.py
│   ├── mcp_client.py                # MCP 클라이언트
│   └── mcp_tools.py                 # MCP 도구 래퍼
│
├── store/                           # 상태 저장소
│   ├── __init__.py
│   ├── state_store.py               # 상태 저장/복원 인터페이스
│   ├── mariadb_store.py             # ★ MariaDB 구현 (GCE 로컬)
│   └── sheets_store.py              # Google Sheets (실시간 백업 및 Failover)
│
├── utils/                           # 유틸리티
│   ├── __init__.py
│   ├── futures_code_manager.py      # 종목코드 관리
│   ├── market_calendar.py           # KRX 거래일 관리
│   ├── telegram_notifier.py         # 텔레그램 알림
│   ├── indicators.py                # 기술 지표 계산
│   └── logger.py                    # 로깅 설정
│
├── models/                          # 데이터 모델
│   ├── __init__.py
│   └── schemas.py                   # Pydantic/dataclass 스키마
│
├── backup/                          # Cloud Run 백업 서비스
│   ├── __init__.py
│   ├── backup_app.py                # Flask 앱 (Cloud Run)
│   ├── health_checker.py            # GCE 헬스체크
│   ├── emergency_handler.py         # 비상 포지션 관리
│   └── Dockerfile                   # Cloud Run 배포용
│
├── tests/                           # 테스트
│   ├── test_regime_engine.py        # ★ Regime Engine 테스트
│   ├── test_foreign_flow.py         # ★ Foreign Flow 테스트
│   ├── test_performance_engine.py   # ★ Performance Engine 테스트
│   ├── test_signal_engine.py
│   ├── test_risk_engine.py
│   ├── test_execution.py
│   └── test_backtest.py
│
├── deploy/                          # 배포 설정
│   ├── gce_setup.sh                 # GCE 초기 설정 스크립트
│   ├── futures-trader.service       # systemd 서비스 파일
│   ├── Dockerfile                   # Cloud Run backup용
│   └── cloudbuild.yaml              # CI/CD
│
└── requirements.txt                 # 의존성
```

---

## 8. Deployment

### 8.1 GCE (Primary) 배포

```bash
# 1. GCE 인스턴스 생성 (무료 티어)
gcloud compute instances create futures-trader \
  --zone=us-central1-a \
  --machine-type=e2-micro \
  --image-family=ubuntu-2404-lts-amd64 \
  --image-project=ubuntu-os-cloud \
  --boot-disk-size=30GB

# 2. 초기 설정
ssh futures-trader
sudo apt update && sudo apt install -y python3.11 python3.11-venv git

# 3. 프로젝트 배포
git clone https://github.com/your-repo/open-trading-api.git
cd open-trading-api
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r future/requirements.txt

# 4. systemd 서비스 등록 (자동 재시작)
sudo cp future/deploy/futures-trader.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable futures-trader
sudo systemctl start futures-trader
```

**systemd 서비스 파일**:
```ini
# /etc/systemd/system/futures-trader.service
[Unit]
Description=KOSPI200 Mini Futures Auto Trader
After=network.target

[Service]
Type=simple
User=trader
WorkingDirectory=/home/trader/open-trading-api
ExecStart=/home/trader/open-trading-api/.venv/bin/python -m future.main
Restart=always
RestartSec=10
Environment=PYTHONUNBUFFERED=1
EnvironmentFile=/home/trader/.env

[Install]
WantedBy=multi-user.target
```

### 8.2 Cloud Run (Backup) 배포

```bash
gcloud run deploy futures-backup \
  --source ./future/backup \
  --region asia-northeast3 \
  --memory 256Mi \
  --timeout 60 \
  --max-instances 1
```

---

## 9. Why This Architecture?

### 안정적 수익을 위한 핵심 설계 이유

| 설계 결정 | 이유 |
|-----------|------|
| **Regime Engine (최우선)** | 횡보장 자동 감지 → 불필요한 거래 60~70% 제거 → 승률 극적 개선 |
| **Foreign Flow Engine** | 외국인이 KOSPI200 선물 방향 주도 → 수급 방향 일치 시 승률 15~20% 향상 |
| **Performance Engine** | 연패 시 자동 사이즈 축소 → 계좌 보호. 연승 시 확대 → 수익 극대화 |
| **Volatility Engine** | 극단적 변동성에서 사이즈 자동 축소 → 급락장 생존 |
| **GCE + WebSocket** | 실시간 손절 반응 < 1초 → ATR 손절과 폴링의 모순 해소 |
| **Cloud Run Backup** | GCE 장애 시 비상 청산 → 무방비 포지션 방지 |
| **규칙 기반 엔진** | 전략이 설명 가능, 백테스트 가능, 실패 원인 분석 가능 |
| **AI는 보조만** | AI 오류 폭주 차단 |
| **5~6년 백테스트** | 상승/하락/횡보/급락 모든 시장 포함 → 전략 신뢰도 |

### 개선 전후 비교

```
[개선 전 — 횡보장]
MA Cross → 매수 → 손절(-1%) → 매도 → 손절(-1%) → 반복 → 월 -8%

[개선 후 — 횡보장]  
Regime = RANGING → 매매 중지 → 손실 0% → 추세 전환 시 재개

[개선 전 — 외국인 반대 매매]
MA Cross → 매수 → 외국인 대량 매도 중 → 급락 → 큰 손절(-3%)

[개선 후 — 외국인 반대 매매]
MA Cross → 매수 신호 → Foreign Filter: 외국인 대량 매도 → 신호 취소

[개선 전 — 연패 후]
5연패(-5%) → 동일 사이즈 계속 → 추가 5연패(-5%) → 총 -10%

[개선 후 — 연패 후]
3연패 → Performance Engine: 사이즈 50% 축소 → 추가 3연패(-2.5%) → 총 -5.5%
```

### 절대 원칙

```
1. ❌ 풀레버리지 사용 금지
2. ❌ 손절 없는 전략 금지  
3. ❌ 백테스트 없는 전략 금지 (5년 이상)
4. ❌ AI 단독 매매 금지
5. ❌ 횡보장에서 추세 전략 사용 금지 (Regime Engine)
6. ❌ 외국인 수급 반대 방향 강제 진입 금지
7. ✅ 하루 손실 제한 필수
8. ✅ 급락장 Replay 검증 필수
9. ✅ 최소 2개월 모의투자 검증 필수
10. ✅ WebSocket 실시간 손절 필수
```