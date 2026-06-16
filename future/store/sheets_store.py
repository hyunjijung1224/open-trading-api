import logging
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime
from typing import List, Dict, Any

logger = logging.getLogger("SheetsStore")

class SheetsStore:
    """
    Google Sheets 기반 상태 동기화 및 백업 스토어 (GCE & Cloud Run 공용)
    - ActivePositions: 실시간 보유 포지션 복제 (Cloud Run 장애 복구 원천 데이터)
    - TradingHistory: 완료된 거래 이력 기록 (누적)
    - BotHealth: GCE 봇 주기적 헬스 체크 상태 기록
    """
    def __init__(self, credentials_path: str, sheet_id: str):
        self.credentials_path = credentials_path
        self.sheet_id = sheet_id
        self.client = None
        self.spreadsheet = None
        self._authenticate()

    def _authenticate(self):
        """Google Service Account 자격 증명을 이용해 Sheets API 인증"""
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive"
        ]
        try:
            creds = Credentials.from_service_account_file(self.credentials_path, scopes=scopes)
            self.client = gspread.authorize(creds)
            self.spreadsheet = self.client.open_by_key(self.sheet_id)
            logger.info("Google Sheets API 인증 및 스프레드시트 연결 성공.")
            self._ensure_sheets()
        except Exception as e:
            logger.error(f"Google Sheets API 연결 실패: {e}")
            raise e

    def _ensure_sheets(self):
        """필요한 시트(Worksheet)가 존재하는지 확인하고 없으면 자동 생성"""
        existing_sheets = [s.title for s in self.spreadsheet.worksheets()]
        
        # 1. ActivePositions
        if "ActivePositions" not in existing_sheets:
            self.spreadsheet.add_worksheet(title="ActivePositions", rows="100", cols="12")
            sheet = self.spreadsheet.worksheet("ActivePositions")
            headers = [
                "position_id", "futures_code", "market", "side", "quantity", 
                "avg_price", "stop_loss", "take_profit", "trailing_stop", 
                "highest_price", "lowest_price", "updated_at"
            ]
            sheet.append_row(headers)
            logger.info("Google Sheets: ActivePositions 시트를 생성했습니다.")

        # 2. TradingHistory
        if "TradingHistory" not in existing_sheets:
            self.spreadsheet.add_worksheet(title="TradingHistory", rows="1000", cols="10")
            sheet = self.spreadsheet.worksheet("TradingHistory")
            headers = [
                "trade_id", "futures_code", "entry_side", "entry_qty", 
                "entry_price", "exit_price", "entry_time", "exit_time", "net_pnl", "fee"
            ]
            sheet.append_row(headers)
            logger.info("Google Sheets: TradingHistory 시트를 생성했습니다.")

        # 3. BotHealth
        if "BotHealth" not in existing_sheets:
            self.spreadsheet.add_worksheet(title="BotHealth", rows="10", cols="5")
            sheet = self.spreadsheet.worksheet("BotHealth")
            headers = ["metric_name", "value", "updated_at"]
            sheet.append_row(headers)
            logger.info("Google Sheets: BotHealth 시트를 생성했습니다.")

    # =========================================================================
    # Active Positions Sync & Recovery
    # =========================================================================
    def update_active_positions(self, positions: List[Dict[str, Any]]):
        """현재 GCE 봇의 활성 포지션을 시트에 완전 동기화 (기존 데이터 Overwrite)"""
        try:
            sheet = self.spreadsheet.worksheet("ActivePositions")
            # 헤더를 제외한 모든 내용 삭제
            sheet.resize(rows=1)
            sheet.resize(rows=100)
            
            if not positions:
                logger.info("Google Sheets: 액티브 포지션이 없어 시트를 비웠습니다.")
                return

            rows_to_write = []
            for pos in positions:
                updated_at_str = pos.get("updated_at")
                if isinstance(updated_at_str, datetime):
                    updated_at_str = updated_at_str.strftime("%Y-%m-%d %H:%M:%S")
                elif not updated_at_str:
                    updated_at_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                rows_to_write.append([
                    str(pos.get("position_id", "")),
                    str(pos.get("futures_code", "")),
                    str(pos.get("market", "")),
                    str(pos.get("side", "")),
                    int(pos.get("quantity", 0)),
                    float(pos.get("avg_price", 0.0)),
                    float(pos.get("stop_loss", 0.0)),
                    float(pos.get("take_profit", 0.0)),
                    float(pos.get("trailing_stop")) if pos.get("trailing_stop") is not None else "",
                    float(pos.get("highest_price")) if pos.get("highest_price") is not None else "",
                    float(pos.get("lowest_price")) if pos.get("lowest_price") is not None else "",
                    str(updated_at_str)
                ])

            # 헤더 바로 아래에 데이터 추가
            sheet.append_rows(rows_to_write)
            logger.info(f"Google Sheets: {len(positions)}개의 액티브 포지션을 동기화했습니다.")
        except Exception as e:
            logger.error(f"Google Sheets ActivePositions 동기화 오류: {e}")

    def get_active_positions(self) -> List[Dict[str, Any]]:
        """Google Sheets에 백업된 활성 포지션 정보를 로드 (Cloud Run 백업 복구용)"""
        try:
            sheet = self.spreadsheet.worksheet("ActivePositions")
            all_records = sheet.get_all_records()
            positions = []
            for record in all_records:
                if not record.get("position_id"):
                    continue
                # 빈 값 처리 및 데이터 타입 변환
                pos = {
                    "position_id": str(record.get("position_id")),
                    "futures_code": str(record.get("futures_code")),
                    "market": str(record.get("market")),
                    "side": str(record.get("side")),
                    "quantity": int(record.get("quantity", 0)),
                    "avg_price": float(record.get("avg_price", 0.0)),
                    "stop_loss": float(record.get("stop_loss", 0.0)),
                    "take_profit": float(record.get("take_profit", 0.0)),
                    "trailing_stop": float(record.get("trailing_stop")) if record.get("trailing_stop") != "" else None,
                    "highest_price": float(record.get("highest_price")) if record.get("highest_price") != "" else None,
                    "lowest_price": float(record.get("lowest_price")) if record.get("lowest_price") != "" else None,
                    "updated_at": str(record.get("updated_at", ""))
                }
                positions.append(pos)
            return positions
        except Exception as e:
            logger.error(f"Google Sheets ActivePositions 로드 실패: {e}")
            return []

    # =========================================================================
    # Trading History Logging
    # =========================================================================
    def append_trade_history(self, trade: Dict[str, Any]):
        """거래 완결 시 Google Sheets 거래 내역 시트의 맨 아래에 기록 추가 (Append)"""
        try:
            sheet = self.spreadsheet.worksheet("TradingHistory")
            
            entry_time = trade.get("entry_time")
            if isinstance(entry_time, datetime):
                entry_time = entry_time.strftime("%Y-%m-%d %H:%M:%S")
                
            exit_time = trade.get("exit_time")
            if isinstance(exit_time, datetime):
                exit_time = exit_time.strftime("%Y-%m-%d %H:%M:%S")

            row_data = [
                str(trade.get("trade_id", "")),
                str(trade.get("futures_code", "")),
                str(trade.get("entry_side", "")),
                int(trade.get("entry_qty", 0)),
                float(trade.get("entry_price", 0.0)),
                float(trade.get("exit_price", 0.0)),
                str(entry_time),
                str(exit_time),
                float(trade.get("net_pnl", 0.0)),
                float(trade.get("fee", 0.0))
            ]
            sheet.append_row(row_data)
            logger.info(f"Google Sheets: 완결 거래 {trade.get('trade_id')} 기록을 누적했습니다.")
        except Exception as e:
            logger.error(f"Google Sheets TradingHistory 기록 실패: {e}")

    # =========================================================================
    # Bot Health check
    # =========================================================================
    def update_bot_health(self, metrics: Dict[str, Any]):
        """GCE 봇의 헬스체크 메트릭 상태 업데이트 (BotHealth 시트 Overwrite)"""
        try:
            sheet = self.spreadsheet.worksheet("BotHealth")
            sheet.resize(rows=1)
            sheet.resize(rows=10)
            
            rows_to_write = []
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            for k, v in metrics.items():
                rows_to_write.append([str(k), str(v), now_str])
                
            sheet.append_rows(rows_to_write)
            logger.info("Google Sheets: 봇 헬스 상태를 업데이트했습니다.")
        except Exception as e:
            logger.error(f"Google Sheets BotHealth 업데이트 실패: {e}")
