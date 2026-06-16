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

def run_partial_tp_simulation(df, stop_loss_pts, take_profit_pts, move_to_breakeven=False, use_flow_filter=True, oi=5, z=0.2):
    records = df.to_dict('records')
    
    # Position state:
    # None or dictionary:
    # {
    #   "side": "LONG" / "SHORT",
    #   "entry_price": float,
    #   "entry_time": datetime,
    #   "half_tp_hit": bool,
    #   "half_tp_price": float,
    #   "half_tp_time": datetime
    # }
    pos = None
    trades_log = []
    total_entries = 0
    
    for i in range(40, len(records)):
        current = records[i]
        prev = records[i-1]
        
        # 1. Flow & Signals (using MACD 5,35,9 and relaxed flow filter)
        oi_change = current['open_interest'] - prev['open_interest']
        price_change = current['close'] - prev['close']
        zscore = 1.0 if price_change > 0 else -1.0
        
        flow_direction = "NEUTRAL"
        if price_change > 0 and oi_change >= oi and zscore > z:
            flow_direction = "LONG_ONLY"
        elif price_change < 0 and oi_change >= oi and zscore < -z:
            flow_direction = "SHORT_ONLY"
            
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
                
        # 2. Position Monitoring
        if pos is not None:
            side = pos["side"]
            entry_p = pos["entry_price"]
            
            # Current Stop Loss Level
            sl_level = entry_p - stop_loss_pts if side == "LONG" else entry_p + stop_loss_pts
            if pos["half_tp_hit"] and move_to_breakeven:
                # Move SL of remaining 50% to breakeven (entry price)
                sl_level = entry_p
                
            # Check Stop Loss (applies to whole if half not hit, otherwise applies to remaining 50%)
            sl_hit = False
            if side == "LONG" and current['low'] <= sl_level:
                sl_hit = True
            elif side == "SHORT" and current['high'] >= sl_level:
                sl_hit = True
                
            if sl_hit:
                if not pos["half_tp_hit"]:
                    # Whole position hit SL (loss = stop_loss_pts)
                    trades_log.append({
                        "entry_time": pos["entry_time"], "exit_time": current['timestamp'],
                        "side": side, "entry_price": entry_p, 
                        "exit_price_1": sl_level, "pnl_1": -stop_loss_pts, "exit_time_1": current['timestamp'],
                        "exit_price_2": sl_level, "pnl_2": -stop_loss_pts, "exit_time_2": current['timestamp'],
                        "total_pnl": -stop_loss_pts, "type": "FULL_SL"
                    })
                else:
                    # Remaining 50% hit SL/Breakeven
                    pnl_2 = 0.0 if move_to_breakeven else -stop_loss_pts
                    total_pnl = (pos["pnl_1"] + pnl_2) / 2.0
                    trades_log.append({
                        "entry_time": pos["entry_time"], "exit_time": current['timestamp'],
                        "side": side, "entry_price": entry_p,
                        "exit_price_1": pos["half_tp_price"], "pnl_1": pos["pnl_1"], "exit_time_1": pos["half_tp_time"],
                        "exit_price_2": sl_level, "pnl_2": pnl_2, "exit_time_2": current['timestamp'],
                        "total_pnl": total_pnl, "type": "PARTIAL_SL"
                    })
                pos = None
                continue
                
            # Check Take Profit for first 50% (if not hit yet)
            if pos is not None and not pos["half_tp_hit"]:
                tp_level = entry_p + take_profit_pts if side == "LONG" else entry_p - take_profit_pts
                tp_hit = False
                if side == "LONG" and current['high'] >= tp_level:
                    tp_hit = True
                elif side == "SHORT" and current['low'] <= tp_level:
                    tp_hit = True
                    
                if tp_hit:
                    pos["half_tp_hit"] = True
                    pos["half_tp_price"] = tp_level
                    pos["half_tp_time"] = current['timestamp']
                    pos["pnl_1"] = take_profit_pts
                    # Pos remains active for second half
                    
            # Check Technical Reverse Exit (MACD cross)
            if pos is not None:
                tech_exit = False
                if side == "LONG" and macd_trigger == "SHORT":
                    tech_exit = True
                elif side == "SHORT" and macd_trigger == "LONG":
                    tech_exit = True
                    
                if tech_exit:
                    exit_p = current['close']
                    pnl_rev = (exit_p - entry_p) if side == "LONG" else (entry_p - exit_p)
                    
                    if not pos["half_tp_hit"]:
                        # Technical reverse before TP hit: exit whole position
                        trades_log.append({
                            "entry_time": pos["entry_time"], "exit_time": current['timestamp'],
                            "side": side, "entry_price": entry_p,
                            "exit_price_1": exit_p, "pnl_1": pnl_rev, "exit_time_1": current['timestamp'],
                            "exit_price_2": exit_p, "pnl_2": pnl_rev, "exit_time_2": current['timestamp'],
                            "total_pnl": pnl_rev, "type": "FULL_REV"
                        })
                    else:
                        # Exit remaining 50% at technical exit
                        total_pnl = (pos["pnl_1"] + pnl_rev) / 2.0
                        trades_log.append({
                            "entry_time": pos["entry_time"], "exit_time": current['timestamp'],
                            "side": side, "entry_price": entry_p,
                            "exit_price_1": pos["half_tp_price"], "pnl_1": pos["pnl_1"], "exit_time_1": pos["half_tp_time"],
                            "exit_price_2": exit_p, "pnl_2": pnl_rev, "exit_time_2": current['timestamp'],
                            "total_pnl": total_pnl, "type": "PARTIAL_REV"
                        })
                    pos = None
                    continue
                    
        # 3. Position Entry
        if pos is None:
            if final_signal == "BUY":
                pos = {
                    "side": "LONG", "entry_price": current['close'], "entry_time": current['timestamp'],
                    "half_tp_hit": False, "half_tp_price": 0.0, "half_tp_time": None
                }
                total_entries += 1
            elif final_signal == "SELL":
                pos = {
                    "side": "SHORT", "entry_price": current['close'], "entry_time": current['timestamp'],
                    "half_tp_hit": False, "half_tp_price": 0.0, "half_tp_time": None
                }
                total_entries += 1
                
    # End of Day force close
    if pos is not None:
        last = records[-1]
        side = pos["side"]
        entry_p = pos["entry_price"]
        pnl_eod = (last['close'] - entry_p) if side == "LONG" else (entry_p - last['close'])
        
        if not pos["half_tp_hit"]:
            trades_log.append({
                "entry_time": pos["entry_time"], "exit_time": last['timestamp'],
                "side": side, "entry_price": entry_p,
                "exit_price_1": last['close'], "pnl_1": pnl_eod, "exit_time_1": last['timestamp'],
                "exit_price_2": last['close'], "pnl_2": pnl_eod, "exit_time_2": last['timestamp'],
                "total_pnl": pnl_eod, "type": "FULL_EOD"
            })
        else:
            total_pnl = (pos["pnl_1"] + pnl_eod) / 2.0
            trades_log.append({
                "entry_time": pos["entry_time"], "exit_time": last['timestamp'],
                "side": side, "entry_price": entry_p,
                "exit_price_1": pos["half_tp_price"], "pnl_1": pos["pnl_1"], "exit_time_1": pos["half_tp_time"],
                "exit_price_2": last['close'], "pnl_2": pnl_eod, "exit_time_2": last['timestamp'],
                "total_pnl": total_pnl, "type": "PARTIAL_EOD"
            })
            
    return trades_log

if __name__ == "__main__":
    df = get_db_data_for_day("2026-06-15")
    df = calculate_technical_indicators(df, fast=5, slow=35, signal_period=9)
    
    # Run two setups:
    # 1. 50% Profit Take (TP=4.0, SL=2.0) and remaining 50% exit at Reverse/SL(2.0)
    # 2. 50% Profit Take (TP=4.0, SL=2.0) and remaining 50% exit at Reverse/Trailing to Breakeven
    
    for be in [False, True]:
        be_str = "본절컷(Breakeven) 작동" if be else "기존 손절라인 유지"
        print(f"\n==================================================")
        print(f" [시뮬레이션] 50% 분할 익절 전략 ({be_str})")
        print(f" 조건: MACD(5,35,9) | OI>=5 수급 필터 | SL: 2.0 Pt | TP: 4.0 Pt")
        print(f"==================================================")
        
        trades = run_partial_tp_simulation(df, stop_loss_pts=2.0, take_profit_pts=4.0, move_to_breakeven=be)
        
        total_pnl = sum(t["total_pnl"] for t in trades)
        win_trades = [t for t in trades if t["total_pnl"] > 0]
        win_rate = len(win_trades) / len(trades) if trades else 0
        total_krw = total_pnl * 50000
        
        print(f" 거래 횟수: {len(trades)}회")
        print(f" 승률 (평균 플런스 기준): {win_rate*100:.1f}% ({len(win_trades)}/{len(trades)})")
        print(f" 누적 손익 포인트: {total_pnl:+.2f} Pt")
        print(f" 누적 평가 손익 (미니 1계약 환산): {total_krw:+,} 원")
        print("-" * 50)
        for idx, t in enumerate(trades):
            print(f"  [{idx+1}] {t['entry_time']} | Side: {t['side']} | Entry: {t['entry_price']:.2f}")
            if "PARTIAL" in t["type"] or t["type"] == "FULL_SL" or t["type"] == "FULL_REV":
                print(f"    - 1차 청산: {t['exit_time_1']} | 가격: {t['exit_price_1']:.2f} | 손익: {t['pnl_1']:+.2f} Pt")
                print(f"    - 2차 청산: {t['exit_time_2']} | 가격: {t['exit_price_2']:.2f} | 손익: {t['pnl_2']:+.2f} Pt")
            else:
                print(f"    - 1차 청산: {t['exit_time_1']} | 가격: {t['exit_price_1']:.2f} | 손익: {t['pnl_1']:+.2f} Pt")
                print(f"    - 2차 청산: {t['exit_time_2']} | 가격: {t['exit_price_2']:.2f} | 손익: {t['pnl_2']:+.2f} Pt")
            print(f"    * 최종 평균 손익: {t['total_pnl']:+.2f} Pt ({t['type']})")
            print()
