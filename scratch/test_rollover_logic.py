# -*- coding: utf-8 -*-
import os
import sys
import datetime
from unittest.mock import MagicMock

# Windows console encoding handling
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from future.supervisor import TradingSupervisor

def test_dates_and_rollover():
    print("==================================================")
    print("Testing Date Calculations and Rollover Logic")
    print("==================================================")
    
    # Create supervisor instance with mocked dependencies
    mock_db = MagicMock()
    mock_sheets = MagicMock()
    
    # Instantiate supervisor
    supervisor = TradingSupervisor(db_store=mock_db, sheets_store=mock_sheets)
    
    # Test second Thursday calculation
    thursdays = {
        (2026, 6): datetime.date(2026, 6, 11),  # June 2026 second Thursday
        (2026, 7): datetime.date(2026, 7, 9),   # July 2026 second Thursday
        (2026, 8): datetime.date(2026, 8, 13),  # Aug 2026 second Thursday
    }
    
    for (y, m), expected in thursdays.items():
        calculated = supervisor._get_second_thursday(y, m)
        assert calculated == expected, f"Failed for {y}-{m}: expected {expected}, got {calculated}"
        print(f"[OK] Second Thursday for {y}-{m:02d} matches expected: {calculated}")

    # Test rollover date calculation (3 business days before second Thursday)
    rollovers = {
        (2026, 6): datetime.date(2026, 6, 8),  # Mon, June 8 (3 business days before June 11)
        (2026, 7): datetime.date(2026, 7, 6),  # Mon, July 6 (3 business days before July 9)
        (2026, 8): datetime.date(2026, 8, 10), # Mon, Aug 10 (3 business days before Aug 13)
    }
    
    for (y, m), expected in rollovers.items():
        calculated = supervisor._get_rollover_date(y, m, days_before=3)
        assert calculated == expected, f"Failed rollover for {y}-{m}: expected {expected}, got {calculated}"
        print(f"[OK] Rollover date (3 days before) for {y}-{m:02d} matches expected: {calculated}")

    print("\n==================================================")
    print("Testing Code Mapping Logic")
    print("==================================================")
    
    # Test mapping short to db codes
    mapping_to_db = {
        "A05607": "105V07",
        "A05608": "105V08",
        "A05609": "105V09",
        "A01609": "101V09",
        "A01W09": "101W09",  # preserved letter
        "A01709": "101W09",  # digit 7 -> W
        "invalid": "invalid"
    }
    
    for short, expected in mapping_to_db.items():
        db_code = supervisor._to_db_code(short)
        assert db_code == expected, f"to_db mapping failed for {short}: expected {expected}, got {db_code}"
        print(f"[OK] Mapping: {short} -> {db_code}")

    # Test mapping db to short codes
    mapping_to_kis = {
        "105V07": "A05607",
        "105V08": "A05608",
        "105V09": "A05609",
        "101V09": "A01609",
        "101W09": "A01709",  # letter W -> digit 7
        "invalid": "invalid"
    }
    
    for db, expected in mapping_to_kis.items():
        kis_code = supervisor._to_kis_code(db)
        assert kis_code == expected, f"to_kis mapping failed for {db}: expected {expected}, got {kis_code}"
        print(f"[OK] Mapping: {db} -> {kis_code}")

    print("\nAll tests passed successfully! Done.")

if __name__ == "__main__":
    test_dates_and_rollover()
