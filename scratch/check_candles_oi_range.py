import os
import pymysql
from dotenv import load_dotenv

load_dotenv()

host = os.getenv("MARIADB_HOST", "127.0.0.1")
port = int(os.getenv("MARIADB_PORT", 3306))
user = os.getenv("MARIADB_USER", "kis_user")
password = os.getenv("MARIADB_PASSWORD", "kis_password")
database = os.getenv("MARIADB_DATABASE", "kis_trading")

try:
    conn = pymysql.connect(
        host=host,
        port=port,
        user=user,
        password=password,
        database=database,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor
    )
    with conn.cursor() as cursor:
        cursor.execute("SELECT * FROM market_candles ORDER BY candle_time DESC LIMIT 10")
        candles = cursor.fetchall()
        print("=== Latest 10 Candles ===")
        for c in candles:
            print(f"Time: {c['candle_time']} | Open: {c['open']} | Close: {c['close']} | Vol: {c['volume']} | OI: {c['open_interest']}")
            
        cursor.execute("""
            SELECT c1.candle_time, c1.open_interest as curr_oi, c2.open_interest as prev_oi,
                   (c1.open_interest - c2.open_interest) as oi_diff
            FROM market_candles c1
            JOIN market_candles c2 ON c2.candle_time = DATE_SUB(c1.candle_time, INTERVAL 1 MINUTE)
            WHERE DATE(c1.candle_time) = '2026-06-15'
              AND ABS(c1.open_interest - c2.open_interest) >= 15
            ORDER BY c1.candle_time DESC
            LIMIT 20
        """)
        diffs = cursor.fetchall()
        print(f"\n=== Candles with OI diff >= 15 today (Total: {len(diffs)}) ===")
        for d in diffs:
            print(f"Time: {d['candle_time']} | Curr OI: {d['curr_oi']} | Prev OI: {d['prev_oi']} | Diff: {d['oi_diff']:+d}")
            
    conn.close()
except Exception as e:
    print("Failed:", e)
