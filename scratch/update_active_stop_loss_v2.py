import os
import sys
import pymysql
from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
load_dotenv()

def run():
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
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True
    )
    
    try:
        with conn.cursor() as cursor:
            # Let's inspect the exact position_id
            cursor.execute("SELECT position_id, stop_loss, take_profit FROM active_positions;")
            rows = cursor.fetchall()
            print("Current rows in DB:")
            for r in rows:
                print(r)
                
            # Perform update
            cursor.execute("""
                UPDATE active_positions 
                SET stop_loss = 1387.09, take_profit = 1393.09 
                WHERE position_id = 'P_105V07_LONG';
            """)
            print(f"Rowcount affected: {cursor.rowcount}")
            
            # Read back
            cursor.execute("SELECT position_id, stop_loss, take_profit FROM active_positions;")
            rows_after = cursor.fetchall()
            print("After update in DB:")
            for r in rows_after:
                print(r)
                
    finally:
        conn.close()

if __name__ == "__main__":
    run()
