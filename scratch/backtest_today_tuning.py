# -*- coding: utf-8 -*-
import os
import sys
import pymysql
import pandas as pd
import numpy as np
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
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
    
    # EMA
    def ema(data, period):
        alpha = 2 / (period + 1)
        res = np.zeros_like(data)
        res[0] = data[0]
        for i in range(1, len(data)):
            res[i] = data[i] * alpha + res[i-1] * (1 - alpha)
        return res
    
    ema12 = ema(closes, 12)
    ema26 = ema(closes, 26)
    macd_line = ema12 - ema26
    macd_signal = ema(macd_line, 9)
    macd_hist = macd_line - macd_signal
    
    df['macd'] = macd_line
    df['macd_signal'] = macd_signal
    df['macd_hist'] = macd_hist
    return df

def run_simulation_tune(df, stop_loss_pts, use_flow_filter=False, oi=15, z=0.5):
    records = df.to_dict('records')
    regime_engine = RegimeEngine()
    
    position = None  # None, 'LONG', 'SHORT'
    entry_price = 0.0
    entry_time = None
    trades_log = []
    
    for i in range(20, len(records)):
        current = records[i]
        prev = records[i-1]
        
        # 1. Regime
        history = records[max(0, i-19):i+1]
        regime_res = regime_engine.detect(current['close'], history)
        regime = regime_res['regime']
        signal_allowed = regime_res['signal_allowed']
        
        # 2. Flow direction mock
        oi_change = current['open_interest'] - prev['open_interest']
        price_change = current['close'] - prev['close']
        zscore = 1.0 if price_change > 0 else -1.0
        
        flow_direction = "NEUTRAL"
        if price_change > 0 and oi_change >= oi and zscore > z:
            flow_direction = "LONG_ONLY"
        elif price_change < 0 and oi_change >= oi and zscore < -z:
            flow_direction = "SHORT_ONLY"
            
        # 3. MACD trigger
        macd_trigger = None
        if current['macd'] > current['macd_signal'] and prev['macd'] <= prev['macd_signal']:
            macd_trigger = "LONG"
        elif current['macd'] < current['macd_signal'] and prev['macd'] >= prev['macd_signal']:
            macd_trigger = "SHORT"
            
        final_signal = "HOLD"
        if signal_allowed == 1:
            if use_flow_filter:
                if macd_trigger == "LONG":
                    if flow_direction == "LONG_ONLY" or regime == "trending":
                        final_signal = "BUY"
                elif macd_trigger == "SHORT":
                    if flow_direction == "SHORT_ONLY" or regime == "trending":
                        final_signal = "SELL"
            else:
                if macd_trigger == "LONG":
                    final_signal = "BUY"
                elif macd_trigger == "SHORT":
                    final_signal = "SELL"
                    
        # Position Management
        if position is not None:
            # Stop Loss
            if position == "LONG":
                if current['low'] <= entry_price - stop_loss_pts:
                    trades_log.append({"side": "LONG", "pnl": -stop_loss_pts, "type": "SL"})
                    position = None
            elif position == "SHORT":
                if current['high'] >= entry_price + stop_loss_pts:
                    trades_log.append({"side": "SHORT", "pnl": -stop_loss_pts, "type": "SL"})
                    position = None
                    
            # Technical exit (MACD Reverse)
            if position is not None:
                if position == "LONG" and macd_trigger == "SHORT":
                    pnl = current['close'] - entry_price
                    trades_log.append({"side": "LONG", "pnl": pnl, "type": "REV"})
                    position = None
                elif position == "SHORT" and macd_trigger == "LONG":
                    pnl = entry_price - current['close']
                    trades_log.append({"side": "SHORT", "pnl": pnl, "type": "REV"})
                    position = None
                    
        # Entry
        if position is None:
            if final_signal == "BUY":
                position = "LONG"
                entry_price = current['close']
                entry_time = current['timestamp']
            elif final_signal == "SELL":
                position = "SHORT"
                entry_price = current['close']
                entry_time = current['timestamp']
                
    if position is not None:
        last = records[-1]
        pnl = (last['close'] - entry_price) if position == "LONG" else (entry_price - last['close'])
        trades_log.append({"side": position, "pnl": pnl, "type": "EOD"})
        
    return trades_log

if __name__ == "__main__":
    df = get_db_data_for_day("2026-06-15")
    df = calculate_technical_indicators(df)
    
    print("=== Stop Loss Tuning for Today (2026-06-15) ===")
    print("Setup: Regime + MACD (Bypass Flow Filter)")
    for sl in [2.0, 3.0, 4.0, 5.0]:
        trades = run_simulation_tune(df, stop_loss_pts=sl, use_flow_filter=False)
        total_pnl = sum(t["pnl"] for t in trades)
        win_trades = [t for t in trades if t["pnl"] > 0]
        win_rate = len(win_trades) / len(trades) if trades else 0
        print(f"SL: {sl:.1f} Pt | Trades: {len(trades)} | Win Rate: {win_rate*100:.1f}% | Total PnL: {total_pnl:+.2f} Pt ({total_pnl*50000:+,} KRW)")

    print("\nSetup: Strict (Regime + Flow Filter OI>=15)")
    for sl in [2.0, 3.0, 4.0, 5.0]:
        trades = run_simulation_tune(df, stop_loss_pts=sl, use_flow_filter=True, oi=15, z=0.5)
        total_pnl = sum(t["pnl"] for t in trades)
        win_trades = [t for t in trades if t["pnl"] > 0]
        win_rate = len(win_trades) / len(trades) if trades else 0
        print(f"SL: {sl:.1f} Pt | Trades: {len(trades)} | Win Rate: {win_rate*100:.1f}% | Total PnL: {total_pnl:+.2f} Pt ({total_pnl*50000:+,} KRW)")
