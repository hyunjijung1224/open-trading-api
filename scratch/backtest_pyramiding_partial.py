# -*- coding: utf-8 -*-
import os
import sys
import pymysql
import pandas as pd
import numpy as np
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

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

def calculate_technical_indicators(df, fast=5, slow=35, signal_period=9):
    closes = df['close'].values
    
    def ema(data, period):
        alpha = 2.0 / (period + 1.0)
        res = np.zeros_like(data)
        res[0] = data[0]
        for i in range(1, len(data)):
            res[i] = data[i] * alpha + res[i-1] * (1.0 - alpha)
        return res
    
    ema_fast = ema(closes, fast)
    ema_slow = ema(closes, slow)
    macd_line = ema_fast - ema_slow
    macd_sig = ema(macd_line, signal_period)
    
    df['macd'] = macd_line
    df['macd_signal'] = macd_sig
    return df

def run_simulation(df, stop_loss_pts=2.0, take_profit_pts=4.0, oi_thr=5, z_thr=0.2, partial_sl=False):
    records = df.to_dict('records')
    
    # Position state dict:
    # {
    #   "side": "LONG" / "SHORT",
    #   "qty": int,
    #   "avg_price": float,
    #   "half_tp_hit": bool,
    #   "half_sl_hit": bool,
    #   "trades": list of open execution legs for logging
    # }
    pos = None
    trades_log = []
    
    for i in range(40, len(records)):
        current = records[i]
        prev = records[i-1]
        
        # 1. Signals (relaxed flow filter)
        oi_change = current['open_interest'] - prev['open_interest']
        price_change = current['close'] - prev['close']
        zscore = 1.0 if price_change > 0 else -1.0
        
        flow_direction = "NEUTRAL"
        if price_change > 0 and oi_change >= oi_thr and zscore > z_thr:
            flow_direction = "LONG_ONLY"
        elif price_change < 0 and oi_change >= oi_thr and zscore < -z_thr:
            flow_direction = "SHORT_ONLY"
            
        macd_trigger = None
        if current['macd'] > current['macd_signal'] and prev['macd'] <= prev['macd_signal']:
            macd_trigger = "LONG"
        elif current['macd'] < current['macd_signal'] and prev['macd'] >= prev['macd_signal']:
            macd_trigger = "SHORT"
            
        final_signal = "HOLD"
        if macd_trigger == "LONG" and flow_direction == "LONG_ONLY":
            final_signal = "BUY"
        elif macd_trigger == "SHORT" and flow_direction == "SHORT_ONLY":
            final_signal = "SELL"
            
        # 2. Position Monitoring
        if pos is not None:
            side = pos["side"]
            avg_price = pos["avg_price"]
            qty = pos["qty"]
            
            # --- A. Check Stop Loss ---
            # 1차 손절 라인
            sl_level = avg_price - stop_loss_pts if side == "LONG" else avg_price + stop_loss_pts
            # 만약 partial_sl이 참이고 1차 손절이 이미 나갔다면, 2차 손절 라인은 더 깊게 설정 (예: 평단가 - 3.5 Pt 또는 반대 신호)
            # 여기서는 2차 손절폭을 3.5 Pt 로 설정
            sl_level_2 = avg_price - 3.5 if side == "LONG" else avg_price + 3.5
            
            sl_hit = False
            sl_2_hit = False
            
            if side == "LONG":
                if current['low'] <= sl_level:
                    sl_hit = True
                if pos["half_sl_hit"] and current['low'] <= sl_level_2:
                    sl_2_hit = True
            elif side == "SHORT":
                if current['high'] >= sl_level:
                    sl_hit = True
                if pos["half_sl_hit"] and current['high'] >= sl_level_2:
                    sl_2_hit = True
                    
            if sl_hit and not pos["half_sl_hit"]:
                if partial_sl and qty >= 2:
                    # 50% 분할 손절 실행
                    sl_qty = qty // 2
                    pnl_loss = -stop_loss_pts * sl_qty
                    pos["qty"] -= sl_qty
                    pos["half_sl_hit"] = True
                    trades_log.append({
                        "time": current['timestamp'], "side": side, "qty": sl_qty,
                        "type": "PARTIAL_SL", "price": sl_level, "pnl": pnl_loss
                    })
                    # 평단가는 그대로 유지되나 수량이 반으로 줆
                    qty = pos["qty"]
                else:
                    # 전량 손절
                    pnl_loss = -stop_loss_pts * qty
                    trades_log.append({
                        "time": current['timestamp'], "side": side, "qty": qty,
                        "type": "FULL_SL", "price": sl_level, "pnl": pnl_loss
                    })
                    pos = None
                    continue
                    
            if pos is not None and pos["half_sl_hit"] and sl_2_hit:
                # 남은 수량 최종 2차 손절
                pnl_loss = -3.5 * qty
                trades_log.append({
                    "time": current['timestamp'], "side": side, "qty": qty,
                    "type": "FULL_SL_2ND", "price": sl_level_2, "pnl": pnl_loss
                })
                pos = None
                continue
                
            # --- B. Check Take Profit ---
            if pos is not None and not pos["half_tp_hit"]:
                tp_level = avg_price + take_profit_pts if side == "LONG" else avg_price - take_profit_pts
                tp_hit = False
                if side == "LONG" and current['high'] >= tp_level:
                    tp_hit = True
                elif side == "SHORT" and current['low'] <= tp_level:
                    tp_hit = True
                    
                if tp_hit:
                    if qty >= 2:
                        # 50% 분할 익절 실행
                        tp_qty = qty // 2
                        pnl_gain = take_profit_pts * tp_qty
                        pos["qty"] -= tp_qty
                        pos["half_tp_hit"] = True
                        trades_log.append({
                            "time": current['timestamp'], "side": side, "qty": tp_qty,
                            "type": "PARTIAL_TP", "price": tp_level, "pnl": pnl_gain
                        })
                        qty = pos["qty"]
                    else:
                        # 1계약뿐이면 전량 익절
                        pnl_gain = take_profit_pts * qty
                        trades_log.append({
                            "time": current['timestamp'], "side": side, "qty": qty,
                            "type": "FULL_TP", "price": tp_level, "pnl": pnl_gain
                        })
                        pos = None
                        continue
                        
            # --- C. Check Technical Exit (MACD Cross) ---
            if pos is not None:
                tech_exit = False
                if side == "LONG" and macd_trigger == "SHORT":
                    tech_exit = True
                elif side == "SHORT" and macd_trigger == "LONG":
                    tech_exit = True
                    
                if tech_exit:
                    exit_p = current['close']
                    pnl_diff = (exit_p - avg_price) if side == "LONG" else (avg_price - exit_p)
                    pnl_gain = pnl_diff * qty
                    trades_log.append({
                        "time": current['timestamp'], "side": side, "qty": qty,
                        "type": "TECH_EXIT", "price": exit_p, "pnl": pnl_gain
                    })
                    pos = None
                    continue
                    
        # 3. Entry & Pyramiding
        if pos is None:
            # 신규 진입 (기본 2계약 진입으로 시뮬레이션)
            if final_signal == "BUY":
                pos = {
                    "side": "LONG", "qty": 2, "avg_price": current['close'],
                    "half_tp_hit": False, "half_sl_hit": False
                }
                trades_log.append({
                    "time": current['timestamp'], "side": "LONG", "qty": 2,
                    "type": "ENTRY", "price": current['close'], "pnl": 0.0
                })
            elif final_signal == "SELL":
                pos = {
                    "side": "SHORT", "qty": 2, "avg_price": current['close'],
                    "half_tp_hit": False, "half_sl_hit": False
                }
                trades_log.append({
                    "time": current['timestamp'], "side": "SHORT", "qty": 2,
                    "type": "ENTRY", "price": current['close'], "pnl": 0.0
                })
        else:
            # 피라미딩 추가매수 조건 검사
            # 동일 방향 신호 발생 시
            is_pyramiding_signal = (pos["side"] == "LONG" and final_signal == "BUY") or (pos["side"] == "SHORT" and final_signal == "SELL")
            if is_pyramiding_signal:
                cur_p = current['close']
                # 물타기 방지 및 수익 버퍼 검증 (1.5 Pt 이상 수익 중일 때)
                is_profitable = (pos["side"] == "LONG" and cur_p > pos["avg_price"]) or (pos["side"] == "SHORT" and cur_p < pos["avg_price"])
                profit_buffer = abs(cur_p - pos["avg_price"])
                
                if is_profitable and profit_buffer >= 1.5:
                    # 피라미딩 1계약 추가
                    old_qty = pos["qty"]
                    old_avg = pos["avg_price"]
                    new_qty = old_qty + 1
                    new_avg = ((old_avg * old_qty) + cur_p) / new_qty
                    
                    pos["qty"] = new_qty
                    pos["avg_price"] = new_avg
                    # 추가매수 시 TP와 SL 상태를 다시 리셋하여 새로운 1차 분할 익절/손절이 가능하게 함
                    pos["half_tp_hit"] = False
                    pos["half_sl_hit"] = False
                    
                    trades_log.append({
                        "time": current['timestamp'], "side": pos["side"], "qty": 1,
                        "type": "PYRAMID_ADD", "price": cur_p, "pnl": 0.0,
                        "meta": f"PrevAvg={old_avg:.2f}, NewAvg={new_avg:.2f}, Qty={new_qty}"
                    })
                    
    # EOD Force Close
    if pos is not None:
        last = records[-1]
        side = pos["side"]
        qty = pos["qty"]
        avg_price = pos["avg_price"]
        pnl_diff = (last['close'] - avg_price) if side == "LONG" else (avg_price - last['close'])
        pnl_gain = pnl_diff * qty
        trades_log.append({
            "time": last['timestamp'], "side": side, "qty": qty,
            "type": "EOD_CLOSE", "price": last['close'], "pnl": pnl_gain
        })
        
    return trades_log

if __name__ == "__main__":
    df_raw = get_db_data_for_day("2026-06-15")
    df = calculate_technical_indicators(df_raw, fast=5, slow=35, signal_period=9)
    
    # 시나리오 1: 피라미딩 허용 + 기존 손절컷 (전량 손절)
    print("==================================================================")
    print(" [시나리오 1] 피라미딩 + 50% 분할 익절 + 전량 손절 (SL 2.0 Pt, TP 4.0 Pt)")
    print("==================================================================")
    trades_s1 = run_simulation(df, stop_loss_pts=2.0, take_profit_pts=4.0, partial_sl=False)
    
    total_pnl_s1 = sum(t["pnl"] for t in trades_s1)
    print(f" 총 손익: {total_pnl_s1:+.2f} Pt (미니선물 환산: {total_pnl_s1 * 50000:+,} KRW)")
    for t in trades_s1:
        meta_str = f" | {t['meta']}" if "meta" in t else ""
        print(f"  [{t['time']}] {t['type']} | {t['side']} | 수량: {t['qty']}계약 | 가격: {t['price']:.2f} | 손익: {t['pnl']:+.2f} Pt{meta_str}")
        
    # 시나리오 2: 피라미딩 허용 + 50% 분할 익절 + 50% 분할 손절
    print("\n==================================================================")
    print(" [시나리오 2] 피라미딩 + 50% 분할 익절 + 50% 분할 손절 (1차 SL 2.0, 2차 SL 3.5)")
    print("==================================================================")
    trades_s2 = run_simulation(df, stop_loss_pts=2.0, take_profit_pts=4.0, partial_sl=True)
    
    total_pnl_s2 = sum(t["pnl"] for t in trades_s2)
    print(f" 총 손익: {total_pnl_s2:+.2f} Pt (미니선물 환산: {total_pnl_s2 * 50000:+,} KRW)")
    for t in trades_s2:
        meta_str = f" | {t['meta']}" if "meta" in t else ""
        print(f"  [{t['time']}] {t['type']} | {t['side']} | 수량: {t['qty']}계약 | 가격: {t['price']:.2f} | 손익: {t['pnl']:+.2f} Pt{meta_str}")
