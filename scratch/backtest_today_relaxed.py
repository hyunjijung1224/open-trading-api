# -*- coding: utf-8 -*-
import os
import sys
import pymysql
import pandas as pd
import numpy as np
from datetime import datetime

# sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
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

def run_simulation(df, setup_name, signal_allowed_override=False, use_flow_filter=False, min_oi_change=15, min_zscore=0.5):
    records = df.to_dict('records')
    regime_engine = RegimeEngine()
    
    position = None  # None, 'LONG', 'SHORT'
    entry_price = 0.0
    entry_time = None
    trades_log = []
    total_signals_count = 0
    STOP_LOSS_PCT = 2.0  # 2.0 Pt
    
    # Mock foreign flows for Z-score (using price changes as a proxy for flow zscore)
    # in real trading we fetch from DB, here we mock it
    # We will simulate zscore based on price movement
    
    for i in range(20, len(records)):
        current = records[i]
        prev = records[i-1]
        
        # 1. Regime
        history = records[max(0, i-19):i+1]
        regime_res = regime_engine.detect(current['close'], history)
        regime = regime_res['regime']
        signal_allowed = regime_res['signal_allowed'] if not signal_override else 1
        
        # 2. Flow direction mock
        oi_change = current['open_interest'] - prev['open_interest']
        price_change = current['close'] - prev['close']
        
        # simple proxy: if price goes up, zscore is positive
        zscore = 1.0 if price_change > 0 else -1.0
        
        flow_direction = "NEUTRAL"
        if price_change > 0 and oi_change >= min_oi_change and zscore > min_zscore:
            flow_direction = "LONG_ONLY"
        elif price_change < 0 and oi_change >= min_oi_change and zscore < -min_zscore:
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
                if current['low'] <= entry_price - STOP_LOSS_PCT:
                    pnl = -STOP_LOSS_PCT
                    trades_log.append({
                        "entry_time": entry_time, "exit_time": current['timestamp'],
                        "side": "LONG", "entry_price": entry_price, "exit_price": entry_price - STOP_LOSS_PCT,
                        "pnl": pnl, "type": "SL"
                    })
                    position = None
            elif position == "SHORT":
                if current['high'] >= entry_price + STOP_LOSS_PCT:
                    pnl = -STOP_LOSS_PCT
                    trades_log.append({
                        "entry_time": entry_time, "exit_time": current['timestamp'],
                        "side": "SHORT", "entry_price": entry_price, "exit_price": entry_price + STOP_LOSS_PCT,
                        "pnl": pnl, "type": "SL"
                    })
                    position = None
                    
            # Technical exit (MACD Reverse)
            if position is not None:
                if position == "LONG" and macd_trigger == "SHORT":
                    pnl = current['close'] - entry_price
                    trades_log.append({
                        "entry_time": entry_time, "exit_time": current['timestamp'],
                        "side": "LONG", "entry_price": entry_price, "exit_price": current['close'],
                        "pnl": pnl, "type": "REV"
                    })
                    position = None
                elif position == "SHORT" and macd_trigger == "LONG":
                    pnl = entry_price - current['close']
                    trades_log.append({
                        "entry_time": entry_time, "exit_time": current['timestamp'],
                        "side": "SHORT", "entry_price": entry_price, "exit_price": current['close'],
                        "pnl": pnl, "type": "REV"
                    })
                    position = None
                    
        # Entry
        if position is None:
            if final_signal == "BUY":
                position = "LONG"
                entry_price = current['close']
                entry_time = current['timestamp']
                total_signals_count += 1
            elif final_signal == "SELL":
                position = "SHORT"
                entry_price = current['close']
                entry_time = current['timestamp']
                total_signals_count += 1
                
    # End of Day force close
    if position is not None:
        last = records[-1]
        pnl = (last['close'] - entry_price) if position == "LONG" else (entry_price - last['close'])
        trades_log.append({
            "entry_time": entry_time, "exit_time": last['timestamp'],
            "side": position, "entry_price": entry_price, "exit_price": last['close'],
            "pnl": pnl, "type": "EOD"
        })
        
    return trades_log

if __name__ == "__main__":
    date_str = "2026-06-15"
    df = get_db_data_for_day(date_str)
    if df.empty:
        print("No data found.")
        sys.exit(1)
        
    df = calculate_technical_indicators(df)
    
    # We will test 4 different setups:
    # 1. Strict (Current Setup: Regime + Flow (OI>=15, Z>=0.5) + MACD)
    # 2. Regime + MACD only (Bypass Flow Filter)
    # 3. No Regime + No Flow (Pure MACD Trigger)
    # 4. Relaxed Flow Filter (Regime + Flow (OI>=5, Z>=0.2) + MACD)
    
    setups = [
        {"name": "1. Strict (Regime + Flow Filter OI>=15)", "signal_override": False, "use_flow_filter": True, "oi": 15, "z": 0.5},
        {"name": "2. Regime + MACD (Bypass Flow Filter)", "signal_override": False, "use_flow_filter": False, "oi": 15, "z": 0.5},
        {"name": "3. Pure MACD (No Regime Block, No Flow Filter)", "signal_override": True, "use_flow_filter": False, "oi": 15, "z": 0.5},
        {"name": "4. Relaxed Flow (Regime + Flow Filter OI>=5)", "signal_override": False, "use_flow_filter": True, "oi": 5, "z": 0.2}
    ]
    
    for setup in setups:
        signal_override = setup["signal_override"]
        use_flow_filter = setup["use_flow_filter"]
        oi = setup["oi"]
        z = setup["z"]
        
        trades = run_simulation(df, setup["name"], signal_allowed_override=signal_override, use_flow_filter=use_flow_filter, min_oi_change=oi, min_zscore=z)
        
        total_pnl = sum(t["pnl"] for t in trades)
        win_trades = [t for t in trades if t["pnl"] > 0]
        win_rate = len(win_trades) / len(trades) if trades else 0
        total_krw = total_pnl * 50000
        
        print(f"\n==================================================")
        print(f" Setup: {setup['name']}")
        print(f" Total Trades: {len(trades)}")
        print(f" Win Rate: {win_rate*100:.1f}% ({len(win_trades)}/{len(trades)})")
        print(f" PnL Points: {total_pnl:+.2f} Pt")
        print(f" PnL KRW: {total_krw:+,} KRW")
        print(f"==================================================")
        for idx, t in enumerate(trades):
            print(f"  [{idx+1}] {t['entry_time']} ~ {t['exit_time']} | Side: {t['side']} | Entry: {t['entry_price']:.2f} | Exit: {t['exit_price']:.2f} | PnL: {t['pnl']:+.2f} Pt ({t['type']})")
