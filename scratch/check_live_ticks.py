import os
import sys
from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from future.store.mariadb_store import MariaDBStore

load_dotenv()

db = MariaDBStore(
    host=os.getenv("MARIADB_HOST", "127.0.0.1"),
    port=int(os.getenv("MARIADB_PORT", 3306)),
    user=os.getenv("MARIADB_USER", "kis_user"),
    password=os.getenv("MARIADB_PASSWORD", "kis_password"),
    database=os.getenv("MARIADB_DATABASE", "kis_trading")
)

print("=== Last 10 market candles for 105V07 ===")
try:
    cursor = db.conn.cursor()
    cursor.execute(
        "SELECT * FROM market_candles WHERE futures_code = '105V07' ORDER BY candle_time DESC LIMIT 10"
    )
    rows = cursor.fetchall()
    for row in rows:
        print(f"Time: {row['candle_time']} | O: {row['open']:.2f} | H: {row['high']:.2f} | L: {row['low']:.2f} | C: {row['close']:.2f} | V: {row['volume']}")
    
    cursor.execute(
        "SELECT COUNT(*) as cnt FROM market_candles WHERE futures_code = '105V07'"
    )
    count_row = cursor.fetchone()
    print(f"\nTotal candles for 105V07: {count_row['cnt']}")
except Exception as e:
    print(f"Error querying database: {e}")

db.close()
