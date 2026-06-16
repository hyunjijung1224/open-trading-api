import pymysql
import os
import sys
import gspread
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
load_dotenv()

from future.store.mariadb_store import MariaDBStore
from config import config

def main():
    target_trade_id = 'T_1781245891'
    fixed_exit_price = 1294.70
    fixed_net_pnl = -82500.00
    
    print(f"--- 거래 복구 스크립트 실행 (대상 Trade ID: {target_trade_id}) ---")
    
    # 1. MariaDB 수정
    db = MariaDBStore(
        host=os.getenv("MARIADB_HOST", "127.0.0.1"),
        port=int(os.getenv("MARIADB_PORT", 3306)),
        user=os.getenv("MARIADB_USER", "kis_user"),
        password=os.getenv("MARIADB_PASSWORD", "kis_password"),
        database=os.getenv("MARIADB_DATABASE", "kis_trading")
    )
    
    db._ensure_connection()
    query = """
        UPDATE trades 
        SET exit_price = %s, net_pnl = %s 
        WHERE trade_id = %s
    """
    with db.conn.cursor() as cursor:
        affected = cursor.execute(query, (fixed_exit_price, fixed_net_pnl, target_trade_id))
        print(f"MariaDB 업데이트 결과: {affected}행 수정됨.")
        
    db.close()
    
    # 2. Google Sheets 수정
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
    try:
        creds = Credentials.from_service_account_file(config.GOOGLE_SA_KEY_PATH, scopes=scopes)
        client = gspread.authorize(creds)
        spreadsheet = client.open_by_key(config.GOOGLE_SHEET_ID)
        sheet = spreadsheet.worksheet("TradingHistory")
        
        # trade_id 열(1번째 열)에서 target_trade_id의 위치 찾기
        cell = sheet.find(target_trade_id)
        if cell:
            row_idx = cell.row
            print(f"Google Sheets: {row_idx}번째 행에서 거래 ID {target_trade_id} 발견.")
            
            # exit_price(6번째 열), net_pnl(9번째 열) 업데이트
            sheet.update_cell(row_idx, 6, fixed_exit_price)
            sheet.update_cell(row_idx, 9, fixed_net_pnl)
            print("Google Sheets 업데이트 완료.")
        else:
            print(f"Google Sheets: {target_trade_id} 거래 ID를 찾을 수 없습니다.")
    except Exception as e:
        print(f"Google Sheets 업데이트 중 에러 발생: {e}")
        
    print("거래 데이터 복구가 정상 완료되었습니다.")

if __name__ == "__main__":
    main()
