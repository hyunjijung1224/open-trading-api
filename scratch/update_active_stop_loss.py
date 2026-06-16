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
            # Check current active positions
            cursor.execute("SELECT * FROM active_positions;")
            positions = cursor.fetchall()
            print("Before update:")
            for pos in positions:
                print(pos)
                
            # Update stop loss to 1387.09 (avg_price 1389.09 - 2.0 Pt)
            # and take profit to 1393.09 (avg_price 1389.09 + 4.0 Pt)
            cursor.execute("""
                UPDATE active_positions 
                SET stop_loss = 1387.09, take_profit = 1393.09 
                WHERE position_id = 'P_105V07_LONG';
            """)
            print("\nUpdated active position.")
            
            cursor.execute("SELECT * FROM active_positions;")
            positions = cursor.fetchall()
            print("After update:")
            for pos in positions:
                print(pos)
                
    finally:
        conn.close()

if __name__ == "__main__":
    run()
