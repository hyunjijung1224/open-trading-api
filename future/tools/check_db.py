import os
import sys
import pymysql
import pandas as pd
from datetime import datetime

# sys.path 추가
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

def main():
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
    WHERE futures_code='105V07' AND candle_time >= '2026-06-12 11:50:00' AND candle_time <= '2026-06-12 12:50:00'
    ORDER BY candle_time ASC
    """
    df = pd.read_sql(query, conn)
    print("OHLCV 12:30 이후 데이터:")
    print(df.tail(20))
    
    # 최근 레짐을 계산해본다.
    from future.engines.regime_engine import RegimeEngine
    from future.engines.signal_engine import SignalEngine
    regime = RegimeEngine()
    signal_engine = SignalEngine()
    
    for target_time in df['timestamp'].tail(3).astype(str):
        subset = df[df['timestamp'] <= target_time].tail(20)
        if len(subset) == 20:
            candles = subset.to_dict('records')
            current_price = candles[-1]['close']
            res = regime.detect(current_price, candles)
            print(f"\n[Regime at {target_time}]")
            print(f"ADX: {res['adx']:.2f}, BBW: {res.get('bbw', 0):.5f}, ATR: {res['atr']:.2f}")
            print(f"Regime: {res['regime']}")
            
            # 지표 계산을 위해 supervisor의 지표 계산 로직 임의 구현
            import numpy as np
            closes = np.array([c['close'] for c in candles])
            highs = np.array([c['high'] for c in candles])
            lows = np.array([c['low'] for c in candles])
            vols = np.array([c['volume'] for c in candles])
            
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
            
            sma20 = np.mean(closes[-20:])
            sma60 = np.mean(closes[-60:]) if len(closes)>=60 else np.mean(closes)
            
            # 미결제약정 변동량 계산
            current_oi = candles[-1].get('open_interest', 0)
            prev_oi = candles[-2].get('open_interest', 0) if len(candles) > 1 else 0
            oi_change = current_oi - prev_oi if current_oi > 0 and prev_oi > 0 else 0

            # 가상의 외국인 수급 시나리오 (+2000 계약) 및 1분 변동 (+100 계약) 주입
            mock_foreign_net_buy = 2000
            mock_foreign_net_buy_1m = 100
            
            indicators = {
                "ma5": np.mean(closes[-5:]),
                "ma20": sma20,
                "ma60": sma60,
                "macd": macd_line[-1],
                "macd_signal": macd_signal[-1],
                "macd_hist": macd_hist[-1],
                "atr": res['atr'],
                "prev_close": closes[-2],
                "current_price": current_price,
                "foreign_net_buy": mock_foreign_net_buy,
                "foreign_net_buy_1m": mock_foreign_net_buy_1m,
                "oi_change": oi_change
            }
            
            sig = signal_engine.generate("105V07", res['regime'], indicators)
            print(f"Signal: {sig['direction']} (Score: {sig['score']})")
            
    conn.close()

if __name__ == "__main__":
    main()
