import os
import sys
import logging
from datetime import datetime
from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from future.store.mariadb_store import MariaDBStore

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("CheckDbCandles")

load_dotenv()

def main():
    try:
        db = MariaDBStore(
            host=os.getenv("MARIADB_HOST", "127.0.0.1"),
            port=int(os.getenv("MARIADB_PORT", 3306)),
            user=os.getenv("MARIADB_USER", "kis_user"),
            password=os.getenv("MARIADB_PASSWORD", "kis_password"),
            database=os.getenv("MARIADB_DATABASE", "kis_trading")
        )
    except Exception as e:
        logger.error(f"DB connection failed: {e}")
        return

    try:
        with db.conn.cursor() as cursor:
            # 1. Total count
            cursor.execute("SELECT COUNT(*) as cnt FROM market_candles")
            row = cursor.fetchone()
            total_count = row["cnt"]
            logger.info(f"Total candles in DB: {total_count}")

            if total_count > 0:
                # 2. Min/Max time
                cursor.execute("SELECT MIN(candle_time) as min_t, MAX(candle_time) as max_t FROM market_candles")
                row = cursor.fetchone()
                logger.info(f"Date range: {row['min_t']} to {row['max_t']}")

                # 3. Night candles count (time >= 18:00 or <= 06:00)
                cursor.execute("""
                    SELECT COUNT(*) as cnt FROM market_candles 
                    WHERE TIME(candle_time) >= '18:00:00' OR TIME(candle_time) <= '06:00:00'
                """)
                row = cursor.fetchone()
                logger.info(f"Total night candles (18:00 - 06:00): {row['cnt']}")

                # 4. Show some night candles if any exist
                if row['cnt'] > 0:
                    cursor.execute("""
                        SELECT futures_code, candle_time, close, volume FROM market_candles 
                        WHERE TIME(candle_time) >= '18:00:00' OR TIME(candle_time) <= '06:00:00'
                        ORDER BY candle_time DESC LIMIT 10
                    """)
                    rows = cursor.fetchall()
                    logger.info("Sample night candles in DB:")
                    for r in rows:
                        logger.info(f"  Code: {r['futures_code']}, Time: {r['candle_time']}, Close: {r['close']}, Vol: {r['volume']}")

                # 5. Show recent day candles
                cursor.execute("""
                    SELECT futures_code, candle_time, close, volume FROM market_candles 
                    ORDER BY candle_time DESC LIMIT 5
                """)
                rows = cursor.fetchall()
                logger.info("Most recent candles in DB:")
                for r in rows:
                    logger.info(f"  Code: {r['futures_code']}, Time: {r['candle_time']}, Close: {r['close']}, Vol: {r['volume']}")

    except Exception as e:
        logger.error(f"Error querying DB: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    main()
