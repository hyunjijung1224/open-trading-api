import os
import sys
import pymysql
from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
load_dotenv()

conn = pymysql.connect(
    host=os.getenv("MARIADB_HOST", "127.0.0.1"),
    port=3306,
    user=os.getenv("MARIADB_USER", "kis_user"),
    password=os.getenv("MARIADB_PASSWORD", "kis_password"),
    database=os.getenv("MARIADB_DATABASE", "kis_trading"),
    autocommit=True
)

try:
    with conn.cursor() as cursor:
        cursor.execute("SELECT * FROM active_positions")
        rows = cursor.fetchall()
        print(f"Active positions in DB: {len(rows)}")
        for r in rows:
            print(r)
finally:
    conn.close()
