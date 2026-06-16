import pymysql
import os
import sys
from datetime import datetime
from dotenv import load_dotenv

# 루트 디렉토리를 path에 추가
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

load_dotenv()

from future.store.mariadb_store import MariaDBStore
from future.store.sheets_store import SheetsStore

def main():
    print("수동 매수 1계약 오버나잇에 맞게 MariaDB 및 Google Sheets 업데이트 시작")
    
    # 1. MariaDB 연결
    db = MariaDBStore(
        host=os.getenv("MARIADB_HOST", "127.0.0.1"),
        port=int(os.getenv("MARIADB_PORT", 3306)),
        user=os.getenv("MARIADB_USER", "kis_user"),
        password=os.getenv("MARIADB_PASSWORD", "kis_password"),
        database=os.getenv("MARIADB_DATABASE", "kis_trading")
    )
    
    # 2. Google Sheets 연결
    from config import config
    sheets = SheetsStore(
        credentials_path=config.GOOGLE_SA_KEY_PATH,
        sheet_id=config.GOOGLE_SHEET_ID
    )
    
    # 3. 포지션 데이터 준비
    avg_price = 1303.74
    qty = 1
    futures_code = "105V07"
    position_id = f"P_{futures_code}_LONG"
    
    pos = {
        "position_id": position_id,
        "futures_code": futures_code,
        "market": "day",
        "side": "LONG",
        "quantity": qty,
        "avg_price": avg_price,
        "stop_loss": avg_price * 0.98,
        "take_profit": avg_price * 1.02,
        "trailing_stop": None,
        "highest_price": avg_price,
        "lowest_price": avg_price,
        "last_checked_price": avg_price
    }
    
    # 4. 주문 데이터 준비
    order = {
        "order_id": "0000004219",  # 실제 매수 성공 주문번호
        "futures_code": futures_code,
        "order_side": "BUY",
        "order_qty": qty,
        "order_price": avg_price,
        "order_type": "LIMIT",
        "status": "FILLED",
        "result_msg": "수동 1계약 오버나잇 테스트 진입"
    }
    
    # 5. DB 저장
    print(f"MariaDB active_positions 저장: {position_id}")
    db.save_position(pos)
    
    print(f"MariaDB orders 저장: {order['order_id']}")
    db.save_order(order)
    
    # 타입 정돈
    for field in ["avg_price", "stop_loss", "take_profit", "trailing_stop", "highest_price", "lowest_price", "last_checked_price"]:
        if pos[field] is not None:
            pos[field] = float(pos[field])
            
    # 6. 구글 시트 저장
    print("Google Sheets active_positions 동기화")
    sheets.update_active_positions([pos])
    
    db.close()
    print("DB 및 시트 업데이트가 완료되었습니다.")

if __name__ == "__main__":
    main()
