# -*- coding: utf-8 -*-
"""
이번 주(6월 8일 ~ 6월 12일) 미니선물(105V07) 실제 데이터를 기반으로 한 전략 시뮬레이션.
- 1단계(시장 레짐) + 3단계(MACD 지표) 결합 (2단계 외인 수급 필터링 제외)
- 일 단위 데이트레이딩 (15:45 강제 청산)
- 코스피200 미니선물 1계약 기준 (포인트당 50,000원)
"""
import os
import sys
import pymysql
import pandas as pd
import numpy as np
from datetime import datetime

# 터미널 UTF-8 출력 강제 설정
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from future.engines.regime_engine import RegimeEngine

def get_db_data_for_day(date_str):
    conn = pymysql.connect(
        host="127.0.0.1", 
        user="coretel", 
        password="coretel1!", 
        database="kis_trading", 
        port=3306
    )
    query = f"""
    SELECT candle_time as timestamp, open, high, low, close, volume, open_interest 
    FROM market_candles 
    WHERE futures_code='105V07' 
      AND candle_time >= '{date_str} 09:00:00' 
      AND candle_time <= '{date_str} 15:45:00'
    ORDER BY candle_time ASC
    """
    df = pd.read_sql(query, conn)
    conn.close()
    return df

def calculate_technical_indicators(df):
    if df.empty:
        return df
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

def run_simulation_for_day(date_str):
    df = get_db_data_for_day(date_str)
    if df.empty:
        print(f"[*] {date_str} 수집된 분봉 데이터가 없습니다. 건너뜁니다.")
        return None, 0, 0
        
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
    
    print(f"\n======================================================================")
    print(f" [Day: {date_str} Simulation] 2-Gate Mode (Regime -> Trigger)")
    print(f" Data Period: {records[0]['timestamp']} ~ {records[-1]['timestamp']}")
    print(f" Total candles: {len(records)} count")
    print(f" (Foreign flows filter bypassed - 1-Gate Regime & 3-Gate MACD only)")
    print("======================================================================")
    
    for i in range(60, len(records)):
        current = records[i]
        prev = records[i-1]
        
        # ── 1단계: Regime 신호 판정 ──
        history = records[max(0, i-29):i+1]
        regime_res = regime_engine.detect(current['close'], history)
        regime = regime_res['regime']
        signal_allowed = regime_res['signal_allowed']
        
        # ── 3단계: MACD 트리거 판정 ──
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
            # 외인 수급 필터를 거치지 않고 바로 매매 진입 허용
            if macd_trigger == "LONG":
                final_signal = "BUY"
            elif macd_trigger == "SHORT":
                final_signal = "SELL"
                    
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

    # 결과 분석 출력 및 일별 집계
    day_pnl_pts = 0.0
    day_pnl_krw = 0.0
    
    print("\n[Trade Logs]")
    print("-" * 90)
    for t in trades_log:
        if "exit_time" in t:
            print(f"[{t['exit_time']}] {t['action']}")
            print(f"  - Entry: {t['entry_time']} ({t['entry_price']:.2f} Pt)")
            print(f"  - Exit: {t['exit_time']} ({t['exit_price']:.2f} Pt)")
            print(f"  - PnL: {t['pnl_pts']:+.2f} Pt ({t['pnl_krw']:+,} KRW)")
            print("-" * 90)
            day_pnl_krw += t['pnl_krw']
            day_pnl_pts += t['pnl_pts']
        else:
            print(f"[{t['entry_time']}] {t['action']}")
            print(f"  - Entry: {t['entry_price']:.2f} Pt")
            print("-" * 90)

    print(f" [{date_str} Summary] Entry: {total_signals_count} | Whipsaw Blocked: {whipsaw_prevented_count} | PnL: {day_pnl_pts:+.2f} Pt ({day_pnl_krw:+,} KRW)")
    print("======================================================================")
    
    return trades_log, day_pnl_pts, day_pnl_krw

def main():
    dates = ["2026-06-08", "2026-06-09", "2026-06-10", "2026-06-11", "2026-06-12"]
    
    total_week_pnl_pts = 0.0
    total_week_pnl_krw = 0.0
    days_tested = 0
    
    for date_str in dates:
        _, day_pts, day_krw = run_simulation_for_day(date_str)
        if day_pts is not None or day_krw != 0:
            total_week_pnl_pts += day_pts
            total_week_pnl_krw += day_krw
            days_tested += 1
            
    print("\n\n" + "=" * 90)
    print(" [Weekly Backtest Summary (6/8 ~ 6/12)]")
    print(f" Tested Days: {days_tested} days")
    print(f" Total points: {total_week_pnl_pts:+.2f} Pt")
    print(f" Cumulative PnL (KOSPI200 Mini 1 contract): {total_week_pnl_krw:+,} KRW")
    print("=" * 90)

if __name__ == "__main__":
    main()
