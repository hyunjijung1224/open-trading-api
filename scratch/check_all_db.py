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
        cursor.execute("SELECT COUNT(*) as cnt FROM orders")
        print("Total Orders in DB:", cursor.fetchone()['cnt'])
        
        cursor.execute("SELECT COUNT(*) as cnt FROM trades")
        print("Total Trades in DB:", cursor.fetchone()['cnt'])
        
        cursor.execute("SELECT COUNT(*) as cnt FROM market_candles")
        print("Total Candles in DB:", cursor.fetchone()['cnt'])
        
        cursor.execute("SELECT COUNT(*) as cnt FROM regime_states")
        print("Total Regimes in DB:", cursor.fetchone()['cnt'])
        
        cursor.execute("SELECT * FROM orders ORDER BY ordered_at DESC LIMIT 5")
        print("Latest Orders:", cursor.fetchall())
        
        cursor.execute("SELECT * FROM trades ORDER BY exit_time DESC LIMIT 5")
        print("Latest Trades:", cursor.fetchall())
        
    conn.close()
except Exception as e:
    print("Failed:", e)
