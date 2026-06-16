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

def calculate_technical_indicators(df, fast, slow, signal_period):
    closes = df['close'].values
    
    def ema(data, period):
        alpha = 2 / (period + 1)
        res = np.zeros_like(data)
        res[0] = data[0]
        for i in range(1, len(data)):
            res[i] = data[i] * alpha + res[i-1] * (1 - alpha)
        return res
    
    ema_fast = ema(closes, fast)
    ema_slow = ema(closes, slow)
    macd_line = ema_fast - ema_slow
    macd_sig = ema(macd_line, signal_period)
    
    df['macd'] = macd_line
    df['macd_signal'] = macd_sig
    return df

def run_simulation(df, stop_loss_pts, take_profit_pts, use_flow_filter=False, oi=15, z=0.5):
    records = df.to_dict('records')
    regime_engine = RegimeEngine()
    
    position = None
    entry_price = 0.0
    entry_time = None
    trades_log = []
    
    for i in range(40, len(records)):
        current = records[i]
        prev = records[i-1]
        
        # 1. Regime (always signal allowed for relaxed backtest)
        signal_allowed = 1
        
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
        if use_flow_filter:
            if macd_trigger == "LONG":
                if flow_direction == "LONG_ONLY":
                    final_signal = "BUY"
            elif macd_trigger == "SHORT":
                if flow_direction == "SHORT_ONLY":
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
                    trades_log.append({"entry_time": entry_time, "exit_time": current['timestamp'], "side": "LONG", "entry_price": entry_price, "exit_price": entry_price - stop_loss_pts, "pnl": -stop_loss_pts, "type": "SL"})
                    position = None
            elif position == "SHORT":
                if current['high'] >= entry_price + stop_loss_pts:
                    trades_log.append({"entry_time": entry_time, "exit_time": current['timestamp'], "side": "SHORT", "entry_price": entry_price, "exit_price": entry_price + stop_loss_pts, "pnl": -stop_loss_pts, "type": "SL"})
                    position = None
                    
            # Take Profit
            if position is not None and take_profit_pts is not None:
                if position == "LONG":
                    if current['high'] >= entry_price + take_profit_pts:
                        trades_log.append({"entry_time": entry_time, "exit_time": current['timestamp'], "side": "LONG", "entry_price": entry_price, "exit_price": entry_price + take_profit_pts, "pnl": take_profit_pts, "type": "TP"})
                        position = None
                elif position == "SHORT":
                    if current['low'] <= entry_price - take_profit_pts:
                        trades_log.append({"entry_time": entry_time, "exit_time": current['timestamp'], "side": "SHORT", "entry_price": entry_price, "exit_price": entry_price - take_profit_pts, "pnl": take_profit_pts, "type": "TP"})
                        position = None
                        
            # Technical exit (MACD Reverse)
            if position is not None:
                if position == "LONG" and macd_trigger == "SHORT":
                    pnl = current['close'] - entry_price
                    trades_log.append({"entry_time": entry_time, "exit_time": current['timestamp'], "side": "LONG", "entry_price": entry_price, "exit_price": current['close'], "pnl": pnl, "type": "REV"})
                    position = None
                elif position == "SHORT" and macd_trigger == "LONG":
                    pnl = entry_price - current['close']
                    trades_log.append({"entry_time": entry_time, "exit_time": current['timestamp'], "side": "SHORT", "entry_price": entry_price, "exit_price": current['close'], "pnl": pnl, "type": "REV"})
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
        trades_log.append({"entry_time": entry_time, "exit_time": last['timestamp'], "side": position, "entry_price": entry_price, "exit_price": last['close'], "pnl": pnl, "type": "EOD"})
        
    return trades_log

if __name__ == "__main__":
    df_raw = get_db_data_for_day("2026-06-15")
    
    print("=== GRID SEARCH FOR TODAY ===")
    results = []
    
    # Grid search over MACD parameters, SL/TP
    for fast in [5, 8, 12]:
        for slow in [20, 26, 35]:
            if fast >= slow:
                continue
            df = df_raw.copy()
            df = calculate_technical_indicators(df, fast, slow, 9)
            
            for use_flow in [False, True]:
                # if use_flow is True, we relax OI to 5
                oi_val = 5 if use_flow else 0
                z_val = 0.2 if use_flow else 0.0
                
                for sl in [2.0, 3.0, 4.0]:
                    for tp in [4.0, 6.0, 8.0, None]:
                        trades = run_simulation(df, stop_loss_pts=sl, take_profit_pts=tp, use_flow_filter=use_flow, oi=oi_val, z=z_val)
                        
                        total_pnl = sum(t["pnl"] for t in trades)
                        win_trades = [t for t in trades if t["pnl"] > 0]
                        win_rate = len(win_trades) / len(trades) if trades else 0
                        
                        results.append({
                            "fast": fast, "slow": slow, "use_flow": use_flow, "sl": sl, "tp": tp,
                            "trades_count": len(trades), "win_rate": win_rate, "pnl": total_pnl
                        })
                        
    # Filter results that have between 8 and 15 trades (around 10 trades)
    filtered = [r for r in results if 8 <= r["trades_count"] <= 15]
    
    # Sort by PnL descending
    filtered = sorted(filtered, key=lambda x: x["pnl"], reverse=True)
    
    print(f"\nTop 5 Profitable Setups with ~10 trades today:")
    for idx, r in enumerate(filtered[:5]):
        tp_str = f"{r['tp']:.1f} Pt" if r['tp'] is not None else "None"
        print(f"[{idx+1}] PnL: {r['pnl']:+.2f} Pt ({r['pnl']*50000:+,} KRW) | WinRate: {r['win_rate']*100:.1f}% | Trades: {r['trades_count']}")
        print(f"    MACD({r['fast']},{r['slow']},9) | FlowFilter: {r['use_flow']} | SL: {r['sl']:.1f} Pt | TP: {tp_str}")
        
        # print details of the best setup
        if idx == 0:
            best_fast = r['fast']
            best_slow = r['slow']
            best_use_flow = r['use_flow']
            best_sl = r['sl']
            best_tp = r['tp']
            
    # Run the best setup again to show detail trade logs
    print("\nDetailed Trade Logs for the BEST setup:")
    df_best = df_raw.copy()
    df_best = calculate_technical_indicators(df_best, best_fast, best_slow, 9)
    best_oi = 5 if best_use_flow else 0
    best_z = 0.2 if best_use_flow else 0.0
    best_trades = run_simulation(df_best, stop_loss_pts=best_sl, take_profit_pts=best_tp, use_flow_filter=best_use_flow, oi=best_oi, z=best_z)
    for idx, t in enumerate(best_trades):
        print(f"  [{idx+1}] {t['entry_time']} ~ {t['exit_time']} | Side: {t['side']} | Entry: {t['entry_price']:.2f} | Exit: {t['exit_price']:.2f} | PnL: {t['pnl']:+.2f} Pt ({t['type']})")
