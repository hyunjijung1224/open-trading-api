import unittest
import asyncio
from datetime import datetime, time as datetime_time
from unittest.mock import MagicMock, patch, AsyncMock

# Mock dependencies before importing
import sys
sys.modules['future.store.mariadb_store'] = MagicMock()
sys.modules['future.store.sheets_store'] = MagicMock()
sys.modules['future.ws_manager'] = MagicMock()
sys.modules['future.engines.telegram_agent'] = MagicMock()

from future.supervisor import TradingSupervisor
from config import config

class TestSessionSchedule(unittest.TestCase):
    def setUp(self):
        self.db_store = MagicMock()
        self.sheets_store = MagicMock()
        self.supervisor = TradingSupervisor(self.db_store, self.sheets_store)
        # Enable night trading for test coverage
        config.ENABLE_NIGHT_TRADING = True
        config.FORCE_CLOSE_MINUTES_BEFORE_CLOSE = 5

    def test_second_thursday(self):
        # June 2026: 1st is Monday, 2nd Thursday is June 11th.
        sec_thurs_june = self.supervisor._get_second_thursday(2026, 6)
        self.assertEqual(sec_thurs_june.day, 11)

        # July 2026: 1st is Wednesday, 2nd Thursday is July 9th.
        sec_thurs_july = self.supervisor._get_second_thursday(2026, 7)
        self.assertEqual(sec_thurs_july.day, 9)

        # August 2026: 1st is Saturday, 2nd Thursday is August 13th.
        sec_thurs_aug = self.supervisor._get_second_thursday(2026, 8)
        self.assertEqual(sec_thurs_aug.day, 13)

    def test_is_final_trading_day(self):
        # Normal day: June 10, 2026 (Wednesday)
        normal_dt = datetime(2026, 6, 10, 10, 0, 0)
        self.assertFalse(self.supervisor._is_final_trading_day(normal_dt))

        # Final day: June 11, 2026 (Thursday, 2nd Thursday)
        final_dt = datetime(2026, 6, 11, 10, 0, 0)
        self.assertTrue(self.supervisor._is_final_trading_day(final_dt))

    @patch('config.config.get_kst_now')
    def test_get_current_session_normal_day(self, mock_get_kst_now):
        # Test on Wednesday (Weekday, June 10, 2026)
        
        # 1. Day Market Open (08:45)
        mock_get_kst_now.return_value = datetime(2026, 6, 10, 8, 45, 0)
        self.assertEqual(self.supervisor._get_current_session(), "day_market")
        
        # 2. Day Market Middle (12:00)
        mock_get_kst_now.return_value = datetime(2026, 6, 10, 12, 0, 0)
        self.assertEqual(self.supervisor._get_current_session(), "day_market")

        # 3. Day Market End (15:45)
        mock_get_kst_now.return_value = datetime(2026, 6, 10, 15, 45, 0)
        self.assertEqual(self.supervisor._get_current_session(), "day_market")

        # 4. Day Close (15:46)
        mock_get_kst_now.return_value = datetime(2026, 6, 10, 15, 46, 0)
        self.assertEqual(self.supervisor._get_current_session(), "day_close")

        # 5. Gap (16:30)
        mock_get_kst_now.return_value = datetime(2026, 6, 10, 16, 30, 0)
        self.assertEqual(self.supervisor._get_current_session(), "gap")

        # 6. Night Market (18:00)
        mock_get_kst_now.return_value = datetime(2026, 6, 10, 18, 0, 0)
        self.assertEqual(self.supervisor._get_current_session(), "night_market")

        # 7. Night Market Late (02:00 next day, Thursday June 11)
        mock_get_kst_now.return_value = datetime(2026, 6, 11, 2, 0, 0)
        self.assertEqual(self.supervisor._get_current_session(), "night_market")

        # 8. Night Market End (06:00)
        mock_get_kst_now.return_value = datetime(2026, 6, 11, 6, 0, 0)
        self.assertEqual(self.supervisor._get_current_session(), "night_market")

        # 9. Night Close (06:15)
        mock_get_kst_now.return_value = datetime(2026, 6, 11, 6, 15, 0)
        self.assertEqual(self.supervisor._get_current_session(), "night_close")

        # 10. Sleep (07:00)
        mock_get_kst_now.return_value = datetime(2026, 6, 11, 7, 0, 0)
        self.assertEqual(self.supervisor._get_current_session(), "sleep")

    @patch('config.config.get_kst_now')
    def test_get_current_session_final_day(self, mock_get_kst_now):
        # Test on Expiration Day (June 11, 2026)
        
        # 1. Day Market Open (08:45)
        mock_get_kst_now.return_value = datetime(2026, 6, 11, 8, 45, 0)
        self.assertEqual(self.supervisor._get_current_session(), "day_market")

        # 2. Expiration Market Middle (12:00)
        mock_get_kst_now.return_value = datetime(2026, 6, 11, 12, 0, 0)
        self.assertEqual(self.supervisor._get_current_session(), "day_market")

        # 3. Shortened Day Market Close (15:20)
        mock_get_kst_now.return_value = datetime(2026, 6, 11, 15, 20, 0)
        self.assertEqual(self.supervisor._get_current_session(), "day_market")

        # 4. Shortened Day Close Session (15:21)
        mock_get_kst_now.return_value = datetime(2026, 6, 11, 15, 21, 0)
        self.assertEqual(self.supervisor._get_current_session(), "day_close")

        # 5. Gap Session (16:30)
        mock_get_kst_now.return_value = datetime(2026, 6, 11, 16, 30, 0)
        self.assertEqual(self.supervisor._get_current_session(), "gap")

    @patch('config.config.get_kst_now')
    def test_get_current_session_weekend(self, mock_get_kst_now):
        # Saturday: June 13, 2026
        
        # 1. Saturday 02:00 (still night market of Friday session)
        mock_get_kst_now.return_value = datetime(2026, 6, 13, 2, 0, 0)
        self.assertEqual(self.supervisor._get_current_session(), "night_market")

        # 2. Saturday 06:15 (night close of Friday session)
        mock_get_kst_now.return_value = datetime(2026, 6, 13, 6, 15, 0)
        self.assertEqual(self.supervisor._get_current_session(), "night_close")

        # 3. Saturday 07:00 (weekend sleep)
        mock_get_kst_now.return_value = datetime(2026, 6, 13, 7, 0, 0)
        self.assertEqual(self.supervisor._get_current_session(), "sleep")

        # Sunday: June 14, 2026
        mock_get_kst_now.return_value = datetime(2026, 6, 14, 12, 0, 0)
        self.assertEqual(self.supervisor._get_current_session(), "sleep")

    @patch('config.config.get_kst_now')
    def test_force_close_window(self, mock_get_kst_now):
        self.supervisor.active_positions = [{"position_id": "test_pos", "futures_code": "105V06", "side": "LONG", "quantity": 1, "avg_price": 350.0, "stop_loss": 340.0, "take_profit": 360.0, "updated_at": datetime.now()}]
        self.supervisor._execute_emergency_close = AsyncMock(return_value=None)
        
        # 1. Normal Day Force Close Window (15:40 ~ 15:45)
        # At 15:39 - no force close
        mock_get_kst_now.return_value = datetime(2026, 6, 10, 15, 39, 0)
        res = asyncio.run(self.supervisor._check_and_force_close_at_session_end())
        self.assertFalse(res)
        
        # At 15:40 - force close triggered
        mock_get_kst_now.return_value = datetime(2026, 6, 10, 15, 40, 0)
        res = asyncio.run(self.supervisor._check_and_force_close_at_session_end())
        self.assertTrue(res)
        self.supervisor._execute_emergency_close.assert_called_once()
        self.supervisor._execute_emergency_close.reset_mock()

        # 2. Expiration Day Force Close Window (15:15 ~ 15:20)
        # At 15:14 - no force close
        mock_get_kst_now.return_value = datetime(2026, 6, 11, 15, 14, 0)
        res = asyncio.run(self.supervisor._check_and_force_close_at_session_end())
        self.assertFalse(res)

        # At 15:15 - force close triggered
        mock_get_kst_now.return_value = datetime(2026, 6, 11, 15, 15, 0)
        res = asyncio.run(self.supervisor._check_and_force_close_at_session_end())
        self.assertTrue(res)
        self.supervisor._execute_emergency_close.assert_called_once()
        self.supervisor._execute_emergency_close.reset_mock()

        # 3. Night Market Force Close Window (05:55 ~ 06:00 next day)
        # At 05:54 - no force close
        mock_get_kst_now.return_value = datetime(2026, 6, 12, 5, 54, 0)
        res = asyncio.run(self.supervisor._check_and_force_close_at_session_end())
        self.assertFalse(res)

        # At 05:55 - force close triggered
        mock_get_kst_now.return_value = datetime(2026, 6, 12, 5, 55, 0)
        res = asyncio.run(self.supervisor._check_and_force_close_at_session_end())
        self.assertTrue(res)
        self.supervisor._execute_emergency_close.assert_called_once()

    def test_calculate_indicators_morning_mode_window(self):
        # Verify morning mode is active during 08:45 ~ 09:45 when candles < 59
        self.supervisor._has_morning_entry_today = MagicMock(return_value=False)
        self.supervisor.db.get_morning_briefing_score = MagicMock(return_value={"direction": "BUY", "score": "0.8"})

        # Inside morning window: 09:00 with 10 candles
        with patch('future.supervisor.datetime') as mock_datetime:
            mock_datetime.now.return_value = datetime(2026, 6, 10, 9, 0, 0)
            indicators = self.supervisor._calculate_indicators(candles=[{}]*10, current_price=350.0)
            self.assertTrue(indicators.get("is_morning_mode"))
            self.assertEqual(indicators.get("morning_direction"), "BUY")
            self.assertEqual(indicators.get("morning_score"), 0.8)

        # Inside morning window: 08:45 with 10 candles
        with patch('future.supervisor.datetime') as mock_datetime:
            mock_datetime.now.return_value = datetime(2026, 6, 10, 8, 45, 0)
            indicators = self.supervisor._calculate_indicators(candles=[{}]*10, current_price=350.0)
            self.assertTrue(indicators.get("is_morning_mode"))

        # Outside morning window: 09:50
        with patch('future.supervisor.datetime') as mock_datetime:
            mock_datetime.now.return_value = datetime(2026, 6, 10, 9, 50, 0)
            indicators = self.supervisor._calculate_indicators(candles=[{}]*10, current_price=350.0)
            self.assertEqual(indicators, {})

    @patch('future.supervisor.asyncio.create_task')
    def test_stop_loss_and_take_profit(self, mock_create_task):
        # Mock dependencies and components
        self.supervisor._execute_emergency_close = MagicMock()
        self.supervisor._execute_partial_close = MagicMock()
        self.supervisor._aggregate_candle = MagicMock()
        
        # Test Case 1: Long position, quantity = 2, stop loss hit
        pos1 = {
            "position_id": "P_105V07_LONG",
            "futures_code": "105V07",
            "side": "LONG",
            "quantity": 2,
            "avg_price": 350.0,
            "stop_loss": 348.0,
            "take_profit": 354.0,
            "updated_at": datetime.now()
        }
        self.supervisor.active_positions = [pos1]
        
        tick1 = {"code": "A05607", "price": 347.5, "volume": 10, "time": "090000"}
        self.supervisor._on_realtime_execution(tick1)
        
        # Should execute emergency close (100% stop loss) instead of partial close
        self.supervisor._execute_emergency_close.assert_called_once_with(pos1, "실시간 전량 손절 트리거")
        self.supervisor._execute_partial_close.assert_not_called()
        mock_create_task.assert_called_once()
        
        # Reset mocks
        self.supervisor._execute_emergency_close.reset_mock()
        self.supervisor._execute_partial_close.reset_mock()
        mock_create_task.reset_mock()
        
        # Test Case 2: Long position, quantity = 2, take profit hit
        pos2 = {
            "position_id": "P_105V07_LONG",
            "futures_code": "105V07",
            "side": "LONG",
            "quantity": 2,
            "avg_price": 350.0,
            "stop_loss": 348.0,
            "take_profit": 354.0,
            "updated_at": datetime.now()
        }
        self.supervisor.active_positions = [pos2]
        
        tick2 = {"code": "A05607", "price": 354.5, "volume": 10, "time": "090000"}
        self.supervisor._on_realtime_execution(tick2)
        
        # Should execute partial close (50% take profit)
        self.supervisor._execute_partial_close.assert_called_once_with(pos2, 1, "실시간 분할 익절 트리거")
        self.supervisor._execute_emergency_close.assert_not_called()
        mock_create_task.assert_called_once()

    def test_websocket_is_connected(self):
        # We need to unmock modules temporarily to import the real WebSocketManager if it was mocked.
        # But wait! In setUp, sys.modules['future.ws_manager'] was mocked.
        # Let's import the actual module by resolving it from sys.path or patching sys.modules
        import sys
        real_ws_module = sys.modules.get('future.ws_manager')
        
        # If it was mocked, let's load it directly or unmock it
        if real_ws_module and isinstance(real_ws_module, MagicMock):
            # We can unmock it temporarily for the test
            del sys.modules['future.ws_manager']
            from future.ws_manager import WebSocketManager
            sys.modules['future.ws_manager'] = real_ws_module
        else:
            from future.ws_manager import WebSocketManager
            
        manager = WebSocketManager(
            ws_url="ws://dummy",
            app_key="dummy",
            app_secret="dummy"
        )
        
        # Test Case 1: ws is None -> is_connected is False
        self.assertFalse(manager.is_connected)
        
        # Test Case 2: ws has 'open' property
        class DummyWsOpen:
            def __init__(self, is_open):
                self.open = is_open
        
        manager.ws = DummyWsOpen(True)
        self.assertTrue(manager.is_connected)
        
        manager.ws = DummyWsOpen(False)
        self.assertFalse(manager.is_connected)
        
        # Test Case 3: ws has only 'state' property (websockets v14+)
        class DummyState:
            def __init__(self, name):
                self.name = name
                
        class DummyWsState:
            def __init__(self, name):
                self.state = DummyState(name)
        
        manager.ws = DummyWsState("OPEN")
        self.assertTrue(manager.is_connected)
        
        manager.ws = DummyWsState("CLOSED")
        self.assertFalse(manager.is_connected)

if __name__ == '__main__':
    unittest.main()
