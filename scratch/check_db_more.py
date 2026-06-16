import os
import pymysql
from dotenv import load_dotenv

load_dotenv()

host = os.getenv("MARIADB_HOST", "127.0.0.1")
port = int(os.getenv("MARIADB_PORT", 3306))
user = os.getenv("MARIADB_USER", "kis_user")
password = os.getenv("MARIADB_PASSWORD", "kis_password")
database = os.getenv("MARIADB_DATABASE", "kis_trading")

try:
    conn = pymysql.connect(
        host=host,
        port=port,
        user=user,
        password=password,
        connect_timeout=2
    )
    with conn.cursor() as cursor:
        cursor.execute("SHOW DATABASES")
        print("All databases:", cursor.fetchall())
    conn.close()
    
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
        # Check regime_states around 14:54 (2:54 PM)
        cursor.execute("""
            SELECT * FROM regime_states 
            WHERE detected_at BETWEEN '2026-06-15 14:50:00' AND '2026-06-15 15:00:00'
            ORDER BY detected_at ASC
        """)
        regimes = cursor.fetchall()
        print("\nRegimes between 14:50 and 15:00:")
        for r in regimes:
            print(f"  Time: {r['detected_at']} | Regime: {r['regime']} | ADX: {r['adx']} | ATR: {r['atr']} | Allowed: {r['signal_allowed']}")
            
        # Check if there are any records in other tables
        cursor.execute("SELECT * FROM performance_metrics ORDER BY calculated_at DESC LIMIT 5")
        print("\nLatest performance metrics:", cursor.fetchall())
        
        cursor.execute("SELECT * FROM foreign_flows ORDER BY fetched_at DESC LIMIT 5")
        print("\nLatest foreign flows:", cursor.fetchall())
        
    conn.close()
except Exception as e:
    print("Failed:", e)
