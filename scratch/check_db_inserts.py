import os
import sys
from datetime import datetime
from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
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
    
    today_str = datetime.now().strftime("%Y-%m-%d")
    check_time_str = f"{today_str} 18:00:00"
    
    print(f"=== Checking DB Inserts since {check_time_str} ===")
    
    db._ensure_connection()
    with db.conn.cursor() as cursor:
        # 1. Check regime_states
        cursor.execute("SELECT * FROM regime_states WHERE detected_at >= %s ORDER BY detected_at DESC LIMIT 5;", (check_time_str,))
        regimes = cursor.fetchall()
        print(f"\nRegime States since 18:00 (count: {len(regimes)}):")
        for r in regimes:
            print(f"- Time: {r['detected_at']} | Regime: {r['regime']} | ADX: {r['adx']} | Action: {r['action']}")
            
        # 2. Check foreign_flows
        cursor.execute("SELECT * FROM foreign_flows WHERE fetched_at >= %s ORDER BY fetched_at DESC LIMIT 5;", (check_time_str,))
        flows = cursor.fetchall()
        print(f"\nForeign Flows since 18:00 (count: {len(flows)}):")
        for f in flows:
            print(f"- Time: {f['fetched_at']} | Foreign Net Buy: {f['foreign_net_buy']} | Institution Net Buy: {f['institution_net_buy']}")
            
        # 3. Check active_positions
        cursor.execute("SELECT * FROM active_positions;")
        positions = cursor.fetchall()
        print(f"\nActive Positions (count: {len(positions)}):")
        for p in positions:
            print(f"- ID: {p['position_id']} | Code: {p['futures_code']} | Price: {p['last_checked_price']} | Updated At: {p['updated_at']}")

if __name__ == "__main__":
    main()
