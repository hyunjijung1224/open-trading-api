import pymysql, os
from datetime import datetime
from dotenv import load_dotenv

load_dotenv(os.path.join(r'E:\0-aiTrading\open-trading-api', '.env'))

host = os.getenv('MARIADB_HOST', '127.0.0.1')
port = int(os.getenv('MARIADB_PORT', 3306))
user = os.getenv('MARIADB_USER', 'kis_user')
pw = os.getenv('MARIADB_PASSWORD', 'kis_password')
db = os.getenv('MARIADB_DATABASE', 'kis_trading')

conn = pymysql.connect(host=host, port=port, user=user, password=pw, database=db, charset='utf8mb4')
cur = conn.cursor()

# 1. 활성 포지션 확인
print("=== ACTIVE POSITIONS ===")
cur.execute("SELECT * FROM active_positions")
rows = cur.fetchall()
cols = [d[0] for d in cur.description]
for r in rows:
    print(dict(zip(cols, r)))
if not rows:
    print("(없음)")

# 2. 오늘 주문 내역
print("\n=== TODAY ORDERS ===")
cur.execute("SELECT * FROM orders WHERE DATE(ordered_at) = CURDATE() ORDER BY ordered_at")
rows = cur.fetchall()
cols = [d[0] for d in cur.description]
for r in rows:
    print(dict(zip(cols, r)))

# 3. 오늘 거래(청산) 내역
print("\n=== TODAY TRADES ===")
cur.execute("SELECT * FROM trades WHERE DATE(exit_time) = CURDATE() ORDER BY exit_time")
rows = cur.fetchall()
cols = [d[0] for d in cur.description]
for r in rows:
    print(dict(zip(cols, r)))

# 4. 오늘 레짐 상태
print("\n=== TODAY REGIME (최근 5개) ===")
cur.execute("SELECT * FROM regime_states WHERE DATE(detected_at) = CURDATE() ORDER BY detected_at DESC LIMIT 5")
rows = cur.fetchall()
cols = [d[0] for d in cur.description]
for r in rows:
    print(dict(zip(cols, r)))

# 5. 오늘 캔들 (장 마감 전후 확인)
print("\n=== TODAY CANDLES (15:30~16:00) ===")
cur.execute("SELECT candle_time, open, high, low, close, volume FROM market_candles WHERE DATE(candle_time) = CURDATE() AND HOUR(candle_time) >= 15 AND MINUTE(candle_time) >= 25 ORDER BY candle_time")
rows = cur.fetchall()
cols = [d[0] for d in cur.description]
for r in rows:
    print(dict(zip(cols, r)))

conn.close()
