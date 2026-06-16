import os
import sys
from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config import config
from future.store.mariadb_store import MariaDBStore

load_dotenv()

db = MariaDBStore(
    host=os.getenv("MARIADB_HOST", "127.0.0.1"),
    port=int(os.getenv("MARIADB_PORT", 3306)),
    user=os.getenv("MARIADB_USER", "kis_user"),
    password=os.getenv("MARIADB_PASSWORD", "kis_password"),
    database=os.getenv("MARIADB_DATABASE", "kis_trading")
)

print("=== Active Positions ===")
positions = db.get_active_positions()
for pos in positions:
    print(pos)

print("\n=== Recent Orders ===")
orders = db.get_orders(5)
for order in orders:
    print(order)

print("\n=== Recent Trades ===")
trades = db.get_recent_trades(5)
for trade in trades:
    print(trade)

print("\n=== Latest Regime ===")
regime = db.get_latest_regime()
print(regime)
