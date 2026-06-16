import os
import sys
import asyncio
from datetime import datetime
from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
load_dotenv()

from future.store.mariadb_store import MariaDBStore
from future.engines.execution_engine import ExecutionEngine
from future.engines.foreign_flow_engine import ForeignFlowEngine
from future.engines.regime_engine import RegimeEngine

db = MariaDBStore(
    host=os.getenv("MARIADB_HOST", "127.0.0.1"),
    port=int(os.getenv("MARIADB_PORT", 3306)),
    user=os.getenv("MARIADB_USER", "kis_user"),
    password=os.getenv("MARIADB_PASSWORD", "kis_password"),
    database=os.getenv("MARIADB_DATABASE", "kis_trading")
)

engine = ExecutionEngine()
flow_engine = ForeignFlowEngine()
regime_engine = RegimeEngine()

async def main():
    print("=" * 60)
    print("[1] REST API price query")
    price = await engine.fetch_futures_price_rest("A05609")
    print(f"  -> price: {price}")
    assert price is not None and price > 0
    print("  [OK] REST price fetch success")

    print()
    print("[2] active_positions price update")
    positions = db.get_active_positions()
    print(f"  -> positions: {len(positions)}")
    for pos in positions:
        print(f"  -> BEFORE: {pos['position_id']} last_checked={pos['last_checked_price']} highest={pos['highest_price']}")
        pos["last_checked_price"] = price
        pos["updated_at"] = datetime.now()
        if pos["side"] == "LONG":
            cur_high = float(pos["highest_price"]) if pos["highest_price"] else 0
            if price > cur_high:
                pos["highest_price"] = price
        elif pos["side"] == "SHORT":
            cur_low = float(pos["lowest_price"]) if pos["lowest_price"] else 99999
            if price < cur_low:
                pos["lowest_price"] = price
        db.save_position(pos)
    updated = db.get_active_positions()
    for pos in updated:
        print(f"  -> AFTER:  {pos['position_id']} last_checked={pos['last_checked_price']} highest={pos['highest_price']}")
    print("  [OK] position price update success")

    print()
    print("[3] regime_states insert")
    regime_state = regime_engine.detect(price, [])
    db_regime = {k: v for k, v in regime_state.items() if k != "detected_at"}
    db_regime["signal_allowed"] = 1 if db_regime.get("signal_allowed") else 0
    db.save_regime_state(db_regime)
    latest = db.get_latest_regime()
    print(f"  -> saved: regime={latest['regime']}, adx={latest['adx']}, atr={latest['atr']}")
    print("  [OK] regime_states insert success")

    print()
    print("[4] foreign_flows insert")
    raw_trend = await engine.fetch_investor_trend()
    print(f"  -> raw_trend: {raw_trend}")
    flow_engine.update_flow(raw_trend)
    flow_data = flow_engine.get_latest_flow()
    db_flow = {
        "foreign_net_buy": flow_data["foreign_net_buy"],
        "institution_net_buy": flow_data["institution_net_buy"],
        "individual_net_buy": flow_data["individual_net_buy"],
        "foreign_oi_change": flow_data["foreign_oi_change"],
        "flow_strength": flow_data["flow_strength"]
    }
    db.save_foreign_flow(db_flow)
    print(f"  -> saved: foreign={db_flow['foreign_net_buy']:+} institution={db_flow['institution_net_buy']:+}")
    print("  [OK] foreign_flows insert success")

    print()
    print("[5] orders insert (dummy)")
    order_id = f"O_TEST_{int(datetime.now().timestamp())}"
    db.save_order({
        "order_id": order_id,
        "futures_code": "105V09",
        "order_side": "BUY",
        "order_qty": 1,
        "order_price": price,
        "order_type": "LIMIT",
        "status": "FILLED",
        "result_msg": "Test order record"
    })
    orders = db.get_orders(1)
    print(f"  -> saved: {orders[0]['order_id']} status={orders[0]['status']}")
    print("  [OK] orders insert success")

    print()
    print("[6] trades insert (dummy)")
    trade_id = f"T_TEST_{int(datetime.now().timestamp())}"
    db.save_trade({
        "trade_id": trade_id,
        "futures_code": "105V09",
        "entry_side": "LONG",
        "entry_qty": 1,
        "entry_price": 1214.30,
        "exit_price": price,
        "entry_time": datetime.now(),
        "exit_time": datetime.now(),
        "net_pnl": (price - 1214.30) * 50000,
        "fee": 0.0
    })
    trades = db.get_recent_trades(1)
    print(f"  -> saved: {trades[0]['trade_id']} pnl={trades[0]['net_pnl']}")
    print("  [OK] trades insert success")

    print()
    print("=" * 60)
    print("ALL TABLES VERIFIED OK")
    print("=" * 60)

if __name__ == "__main__":
    asyncio.run(main())
