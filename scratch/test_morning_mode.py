import os
import sys
import logging
from datetime import datetime, time as datetime_time
from unittest.mock import MagicMock, patch
from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from future.store.mariadb_store import MariaDBStore
from future.engines import SignalEngine
from future.supervisor import TradingSupervisor

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("TestMorningMode")

load_dotenv()

def test_morning_mode_logic():
    # 1. Initialize DB Store
    try:
        db = MariaDBStore(
            host=os.getenv("MARIADB_HOST", "127.0.0.1"),
            port=int(os.getenv("MARIADB_PORT", 3306)),
            user=os.getenv("MARIADB_USER", "kis_user"),
            password=os.getenv("MARIADB_PASSWORD", "kis_password"),
            database=os.getenv("MARIADB_DATABASE", "kis_trading")
        )
    except Exception as e:
        logger.error(f"Failed to connect to MariaDB: {e}")
        return

    # 2. Insert mock morning briefing score for today
    today_str = datetime.now().strftime("%Y-%m-%d")
    mock_score = 0.75
    mock_direction = "BUY"
    mock_rationale = "Test rationale: US markets surged, pre-market domestic sentiment strong"
    
    logger.info(f"Inserting mock morning briefing score: date={today_str}, direction={mock_direction}, score={mock_score}")
    db.save_morning_briefing_score(today_str, mock_score, mock_direction, mock_rationale)
    
    # Verify retrieval
    retrieved = db.get_morning_briefing_score(today_str)
    assert retrieved is not None, "Failed to retrieve morning briefing score"
    logger.info(f"Retrieved morning score: {retrieved}")
    
    # 3. Test SignalEngine when is_morning_mode is active
    logger.info("Testing SignalEngine generate() with is_morning_mode=True...")
    sig_engine = SignalEngine()
    indicators_morning = {
        "is_morning_mode": True,
        "morning_direction": "BUY",
        "morning_score": 0.75,
        "atr": 2.0,
        "current_price": 1300.0
    }
    signal = sig_engine.generate(
        code="105V09",
        regime="weak_trend",
        flow_direction="NEUTRAL",
        foreign_zscore=0.0,
        indicators=indicators_morning
    )
    logger.info(f"Generated morning signal: {signal}")
    assert signal["direction"] == "BUY", "Morning signal direction should be BUY"
    assert "모닝브리핑" in signal["reasons"][0], "Reasons should state morning briefing entry"
    logger.info("SignalEngine morning mode test passed! ✅")
    
    # 4. Test TradingSupervisor _calculate_indicators and entry limitation
    logger.info("Testing TradingSupervisor _calculate_indicators with mocked times...")
    
    # Instantiate supervisor in mock environment (mocking components not needed for indicator calculations)
    sheets_mock = MagicMock()
    supervisor = TradingSupervisor(db_store=db, sheets_store=sheets_mock)
    
    # Case A: Inside morning window (09:30 AM), fewer than 59 candles, no trades today
    mock_now_morning = datetime(2026, 6, 14, 9, 30, 0)
    with patch('future.supervisor.datetime') as mock_datetime:
        mock_datetime.now.return_value = mock_now_morning
        mock_datetime.strptime = datetime.strptime
        
        # Ensure we have no active positions or trades today
        supervisor.active_positions = []
        # Mock get_recent_trades to return empty list
        db.get_recent_trades = MagicMock(return_value=[])
        
        candles = [{"close": 1300.0} for _ in range(10)] # Only 10 candles (insufficient)
        indicators = supervisor._calculate_indicators(candles, 1300.0)
        logger.info(f"Indicators at 09:30 AM with 10 candles: {indicators}")
        assert indicators.get("is_morning_mode") is True, "is_morning_mode should be True"
        assert indicators.get("morning_direction") == "BUY", "morning_direction should be BUY"
        logger.info("Supervisor Morning mode indicator bypass passed! ✅")

    # Case B: Inside morning window (09:30 AM), but already entered trade today
    with patch('future.supervisor.datetime') as mock_datetime:
        mock_datetime.now.return_value = mock_now_morning
        mock_datetime.strptime = datetime.strptime
        
        # Mocking active_positions to simulate an active trade
        supervisor.active_positions = [{"futures_code": "105V09", "side": "LONG", "quantity": 1}]
        indicators = supervisor._calculate_indicators(candles, 1300.0)
        logger.info(f"Indicators at 09:30 AM when trade already exists: {indicators}")
        assert indicators == {}, "Indicators should be empty (trade already exists today)"
        logger.info("Supervisor Morning mode guard (no duplicate entries) passed! ✅")

    # Case C: Outside morning window (10:15 AM), fewer than 59 candles
    mock_now_late = datetime(2026, 6, 14, 10, 15, 0)
    with patch('future.supervisor.datetime') as mock_datetime:
        mock_datetime.now.return_value = mock_now_late
        mock_datetime.strptime = datetime.strptime
        
        supervisor.active_positions = []
        db.get_recent_trades = MagicMock(return_value=[])
        
        indicators = supervisor._calculate_indicators(candles, 1300.0)
        logger.info(f"Indicators at 10:15 AM with 10 candles: {indicators}")
        assert indicators == {}, "Indicators should be empty outside morning window if candles < 59"
        logger.info("Supervisor normal window minimum candle constraint passed! ✅")

    db.close()
    logger.info("All morning mode verification tests passed successfully! 🎉")

if __name__ == "__main__":
    test_morning_mode_logic()
