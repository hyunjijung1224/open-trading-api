import os
import sys
import numpy as np
from datetime import datetime
from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from future.store.mariadb_store import MariaDBStore
from future.engines.signal_engine import SignalEngine
from future.engines.regime_engine import RegimeEngine

load_dotenv()

db = MariaDBStore(
    host=os.getenv("MARIADB_HOST", "127.0.0.1"),
    port=int(os.getenv("MARIADB_PORT", 3306)),
    user=os.getenv("MARIADB_USER", "kis_user"),
    password=os.getenv("MARIADB_PASSWORD", "kis_password"),
    database=os.getenv("MARIADB_DATABASE", "kis_trading")
)

def calculate_indicators(candles, current_price):
    if len(candles) < 59:
        return None
        
    closes = np.array([c["close"] for c in candles] + [current_price], dtype=float)
    highs = np.array([c["high"] for c in candles] + [current_price], dtype=float)
    lows = np.array([c["low"] for c in candles] + [current_price], dtype=float)
    
    # 1. SMA 20, 60
    ma20 = float(np.mean(closes[-20:]))
    prev_ma20 = float(np.mean(closes[-21:-1]))
    ma60 = float(np.mean(closes[-60:]))
    prev_ma60 = float(np.mean(closes[-61:-1]))
    
    # 2. Bollinger Bands
    recent_20 = closes[-20:]
    std_20 = np.std(recent_20)
    bb_upper = ma20 + (2.0 * std_20)
    bb_lower = ma20 - (2.0 * std_20)
    
    # 3. ATR
    tr = np.zeros(len(closes) - 1)
    for i in range(len(closes) - 1):
        h_l = highs[i+1] - lows[i+1]
        h_pc = abs(highs[i+1] - closes[i])
        l_pc = abs(lows[i+1] - closes[i])
        tr[i] = max(h_l, h_pc, l_pc)
    atr = float(np.mean(tr[-14:]))
    
    # 4. MACD
    def calculate_ema(data, period):
        alpha = 2.0 / (period + 1.0)
        ema = np.zeros_like(data)
        ema[0] = data[0]
        for i in range(1, len(data)):
            ema[i] = data[i] * alpha + ema[i-1] * (1.0 - alpha)
        return ema
        
    ema12 = calculate_ema(closes, 12)
    ema26 = calculate_ema(closes, 26)
    macd_line = ema12 - ema26
    macd_signal = calculate_ema(macd_line, 9)
    macd_hist = macd_line - macd_signal
    
    return {
        "ma20": ma20,
        "prev_ma20": prev_ma20,
        "ma60": ma60,
        "prev_ma60": prev_ma60,
        "macd": float(macd_line[-1]),
        "macd_signal": float(macd_signal[-1]),
        "macd_hist": float(macd_hist[-1]),
        "atr": atr,
        "prev_close": float(closes[-2]),
        "current_price": current_price,
        "bb_upper": bb_upper,
        "bb_lower": bb_lower
    }

def main():
    # Fetch candles for 105V07
    cursor = db.conn.cursor()
    cursor.execute(
        "SELECT * FROM market_candles WHERE futures_code = '105V07' ORDER BY candle_time ASC"
    )
    candles = cursor.fetchall()
    print(f"Loaded {len(candles)} candles.")
    
    if not candles:
        print("No candles in database.")
        db.close()
        return
        
    latest_candle = candles[-1]
    current_price = float(latest_candle["close"])
    print(f"Latest Candle Time: {latest_candle['candle_time']} | Close Price: {current_price}")
    
    # Calculate indicators (use up to the second to last candle + latest candle as current_price)
    hist_candles = candles[:-1]
    indicators = calculate_indicators(hist_candles, current_price)
    
    if not indicators:
        print("Not enough candles to calculate indicators.")
        db.close()
        return
        
    # Detect regime
    regime_eng = RegimeEngine()
    regime_state = regime_eng.detect(current_price, hist_candles)
    
    # Generate signal
    sig_eng = SignalEngine()
    signal = sig_eng.generate("105V07", regime_state["regime"], indicators)
    
    print("\n==============================================")
    print("CURRENT INDICATOR VALUE DETAILS")
    print("==============================================")
    print(f"Regime: {signal['regime'].upper()} (Signal Allowed: {regime_state['signal_allowed']})")
    print(f"Current Price: {current_price:.2f}")
    print(f"MA20: {indicators['ma20']:.2f} | MA60: {indicators['ma60']:.2f} (Diff: {indicators['ma20'] - indicators['ma60']:.2f})")
    print(f"MACD: {indicators['macd']:.4f} | MACD Signal: {indicators['macd_signal']:.4f} | Hist: {indicators['macd_hist']:.4f}")
    print(f"Bollinger Bands: Lower={indicators['bb_lower']:.2f} ~ Upper={indicators['bb_upper']:.2f}")
    print(f"ATR (14): {indicators['atr']:.2f} | Prev Close: {indicators['prev_close']:.2f}")
    print(f"ATR Upper (1.5x): {indicators['prev_close'] + 1.5 * indicators['atr']:.2f}")
    print(f"ATR Lower (1.5x): {indicators['prev_close'] - 1.5 * indicators['atr']:.2f}")
    
    print("\n==============================================")
    print("STRATEGY SCORE BREAKDOWN")
    print("==============================================")
    print(f"Total Score: {signal['score']:+d} (Threshold to buy: +40, sell: -40)")
    print(f"Signal Direction: {signal['direction']}")
    print("Score Factors:")
    for reason in signal["reasons"]:
        print(f" - {reason}")
        
    db.close()

if __name__ == "__main__":
    main()
