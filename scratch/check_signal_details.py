import os
import sys
import pymysql
from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
load_dotenv()

def run():
    host = os.getenv("MARIADB_HOST", "127.0.0.1")
    port = int(os.getenv("MARIADB_PORT", 3306))
    user = os.getenv("MARIADB_USER", "kis_user")
    password = os.getenv("MARIADB_PASSWORD", "kis_password")
    database = os.getenv("MARIADB_DATABASE", "kis_trading")
    
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
            # 1. Get foreign flows around 09:48
            print("=== FOREIGN FLOWS AROUND 09:48 ===")
            cursor.execute("SELECT * FROM foreign_flows WHERE fetched_at BETWEEN '2026-06-16 09:45:00' AND '2026-06-16 09:55:00' ORDER BY fetched_at ASC;")
            flows = cursor.fetchall()
            for flow in flows:
                print(flow)
                
            # 2. Get regime states around 09:48
            print("\n=== REGIME STATES AROUND 09:48 ===")
            cursor.execute("SELECT * FROM regime_states WHERE detected_at BETWEEN '2026-06-16 09:45:00' AND '2026-06-16 09:55:00' ORDER BY detected_at ASC;")
            regimes = cursor.fetchall()
            for reg in regimes:
                print(reg)
                
            # 3. Get candles around 09:48
            print("\n=== CANDLES AROUND 09:48 ===")
            cursor.execute("SELECT * FROM market_candles WHERE candle_time BETWEEN '2026-06-16 09:45:00' AND '2026-06-16 09:50:00' ORDER BY candle_time ASC;")
            candles = cursor.fetchall()
            for candle in candles:
                print(candle)
                
    finally:
        conn.close()

if __name__ == "__main__":
    run()
