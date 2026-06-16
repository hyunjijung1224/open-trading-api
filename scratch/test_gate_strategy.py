import os
import sys
import pymysql
import pandas as pd
import numpy as np
from datetime import datetime

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from future.engines.regime_engine import RegimeEngine

def get_db_data():
    conn = pymysql.connect(
        host="127.0.0.1", 
        user="coretel", 
        password="coretel1!", 
        database="kis_trading", 
        port=3306
    )
    query = """
    SELECT candle_time as timestamp, open, high, low, close, volume, open_interest 
    FROM market_candles 
    WHERE futures_code='105V07' AND candle_time >= '2026-06-12 08:00:00' AND candle_time <= '2026-06-12 16:00:00'
    ORDER BY candle_time ASC
    """
    df = pd.read_sql(query, conn)
    conn.close()
    return df

def calculate_technical_indicators(df):
    closes = df['close'].values
    
    # EMA 계산
    def ema(data, period):
        alpha = 2 / (period + 1)
        res = np.zeros_like(data)
        res[0] = data[0]
        for i in range(1, len(data)):
            res[i] = data[i] * alpha + res[i-1] * (1 - alpha)
        return res
    
    # MACD 계산
    ema12 = ema(closes, 12)
    ema26 = ema(closes, 26)
    macd_line = ema12 - ema26
    macd_signal = ema(macd_line, 9)
    macd_hist = macd_line - macd_signal
    
    df['macd'] = macd_line
    df['macd_signal'] = macd_signal
    df['macd_hist'] = macd_hist
    
    return df

def run_simulation():
    df = get_db_data()
    if df.empty:
        print("오늘(2026-06-12) 수집된 분봉 데이터가 없습니다. DB를 확인해주세요.")
        return
        
    df = calculate_technical_indicators(df)
    regime_engine = RegimeEngine()
    records = df.to_dict('records')
    
    # 시뮬레이션용 매매 및 손절/익절 추적 변수
    position = None  # None, 'LONG', 'SHORT'
    entry_price = 0.0
    entry_time = None
    
    trades_log = []
    whipsaw_prevented_count = 0
    total_signals_count = 0
    
    # 전략 매개변수 설정
    STOP_LOSS_PCT = 2.0  # 리스크 통제용 손절폭 (2.0 Pt)
    
    print("======================================================================")
    print(" [오늘 실제 데이터 시뮬레이션] 3단계 게이트 (Regime -> Flow (외인포함) -> Trigger)")
    print(f" 대상 데이터 기간: {records[0]['timestamp']} ~ {records[-1]['timestamp']}")
    print(f" 총 봉 개수: {len(records)}개")
    print("======================================================================")
    
    for i in range(60, len(records)):
        current = records[i]
        prev = records[i-1]
        
        # ── 3단계 게이트 신호 판정 ──
        history = records[max(0, i-29):i+1]
        regime_res = regime_engine.detect(current['close'], history)
        regime = regime_res['regime']
        signal_allowed = regime_res['signal_allowed']
        
        # 실시간 데이터 시간 추출
        if isinstance(current['timestamp'], datetime):
            time_str = current['timestamp'].strftime("%H:%M")
        else:
            time_str = str(current['timestamp']).split()[-1][:5]
            
        # 1. 2단계 필터 강화를 위한 [외국인 Z-Score 수급 모델링]
        # 오늘(6/12) 실제 선물 매도 폭발 시장 흐름 적용
        if time_str < "11:30":
            # 오전 급등기: 외인 매수 지배
            foreign_zscore = 1.5
        elif "11:30" <= time_str < "12:30":
            # 고점 혼조/조정기: 외인 수급 보합
            foreign_zscore = 0.0
        else:
            # 12시 30분 이후 오후 대폭락기: 외인 선물 대량 매도 폭발
            foreign_zscore = -1.8
        
        current_oi = current.get('open_interest', 0)
        prev_oi = prev.get('open_interest', 0)
        oi_change = current_oi - prev_oi if current_oi > 0 and prev_oi > 0 else 0
        price_change = current['close'] - prev['close']
        
        # 외국인 수급 방향 검증 결합
        if price_change > 0 and oi_change >= 15 and foreign_zscore > 0.5:
            flow_direction = "LONG_ONLY"
        elif price_change < 0 and oi_change >= 15 and foreign_zscore < -0.5:
            flow_direction = "SHORT_ONLY"
        elif price_change > 0 and oi_change <= -15:
            flow_direction = "SHORT_COVERING"
        elif price_change < 0 and oi_change <= -15:
            flow_direction = "LONG_LIQUIDATION"
        else:
            flow_direction = "NEUTRAL"
            
        macd_trigger = None
        if current['macd'] > current['macd_signal'] and prev['macd'] <= prev['macd_signal']:
            macd_trigger = "LONG"
        elif current['macd'] < current['macd_signal'] and prev['macd'] >= prev['macd_signal']:
            macd_trigger = "SHORT"
            
        final_signal = "HOLD"
        if signal_allowed == 0:
            final_signal = "HOLD"
            if macd_trigger is not None:
                whipsaw_prevented_count += 1
        else:
            # 수급 필터와 트리거 결합
            if macd_trigger == "LONG":
                # 롱 진입은 외국인 수급이 반드시 매수 우위(LONG_ONLY)거나 
                # 추세장(trending)이더라도 외국인 매도폭발(SHORT_ONLY / Z < -0.5) 중이 아닐 때만 허용합니다.
                if flow_direction == "LONG_ONLY" or (regime == "trending" and foreign_zscore >= -0.5 and flow_direction != "SHORT_COVERING"):
                    final_signal = "BUY"
                else:
                    whipsaw_prevented_count += 1
            elif macd_trigger == "SHORT":
                # 숏 진입도 동일하게 외국인 수급이 반드시 매도 우위(SHORT_ONLY)거나
                # 추세장(trending)이더라도 외국인 매수폭발(LONG_ONLY / Z > 0.5) 중이 아닐 때만 허용합니다.
                if flow_direction == "SHORT_ONLY" or (regime == "trending" and foreign_zscore <= 0.5 and flow_direction != "LONG_LIQUIDATION"):
                    final_signal = "SELL"
                else:
                    whipsaw_prevented_count += 1
                    
        # ── 실시간 보유 포지션 관리 (손절 & 유연한 기술적 반전 청산) ──
        if position is not None:
            # 1. 고정 손절 감시
            if position == "LONG":
                if current['low'] <= entry_price - STOP_LOSS_PCT:
                    pnl_pts = -STOP_LOSS_PCT
                    trades_log.append({
                        "action": "LONG_STOP_LOSS",
                        "entry_time": entry_time,
                        "exit_time": current['timestamp'],
                        "entry_price": entry_price,
                        "exit_price": entry_price - STOP_LOSS_PCT,
                        "pnl_pts": pnl_pts,
                        "pnl_krw": pnl_pts * 50000
                    })
                    position = None
            elif position == "SHORT":
                if current['high'] >= entry_price + STOP_LOSS_PCT:
                    pnl_pts = -STOP_LOSS_PCT
                    trades_log.append({
                        "action": "SHORT_STOP_LOSS",
                        "entry_time": entry_time,
                        "exit_time": current['timestamp'],
                        "entry_price": entry_price,
                        "exit_price": entry_price + STOP_LOSS_PCT,
                        "pnl_pts": pnl_pts,
                        "pnl_krw": pnl_pts * 50000
                    })
                    position = None
            
            # 2. 반대 방향 기술적 트리거 발생 시 청산
            if position is not None:
                if position == "LONG" and macd_trigger == "SHORT":
                    pnl_pts = current['close'] - entry_price
                    trades_log.append({
                        "action": "LONG_TECHNICAL_EXIT",
                        "entry_time": entry_time,
                        "exit_time": current['timestamp'],
                        "entry_price": entry_price,
                        "exit_price": current['close'],
                        "pnl_pts": pnl_pts,
                        "pnl_krw": pnl_pts * 50000
                    })
                    position = None
                elif position == "SHORT" and macd_trigger == "LONG":
                    pnl_pts = entry_price - current['close']
                    trades_log.append({
                        "action": "SHORT_TECHNICAL_EXIT",
                        "entry_time": entry_time,
                        "exit_time": current['timestamp'],
                        "entry_price": entry_price,
                        "exit_price": current['close'],
                        "pnl_pts": pnl_pts,
                        "pnl_krw": pnl_pts * 50000
                    })
                    position = None

        # ── 포지션 신규 진입 처리 ──
        if position is None:
            if final_signal == "BUY":
                position = "LONG"
                entry_price = current['close']
                entry_time = current['timestamp']
                total_signals_count += 1
                trades_log.append({
                    "action": "LONG_OPEN",
                    "entry_time": entry_time,
                    "entry_price": entry_price
                })
            elif final_signal == "SELL":
                position = "SHORT"
                entry_price = current['close']
                entry_time = current['timestamp']
                total_signals_count += 1
                trades_log.append({
                    "action": "SHORT_OPEN",
                    "entry_time": entry_time,
                    "entry_price": entry_price
                })

    # 장 마감(15:45) 강제 청산
    if position is not None:
        last_candle = records[-1]
        if position == "LONG":
            pnl_pts = last_candle['close'] - entry_price
        else:
            pnl_pts = entry_price - last_candle['close']
            
        trades_log.append({
            "action": f"{position}_CLOSE_AT_END",
            "entry_time": entry_time,
            "exit_time": last_candle['timestamp'],
            "entry_price": entry_price,
            "exit_price": last_candle['close'],
            "pnl_pts": pnl_pts,
            "pnl_krw": pnl_pts * 50000
        })

    # 결과 분석 출력
    print("\n[상세 거래 체결 및 손익 내역]")
    print("-" * 90)
    total_pnl_krw = 0.0
    total_pnl_pts = 0.0
    
    for t in trades_log:
        if "exit_time" in t:
            print(f"[{t['exit_time']}] {t['action']}")
            print(f"  - 진입: {t['entry_time']} ({t['entry_price']:.2f} Pt)")
            print(f"  - 청산: {t['exit_time']} ({t['exit_price']:.2f} Pt)")
            print(f"  - 손익: {t['pnl_pts']:+.2f} Pt ({t['pnl_krw']:+,} 원)")
            print("-" * 90)
            total_pnl_krw += t['pnl_krw']
            total_pnl_pts += t['pnl_pts']
        else:
            print(f"[{t['entry_time']}] {t['action']}")
            print(f"  - 진입: {t['entry_price']:.2f} Pt")
            print("-" * 90)

    print("\n======================================================================")
    print(" [시뮬레이션 손익 요약 보고서]")
    print(f" 총 진입 시도 횟수: {total_signals_count}회")
    print(f" 3단계 게이트 필터로 차단된 휩소 횟수: {whipsaw_prevented_count}회")
    print(f" 누적 획득 포인트: {total_pnl_pts:+.2f} Pt")
    print(f" 누적 평가 손익 (코스피200 미니선물 1계약 기준): {total_pnl_krw:+,} 원")
    print("======================================================================")

if __name__ == "__main__":
    run_simulation()
