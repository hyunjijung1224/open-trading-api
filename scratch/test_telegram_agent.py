import os
import sys
import asyncio
from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
load_dotenv()

# Reconfigure stdout to support UTF-8 characters on Windows terminal
try:
    sys.stdout.reconfigure(encoding='utf-8')
except AttributeError:
    pass

from future.store.mariadb_store import MariaDBStore
from future.supervisor import TradingSupervisor
from future.engines import ExecutionEngine

# Mock classes for testing
class MockSheetsStore:
    def update_active_positions(self, pos):
        pass
    def update_bot_health(self, metrics):
        pass
    def append_trade_history(self, trade):
        pass

class MockWebSocketManager:
    def get_latest_price(self, code):
        return 320.50

async def main():
    print("=== Testing TelegramAgent Natural Language Processing ===")
    
    # 1. Initialize DB Store
    db_store = MariaDBStore(
        host=os.getenv("MARIADB_HOST", "127.0.0.1"),
        port=int(os.getenv("MARIADB_PORT", 3306)),
        user=os.getenv("MARIADB_USER", "kis_user"),
        password=os.getenv("MARIADB_PASSWORD", "kis_password"),
        database=os.getenv("MARIADB_DATABASE", "kis_trading")
    )
    
    # 2. Setup Supervisor with mocks
    sheets_store = MockSheetsStore()
    supervisor = TradingSupervisor(db_store=db_store, sheets_store=sheets_store)
    supervisor.ws_manager = MockWebSocketManager()
    supervisor.execution_engine = ExecutionEngine()
    
    agent = supervisor.telegram_agent
    
    # 3. Test Cases
    test_queries = [
        "계좌 평가자산이랑 잔고 조회해줘.",
        "현재 보유하고 있는 선물 포지션이 어떻게 돼?",
        "최근 주문한 내역 5개만 보여줘.",
        "트레이딩에서 리스크 관리가 중요한 이유에 대해 설명해줘." # General question
    ]
    
    for idx, query in enumerate(test_queries, 1):
        print(f"\n[Test Case {idx}] User: {query}")
        print("Agent is thinking...")
        reply = await agent.handle_user_query(query)
        print(f"Agent Reply:\n{reply}")
        print("-" * 50)

if __name__ == "__main__":
    asyncio.run(main())
