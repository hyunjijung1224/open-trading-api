"""
future/kiwoom_bridge.py
========================
32비트 Python 전용 키움 OpenAPI+ 야간선물 분봉 수집기

실행 방법:
  C:\Python39-32\python.exe future/kiwoom_bridge.py

동작:
  1. 키움 OpenAPI+ 로그인
  2. 야간선물(미니코스피200) 분봉 조회 (opt50028)
  3. 결과를 MariaDB에 직접 upsert 또는 CSV 파일로 저장
"""

import sys
import os
import time
import logging
from datetime import datetime, timedelta

# ── 32비트 체크 ──────────────────────────────────────────────
if sys.maxsize > 2**32:
    print("ERROR: 이 스크립트는 반드시 32비트 Python으로 실행해야 합니다.")
    print("  C:\\Python39-32\\python.exe future/kiwoom_bridge.py")
    sys.exit(1)

try:
    import win32com.client
    import pythoncom
except ImportError:
    print("ERROR: pywin32 미설치. 다음 명령으로 설치하세요:")
    print("  C:\\Python39-32\\python.exe -m pip install pywin32")
    sys.exit(1)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("kiwoom_bridge.log", encoding="utf-8")
    ]
)
logger = logging.getLogger("KiwoomBridge")

# 결과 저장 CSV 경로 (64비트 메인 프로세스가 읽어갈 수 있는 경로)
OUTPUT_CSV = os.path.join(os.path.dirname(__file__), "kiwoom_night_candles.csv")


class KiwoomBridge:
    """키움 OpenAPI+ COM 오브젝트 래퍼 (32비트 전용)"""

    def __init__(self):
        self.kiwoom = win32com.client.Dispatch("KHOPENAPI.KHOpenAPICtrl.1")
        self._event_connected = False
        self._tr_data = {}       # TR 응답 저장소
        self._tr_complete = {}   # TR 완료 플래그

    # ─── 이벤트 핸들러 ───────────────────────────────────────

    def OnEventConnect(self, errCode):
        if errCode == 0:
            logger.info("키움 로그인 성공")
            self._event_connected = True
        else:
            logger.error(f"키움 로그인 실패: errCode={errCode}")
            self._event_connected = False

    def OnReceiveTrData(self, screenNo, rqName, trCode, recordName, prevNext,
                        dataLen, errorCode, message, splmMsg):
        """TR 데이터 수신 이벤트"""
        logger.info(f"TR 수신: {trCode} / {rqName} / prevNext={prevNext}")

        rows = []
        repeat_cnt = self.kiwoom.GetRepeatCnt(trCode, recordName)

        for i in range(repeat_cnt):
            row = {
                "date":   self.kiwoom.GetCommData(trCode, recordName, i, "체결시간").strip(),
                "open":   self.kiwoom.GetCommData(trCode, recordName, i, "시가").strip(),
                "high":   self.kiwoom.GetCommData(trCode, recordName, i, "고가").strip(),
                "low":    self.kiwoom.GetCommData(trCode, recordName, i, "저가").strip(),
                "close":  self.kiwoom.GetCommData(trCode, recordName, i, "현재가").strip(),
                "volume": self.kiwoom.GetCommData(trCode, recordName, i, "거래량").strip(),
            }
            rows.append(row)

        self._tr_data[rqName] = {
            "rows": rows,
            "prevNext": prevNext
        }
        self._tr_complete[rqName] = True

    def OnReceiveMsg(self, screenNo, rqName, trCode, msg):
        logger.info(f"서버 메시지: [{trCode}] {msg}")

    # ─── 공통 메서드 ─────────────────────────────────────────

    def login(self, timeout: int = 60) -> bool:
        """로그인 팝업 표시 및 완료 대기"""
        self.kiwoom.CommConnect()
        elapsed = 0
        while not self._event_connected and elapsed < timeout:
            pythoncom.PumpWaitingMessages()
            time.sleep(0.1)
            elapsed += 0.1
        return self._event_connected

    def _wait_tr(self, rqName: str, timeout: int = 10) -> bool:
        """TR 응답 대기"""
        self._tr_complete[rqName] = False
        elapsed = 0
        while not self._tr_complete.get(rqName, False) and elapsed < timeout:
            pythoncom.PumpWaitingMessages()
            time.sleep(0.1)
            elapsed += 0.1
        return self._tr_complete.get(rqName, False)

    def get_future_minute_bars(
        self,
        code: str,
        interval: int = 1,
        days_limit: int = 30
    ) -> list:
        """
        opt50028: 선물/옵션 분봉 조회
        - code: 종목코드 (예: '10500' 미니코스피연결, 또는 야간 종목코드)
        - interval: 분 단위 (1, 3, 5, 10, 15, 30, 60)
        - days_limit: 수집 기간 (일)
        """
        all_rows = []
        screen_no = "5001"
        rq_name = "opt50028_req"
        tr_code = "opt50028"
        limit_date = (datetime.now() - timedelta(days=days_limit)).strftime("%Y%m%d%H%M%S")

        # 최초 조회
        self.kiwoom.SetInputValue("종목코드", code)
        self.kiwoom.SetInputValue("틱범위", str(interval))   # 분 단위
        self.kiwoom.SetInputValue("수정주가구분", "1")
        ret = self.kiwoom.CommRqData(rq_name, tr_code, 0, screen_no)
        if ret != 0:
            logger.error(f"CommRqData 실패: ret={ret}")
            return []

        page = 1
        while True:
            if not self._wait_tr(rq_name):
                logger.error("TR 응답 타임아웃")
                break

            result = self._tr_data.get(rq_name, {})
            rows = result.get("rows", [])
            prevNext = result.get("prevNext", "0")

            if not rows:
                logger.info("데이터 없음. 수집 종료.")
                break

            for row in rows:
                all_rows.append(row)

            logger.info(f"[페이지 {page}] {len(rows)}건 수신 (누적 {len(all_rows)}건)")

            # 마지막 레코드 날짜가 limit_date 이전이면 종료
            last_date = rows[-1].get("date", "")
            if last_date and last_date < limit_date:
                logger.info(f"한계 날짜({limit_date}) 도달. 종료.")
                break

            # 다음 페이지 여부
            if prevNext != "2":
                logger.info("마지막 페이지. 수집 완료.")
                break

            # 다음 페이지 조회 (연속 조회)
            time.sleep(0.5)  # 키움 API 호출 제한 (초당 5회)
            self.kiwoom.SetInputValue("종목코드", code)
            self.kiwoom.SetInputValue("틱범위", str(interval))
            self.kiwoom.SetInputValue("수정주가구분", "1")
            ret = self.kiwoom.CommRqData(rq_name, tr_code, 2, screen_no)
            if ret != 0:
                logger.error(f"연속 조회 CommRqData 실패: ret={ret}")
                break
            page += 1

        return all_rows

    def get_login_info(self, tag: str) -> str:
        return self.kiwoom.GetLoginInfo(tag)


def save_to_csv(rows: list, symbol: str, path: str):
    """수집 결과를 CSV로 저장 (64비트 프로세스가 읽어갈 수 있도록)"""
    import csv

    night_rows = []
    for r in rows:
        date_str = r.get("date", "")
        if len(date_str) >= 14:
            time_part = int(date_str[8:10])  # HH
            # 야간 세션: 18~24시, 00~06시
            if time_part >= 18 or time_part <= 6:
                night_rows.append(r)

    logger.info(f"전체 {len(rows)}건 중 야간 {len(night_rows)}건 필터링")

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["symbol", "datetime", "open", "high", "low", "close", "volume"])
        writer.writeheader()
        for r in night_rows:
            date_str = r.get("date", "")
            if len(date_str) >= 14:
                try:
                    dt = datetime.strptime(date_str[:14], "%Y%m%d%H%M%S")
                    writer.writerow({
                        "symbol":   symbol,
                        "datetime": dt.strftime("%Y-%m-%d %H:%M:%S"),
                        "open":     r.get("open", "0").lstrip("+").lstrip("-") or "0",
                        "high":     r.get("high", "0").lstrip("+").lstrip("-") or "0",
                        "low":      r.get("low",  "0").lstrip("+").lstrip("-") or "0",
                        "close":    r.get("close","0").lstrip("+").lstrip("-") or "0",
                        "volume":   r.get("volume","0").strip() or "0",
                    })
                except Exception as e:
                    logger.warning(f"날짜 파싱 실패: {date_str} / {e}")

    logger.info(f"CSV 저장 완료: {path}")


def main():
    """
    메인 실행 흐름
    
    ★ 종목코드 안내:
      - 10500  : 미니코스피200 연결선물 (주간 데이터만 가능할 수 있음)
      - 야간선물 코드는 키움 HTS > [0212] 종목코드조회에서 확인
        예: '10600' 또는 특정 월물 코드 (106F6000 형식)
    """
    # COM 초기화
    pythoncom.CoInitialize()

    bridge = KiwoomBridge()

    # 이벤트 핸들러 연결
    win32com.client.WithEvents(bridge.kiwoom, bridge)

    logger.info("키움 OpenAPI+ 로그인 시도...")
    if not bridge.login(timeout=60):
        logger.error("로그인 실패. 종료.")
        return

    acc = bridge.get_login_info("ACCNO")
    logger.info(f"계좌번호: {acc}")

    # ★ 여기서 야간선물 종목코드를 지정하세요
    # 키움 HTS [0212]에서 '미니코스피야간' 검색하여 코드 확인
    NIGHT_FUTURE_CODE = "10500"  # TODO: 야간선물 실제 코드로 변경 필요
    INTERVAL = 1       # 1분봉
    DAYS = 30          # 최근 30일

    logger.info(f"분봉 수집 시작: 종목={NIGHT_FUTURE_CODE}, {INTERVAL}분봉, {DAYS}일치")
    rows = bridge.get_future_minute_bars(
        code=NIGHT_FUTURE_CODE,
        interval=INTERVAL,
        days_limit=DAYS
    )

    if rows:
        save_to_csv(rows, symbol="10500", path=OUTPUT_CSV)
        logger.info(f"✅ 완료! {len(rows)}건 수집 → {OUTPUT_CSV}")
    else:
        logger.warning("수집된 데이터 없음")

    pythoncom.CoUninitialize()


if __name__ == "__main__":
    main()
