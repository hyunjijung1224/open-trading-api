import os
import pymysql
from dotenv import load_dotenv

load_dotenv()

host = os.getenv("MARIADB_HOST", "127.0.0.1")
port = int(os.getenv("MARIADB_PORT", 3306))
user = os.getenv("MARIADB_USER", "kis_user")
password = os.getenv("MARIADB_PASSWORD", "kis_password")
database = os.getenv("MARIADB_DATABASE", "kis_trading")

print(f"Connecting to {host}:{port} with user {user} and db {database}...")
try:
    conn = pymysql.connect(
        host=host,
        port=port,
        user=user,
        password=password,
        database=database,
        connect_timeout=2
    )
    print("Connection SUCCESSFUL!")
    with conn.cursor() as cursor:
        cursor.execute("SHOW TABLES")
        tables = cursor.fetchall()
        print("Tables:", tables)
        
        # Check active positions
        cursor.execute("SELECT * FROM active_positions")
        print("Active Positions:", cursor.fetchall())
        
        # Check recent trades
        cursor.execute("SELECT * FROM trades ORDER BY exit_time DESC LIMIT 10")
        print("Recent Trades:", cursor.fetchall())
        
        # Check recent orders
        cursor.execute("SELECT * FROM orders ORDER BY ordered_at DESC LIMIT 10")
        print("Recent Orders:", cursor.fetchall())

        # Check latest regime
        cursor.execute("SELECT * FROM regime_states ORDER BY detected_at DESC LIMIT 5")
        print("Recent Regimes:", cursor.fetchall())
    conn.close()
except Exception as e:
    print("Connection FAILED:", e)
