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
        cursor.execute("""
            SELECT * FROM market_candles 
            WHERE candle_time BETWEEN '2026-06-15 14:40:00' AND '2026-06-15 15:05:00'
            ORDER BY candle_time ASC
        """)
        candles = cursor.fetchall()
        print("=== Candles between 14:40 and 15:05 ===")
        for c in candles:
            print(f"Time: {c['candle_time']} | Open: {c['open']} | High: {c['high']} | Low: {c['low']} | Close: {c['close']} | Vol: {c['volume']} | OI: {c['open_interest']}")
    conn.close()
except Exception as e:
    print("Failed:", e)
