import os
import sys
import csv
from datetime import datetime, timedelta
from dotenv import load_dotenv

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
load_dotenv()

from future.store.mariadb_store import MariaDBStore

def main():
    print("Testing DB backup and pruning logic...")
    
    host = os.getenv("MARIADB_HOST", "127.0.0.1")
    port = int(os.getenv("MARIADB_PORT", 3306))
    user = os.getenv("MARIADB_USER", "kis_user")
    password = os.getenv("MARIADB_PASSWORD", "kis_password")
    database = os.getenv("MARIADB_DATABASE", "kis_trading")
    
    db = MariaDBStore(
        host=host,
        port=port,
        user=user,
        password=password,
        database=database
    )
    
    now = datetime.now()
    old_time = now - timedelta(days=35)
    today_time = now
    
    print(f"Old time for test: {old_time}")
    print(f"Today time for test: {today_time}")
    
    # 1. Clear any existing test items if any (safety)
    db._ensure_connection()
    with db.conn.cursor() as cursor:
        cursor.execute("DELETE FROM regime_states WHERE detected_at = %s OR detected_at = %s;", (old_time.strftime("%Y-%m-%d %H:%M:%S"), today_time.strftime("%Y-%m-%d %H:%M:%S")))
        cursor.execute("DELETE FROM pre_market_basis WHERE fetched_at = %s OR fetched_at = %s;", (old_time.strftime("%Y-%m-%d %H:%M:%S"), today_time.strftime("%Y-%m-%d %H:%M:%S")))
    
    # 2. Insert dummy records
    print("Inserting test records...")
    with db.conn.cursor() as cursor:
        # regime_states
        cursor.execute("""
            INSERT INTO regime_states (detected_at, regime, adx, atr, volatility_level, trend_strength, action, signal_allowed, size_multiplier)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s);
        """, (old_time.strftime("%Y-%m-%d %H:%M:%S"), "ranging", 10.0, 1.2, "normal", "none", "Prune Test Old", 0, 0.0))
        
        cursor.execute("""
            INSERT INTO regime_states (detected_at, regime, adx, atr, volatility_level, trend_strength, action, signal_allowed, size_multiplier)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s);
        """, (today_time.strftime("%Y-%m-%d %H:%M:%S"), "trending", 30.0, 2.5, "normal", "strong", "Prune Test Today", 1, 1.0))
        
        # pre_market_basis
        cursor.execute("""
            INSERT INTO pre_market_basis (fetched_at, futures_code, expected_futures_price, expected_spot_return, expected_futures_return, temporary_basis)
            VALUES (%s, %s, %s, %s, %s, %s);
        """, (old_time.strftime("%Y-%m-%d %H:%M:%S"), "105V09", 340.0, 0.01, 0.01, 0.5))
        
        cursor.execute("""
            INSERT INTO pre_market_basis (fetched_at, futures_code, expected_futures_price, expected_spot_return, expected_futures_return, temporary_basis)
            VALUES (%s, %s, %s, %s, %s, %s);
        """, (today_time.strftime("%Y-%m-%d %H:%M:%S"), "105V09", 345.0, 0.02, 0.02, 0.6))

    # Verify rows exist
    with db.conn.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) FROM regime_states WHERE action LIKE 'Prune Test%';")
        print(f"Regime states test row count before pruning: {cursor.fetchone()['COUNT(*)']}")
        cursor.execute("SELECT COUNT(*) FROM pre_market_basis WHERE fetched_at = %s OR fetched_at = %s;", (old_time.strftime("%Y-%m-%d %H:%M:%S"), today_time.strftime("%Y-%m-%d %H:%M:%S")))
        print(f"Pre-market basis test row count before pruning: {cursor.fetchone()['COUNT(*)']}")

    # 3. Call backup and prune
    print("Executing backup_and_prune_old_data...")
    backup_dir = "backtest"
    db.backup_and_prune_old_data(backup_dir, 30)
    
    # 4. Verify results
    print("Verifying DB results...")
    with db.conn.cursor() as cursor:
        # Old should be deleted, today should remain
        cursor.execute("SELECT * FROM regime_states WHERE action = 'Prune Test Old';")
        assert cursor.fetchone() is None, "FAIL: Old regime state row was not deleted"
        
        cursor.execute("SELECT * FROM regime_states WHERE action = 'Prune Test Today';")
        assert cursor.fetchone() is not None, "FAIL: Today regime state row was deleted"
        
        cursor.execute("SELECT * FROM pre_market_basis WHERE fetched_at = %s;", (old_time.strftime("%Y-%m-%d %H:%M:%S"),))
        assert cursor.fetchone() is None, "FAIL: Old pre-market basis row was not deleted"
        
        cursor.execute("SELECT * FROM pre_market_basis WHERE fetched_at = %s;", (today_time.strftime("%Y-%m-%d %H:%M:%S"),))
        assert cursor.fetchone() is not None, "FAIL: Today pre-market basis row was deleted"

    print("Verifying CSV results...")
    regime_csv = os.path.join(backup_dir, "regime_states.csv")
    basis_csv = os.path.join(backup_dir, "pre_market_basis.csv")
    
    assert os.path.exists(regime_csv), "FAIL: regime_states.csv not created"
    assert os.path.exists(basis_csv), "FAIL: pre_market_basis.csv not created"
    
    # Read CSV and check if old record is there
    found_old_regime = False
    with open(regime_csv, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("action") == "Prune Test Old":
                found_old_regime = True
                
    found_old_basis = False
    with open(basis_csv, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # check old timestamp
            if row.get("fetched_at") == old_time.strftime("%Y-%m-%d %H:%M:%S"):
                found_old_basis = True
                
    assert found_old_regime, "FAIL: Old regime state row not found in CSV"
    assert found_old_basis, "FAIL: Old pre-market basis row not found in CSV"
    
    # Cleanup test items from DB
    with db.conn.cursor() as cursor:
        cursor.execute("DELETE FROM regime_states WHERE action = 'Prune Test Today';")
        cursor.execute("DELETE FROM pre_market_basis WHERE fetched_at = %s;", (today_time.strftime("%Y-%m-%d %H:%M:%S"),))
        
    print("ALL TESTS PASSED SUCCESSFULLY! ✅")
    db.close()

if __name__ == "__main__":
    main()
