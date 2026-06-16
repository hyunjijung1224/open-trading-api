import os
import pymysql
from dotenv import load_dotenv

load_dotenv()

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

with conn.cursor() as cursor:
    print("=== All Orders in DB ===")
    cursor.execute("SELECT * FROM orders ORDER BY ordered_at DESC LIMIT 20")
    orders = cursor.fetchall()
    for o in orders:
        print(o)

    print("\n=== All Trades in DB ===")
    cursor.execute("SELECT * FROM trades ORDER BY exit_time DESC LIMIT 20")
    trades = cursor.fetchall()
    for t in trades:
        print(t)

conn.close()
