import pymysql
import pandas as pd

conn = pymysql.connect(
    host="127.0.0.1",
    user="coretel",
    password="coretel1!",
    database="kis_trading",
    port=3306
)

query = """
SELECT DATE(candle_time) as date, COUNT(*) as count, MIN(candle_time) as start_time, MAX(candle_time) as end_time
FROM market_candles
WHERE futures_code='105V07' AND candle_time >= '2026-06-08 00:00:00'
GROUP BY DATE(candle_time)
ORDER BY date ASC;
"""

df = pd.read_sql(query, conn)
print(df)
conn.close()
