import os
import sys
from datetime import datetime, timedelta
from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
load_dotenv()

from future.store.mariadb_store import MariaDBStore

def main():
    db = MariaDBStore(
        host=os.getenv("MARIADB_HOST", "127.0.0.1"),
        port=int(os.getenv("MARIADB_PORT", 3306)),
        user=os.getenv("MARIADB_USER", "kis_user"),
        password=os.getenv("MARIADB_PASSWORD", "kis_password"),
        database=os.getenv("MARIADB_DATABASE", "kis_trading")
    )
    
    today_str = datetime.now().strftime("%Y-%m-%d")
    check_time_str = f"{today_str} 17:50:00"
    
    print(f"=== Checking Candles since {check_time_str} ===")
    
    db._ensure_connection()
    with db.conn.cursor() as cursor:
        query = """
            SELECT * FROM market_candles 
            WHERE candle_time >= %s
            ORDER BY candle_time DESC
            LIMIT 30;
        """
        cursor.execute(query, (check_time_str,))
        rows = cursor.fetchall()
        
    if not rows:
        print("No candles found since 17:50.")
        
        # Check overall counts
        with db.conn.cursor() as cursor:
            cursor.execute("SELECT futures_code, count(*) as cnt, max(candle_time) as max_time FROM market_candles GROUP BY futures_code;")
            summary = cursor.fetchall()
            print("\nDatabase Summary (All Candles):")
            for s in summary:
                print(f"- 종목: {s['futures_code']} | 건수: {s['cnt']} | 최근 시간: {s['max_time']}")
    else:
        print(f"Found {len(rows)} candles since 17:50:")
        for r in rows:
            print(f"- {r['futures_code']} | Time: {r['candle_time']} | Open: {r['open']} | High: {r['high']} | Low: {r['low']} | Close: {r['close']} | Volume: {r['volume']}")
            
if __name__ == "__main__":
    main()
