import os
import pymysql
from datetime import datetime, date
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
        database=database,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor
    )
    
    with conn.cursor() as cursor:
        print("=== Database Analysis for Today (2026-06-15) ===")
        
        # 1. Check Morning Briefing Score
        cursor.execute("SELECT * FROM morning_briefing_scores WHERE briefing_date = '2026-06-15'")
        briefing = cursor.fetchone()
        print("\n1. Morning Briefing:")
        if briefing:
            print(f"  Date: {briefing['briefing_date']}")
            print(f"  Score: {briefing['score']}")
            print(f"  Direction: {briefing['direction']}")
            print(f"  Rationale: {briefing['rationale']}")
        else:
            print("  No morning briefing score found for today.")
            
        # 2. Check Regime States Distribution
        cursor.execute("""
            SELECT regime, COUNT(*) as count, AVG(adx) as avg_adx, AVG(atr) as avg_atr 
            FROM regime_states 
            WHERE DATE(detected_at) = '2026-06-15'
            GROUP BY regime
        """)
        regimes = cursor.fetchall()
        print("\n2. Regime States Distribution:")
        for r in regimes:
            print(f"  Regime: {r['regime']}, Count: {r['count']}, Avg ADX: {r['avg_adx']:.2f}, Avg ATR: {r['avg_atr']:.2f}")
            
        # 3. Check Foreign Flows Statistics
        cursor.execute("""
            SELECT COUNT(*) as count, AVG(flow_strength) as avg_strength, 
                   MIN(foreign_net_buy) as min_net, MAX(foreign_net_buy) as max_net,
                   AVG(foreign_net_buy_1m) as avg_1m
            FROM foreign_flows
            WHERE DATE(fetched_at) = '2026-06-15'
        """)
        flow = cursor.fetchone()
        print("\n3. Foreign Flows Stats:")
        if flow and flow['count'] > 0:
            print(f"  Count: {flow['count']}")
            print(f"  Avg Strength: {flow['avg_strength']:.2f}")
            print(f"  Min Net Buy: {flow['min_net']:,} | Max Net Buy: {flow['max_net']:,}")
            print(f"  Avg 1m change: {flow['avg_1m']:.2f}")
        else:
            print("  No foreign flows records for today.")
            
        # 4. Check Orders today
        cursor.execute("""
            SELECT * FROM orders 
            WHERE DATE(ordered_at) = '2026-06-15'
            ORDER BY ordered_at ASC
        """)
        orders = cursor.fetchall()
        print("\n4. Orders Today:")
        if orders:
            for o in orders:
                print(f"  Time: {o['ordered_at']} | Side: {o['order_side']} | Qty: {o['order_qty']} | Price: {o['order_price']} | Status: {o['status']} | Msg: {o['result_msg']}")
        else:
            print("  No orders found for today.")
            
        # 5. Check Trades today
        cursor.execute("""
            SELECT * FROM trades 
            WHERE DATE(exit_time) = '2026-06-15'
            ORDER BY exit_time ASC
        """)
        trades = cursor.fetchall()
        print("\n5. Trades Today:")
        if trades:
            for t in trades:
                print(f"  Exit Time: {t['exit_time']} | Code: {t['futures_code']} | Entry Side: {t['entry_side']} | Qty: {t['entry_qty']} | Entry Price: {t['entry_price']} | Exit Price: {t['exit_price']} | PnL: {t['net_pnl']:,}원")
        else:
            print("  No completed trades found for today.")

        # 6. Check Active Positions
        cursor.execute("SELECT * FROM active_positions")
        positions = cursor.fetchall()
        print("\n6. Current Active Positions:")
        if positions:
            for p in positions:
                print(f"  Code: {p['futures_code']} | Side: {p['side']} | Qty: {p['quantity']} | Avg Price: {p['avg_price']} | Stop Loss: {p['stop_loss']} | Take Profit: {p['take_profit']}")
        else:
            print("  No active positions.")

        # 7. Check Candle data today to see if prices are updating
        cursor.execute("""
            SELECT COUNT(*) as count, MIN(candle_time) as min_time, MAX(candle_time) as max_time
            FROM market_candles
            WHERE DATE(candle_time) = '2026-06-15'
        """)
        candles_info = cursor.fetchone()
        print("\n7. Market Candles Today:")
        if candles_info and candles_info['count'] > 0:
            print(f"  Count: {candles_info['count']} | Min Time: {candles_info['min_time']} | Max Time: {candles_info['max_time']}")
        else:
            print("  No market candles records for today.")

    conn.close()
except Exception as e:
    print("Database connection or execution failed:", e)
