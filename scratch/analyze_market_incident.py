import os
import sys
import pymysql
from datetime import datetime, timedelta
from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
load_dotenv()

def analyze():
    # Database connection parameters
    host = os.getenv("MARIADB_HOST", "127.0.0.1")
    port = int(os.getenv("MARIADB_PORT", 3306))
    user = os.getenv("MARIADB_USER", "kis_user")
    password = os.getenv("MARIADB_PASSWORD", "kis_password")
    database = os.getenv("MARIADB_DATABASE", "kis_trading")
    
    print(f"Connecting to DB {database} at {host}:{port}...")
    conn = pymysql.connect(
        host=host,
        port=port,
        user=user,
        password=password,
        database=database,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor
    )
    
    try:
        with conn.cursor() as cursor:
            # 1. Active Positions
            print("\n=== ACTIVE POSITIONS ===")
            cursor.execute("SELECT * FROM active_positions;")
            positions = cursor.fetchall()
            if not positions:
                print("No active positions.")
            for pos in positions:
                print(pos)
                
            # 2. Today's Orders
            print("\n=== TODAY'S ORDERS ===")
            cursor.execute("SELECT * FROM orders WHERE ordered_at >= CURDATE() ORDER BY ordered_at DESC LIMIT 20;")
            orders = cursor.fetchall()
            if not orders:
                print("No orders today.")
            for ord in orders:
                print(ord)
                
            # 3. Today's Trades
            print("\n=== TODAY'S TRADES ===")
            cursor.execute("SELECT * FROM trades WHERE exit_time >= CURDATE() ORDER BY exit_time DESC LIMIT 20;")
            trades = cursor.fetchall()
            if not trades:
                print("No completed trades today.")
            for trd in trades:
                print(trd)
                
            # 4. Regime States around 09:48
            print("\n=== REGIME STATES (LAST 1 HOUR) ===")
            cursor.execute("SELECT * FROM regime_states WHERE detected_at >= DATE_SUB(NOW(), INTERVAL 1 HOUR) ORDER BY detected_at DESC LIMIT 30;")
            regimes = cursor.fetchall()
            for reg in regimes:
                print(reg)
                
            # 5. Foreign flows (Last 1 hour)
            print("\n=== FOREIGN FLOWS (LAST 1 HOUR) ===")
            cursor.execute("SELECT * FROM foreign_flows WHERE fetched_at >= DATE_SUB(NOW(), INTERVAL 1 HOUR) ORDER BY fetched_at DESC LIMIT 10;")
            flows = cursor.fetchall()
            for flow in flows:
                print(flow)
                
            # 6. Candles around 09:48
            print("\n=== MARKET CANDLES AROUND 09:48 (LAST 30 MIN) ===")
            cursor.execute("SELECT * FROM market_candles WHERE candle_time >= DATE_SUB(NOW(), INTERVAL 1 HOUR) ORDER BY candle_time DESC LIMIT 30;")
            candles = cursor.fetchall()
            for candle in candles:
                print(candle)
                
    finally:
        conn.close()

if __name__ == "__main__":
    analyze()
