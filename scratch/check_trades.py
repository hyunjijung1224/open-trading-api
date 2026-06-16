import pymysql
import os
import sys
from dotenv import load_dotenv

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
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
    
    db._ensure_connection()
    query = "SELECT * FROM trades ORDER BY exit_time DESC LIMIT 5"
    with db.conn.cursor() as cursor:
        cursor.execute(query)
        rows = cursor.fetchall()
        print("--- 최근 5개 거래 내역 ---")
        for row in rows:
            print(row)
            
    db.close()

if __name__ == "__main__":
    main()
