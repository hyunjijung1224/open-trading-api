import os
import sys
import argparse
import logging
from datetime import datetime
from dotenv import load_dotenv

# 루트 경로를 sys.path에 수동 삽입하여 config 및 store 임포트 활성화
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from future.store.mariadb_store import MariaDBStore

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("DataCollector")

# .env 명시적 로드
load_dotenv()

def collect_from_daishin(code: str, chart_type: str, interval: int = 1):
    """
    대신증권 CYBOS Plus API를 사용하여 시계열 시세 분봉 데이터를 수집하고 MariaDB에 벌크 적재
    - code: 대신증권 단축코드 (예: 지수선물연결 '10100', 미니선물연결 '10500')
    - chart_type: 'm' (분봉), 'D' (일봉)
    - interval: 분봉 주기 (기본 1분)
    """
    try:
        import win32com.client
    except ImportError:
        logger.error(
            "win32com 모듈을 임포트할 수 없습니다. \n"
            "대신증권 API는 Windows OS와 32비트(x86) Python 환경이 필수적입니다.\n"
            "64비트 파이썬 가상환경에서 동작 중일 경우 COM 호출이 거부될 수 있습니다."
        )
        return

    # 1. CYBOS Plus 연결 체크
    try:
        cybos_util = win32com.client.Dispatch("CpUtil.CpCybos")
        if not cybos_util.IsConnect:
            logger.error(
                "❌ 대신증권 CYBOS Plus가 실행되어 있지 않거나 로그인 상태가 아닙니다.\n"
                "대신증권 HTS(CYBOS 5 또는 크레온 HTS) 로그인 창에서 'CYBOS Plus' 모드로 로그인해 주세요."
            )
            return
    except Exception as e:
        logger.error(f"대신증권 COM 컴포넌트 로드 실패: {e}")
        return

    logger.info(f"대신증권 CYBOS Plus 연결 확인 완료. 데이터 수집을 준비합니다. (종목: {code})")

    # 2. MariaDB 연결 초기화
    try:
        db = MariaDBStore(
            host=os.getenv("MARIADB_HOST", "127.0.0.1"),
            port=int(os.getenv("MARIADB_PORT", 3306)),
            user=os.getenv("MARIADB_USER", "kis_user"),
            password=os.getenv("MARIADB_PASSWORD", "kis_password"),
            database=os.getenv("MARIADB_DATABASE", "kis_trading")
        )
    except Exception as e:
        logger.error(f"MariaDB 데이터베이스 연결 오류: {e}")
        return

    # 3. 대신증권 FutOptChart API 설정 및 루프 수집
    # CpSysDib.FutOptChart 컴포넌트 생성
    chart = win32com.client.Dispatch("CpSysDib.FutOptChart")
    
    # 입력 인자 설정
    chart.SetInputValue(0, code)             # 0: 종목코드
    chart.SetInputValue(1, ord('2'))         # 1: 조회 방식 (1: 기간, 2: 개수)
    chart.SetInputValue(4, 9999)             # 4: 요청 개수 (최대 9999)
    # 5: 요청 필드 (0:날짜, 1:시간, 2:시가, 3:고가, 4:저가, 5:종가, 8:거래량, 10:미결제약정)
    chart.SetInputValue(5, [0, 1, 2, 3, 4, 5, 8, 10])
    chart.SetInputValue(6, ord(chart_type))  # 6: 차트 구분 (m: 분, D: 일)
    chart.SetInputValue(7, interval)         # 7: 주기 (1분)
    chart.SetInputValue(8, ord('0'))         # 8: 갭보정 여부 (0: 무보정)
    chart.SetInputValue(9, ord('1'))         # 9: 수정 주가 적용 여부 (1: 적용)

    total_inserted = 0
    page_count = 1

    logger.info("과거 시세 페이징 요청 시작 (최대 한도 도달 시까지 반복)...")

    while True:
        # KIS / 대신증권 호출 제한 방어용 딜레이 (CYBOS Plus는 15초당 60회 이내 권장)
        import time
        time.sleep(0.25)
        
        # BlockRequest()로 데이터 송수신
        chart.BlockRequest()
        
        status = chart.GetDibStatus()
        msg = chart.GetDibMsg1()
        if status != 0:
            logger.error(f"API 통신 에러 (상태코드: {status}): {msg}")
            break

        # 수신된 행 개수 확인
        rows = chart.GetHeaderValue(3)
        if rows <= 0:
            logger.info("더 이상 수신할 과거 데이터가 존재하지 않습니다. 수집 종료.")
            break

        candles = []
        for i in range(rows):
            try:
                # 수신 데이터 파싱
                date = int(chart.GetDataValue(0, i))  # YYYYMMDD
                time_val = int(chart.GetDataValue(1, i))  # HHMM
                
                # 날짜 및 시간 문자열 가공
                date_str = str(date)
                time_str = f"{time_val:04d}"
                
                try:
                    dt_obj = datetime.strptime(f"{date_str} {time_str}", "%Y%m%d %H%M")
                except Exception as e:
                    if i == 0:
                        logger.warning(f"datetime.strptime 파싱 실패 (날짜: {date_str}, 시간: {time_str}): {e}")
                    continue
                    
                open_val = float(chart.GetDataValue(2, i))
                high_val = float(chart.GetDataValue(3, i))
                low_val = float(chart.GetDataValue(4, i))
                close_val = float(chart.GetDataValue(5, i))
                volume = int(chart.GetDataValue(6, i))
                oi = int(chart.GetDataValue(7, i))

                candles.append({
                    "futures_code": code,
                    "candle_time": dt_obj,
                    "open": open_val,
                    "high": high_val,
                    "low": low_val,
                    "close": close_val,
                    "volume": volume,
                    "open_interest": oi,
                    "accum_amount": None  # 대신증권 StockChart 필드 생략 가능
                })
            except Exception as e:
                if i == 0:
                    logger.warning(f"데이터 로우 {i} 파싱 실패: {e}")
                continue

        if not candles:
            try:
                raw_date = chart.GetDataValue(0, 0)
                raw_time = chart.GetDataValue(1, 0)
            except Exception as e:
                raw_date, raw_time = f"Error: {e}", "Error"
            logger.warning(f"수신된 데이터 {rows}개 중 파싱에 성공한 데이터가 없습니다. (첫 행 raw 값: 날짜={raw_date}, 시간={raw_time})")
            break

        # DB 벌크 적재 실행 (executemany)
        db.save_candles(candles)
        total_inserted += len(candles)
        
        logger.info(f"[페이지 {page_count}] {len(candles)}개 데이터 적재 완료. (누적: {total_inserted}개, 가장 과거 시점: {candles[-1]['candle_time']})")
        
        # 연속 조회 체크
        if not chart.Continue:
            logger.info("대신증권 서버 연속 조회(Continue) 플래그가 비활성화되었습니다. 전체 수집 완료.")
            break
            
        page_count += 1

    db.close()
    logger.info(f"🎉 최종 수집이 완료되었습니다! 총 {total_inserted}개의 분봉 시세 데이터가 MariaDB에 완벽히 이식되었습니다.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="대신증권 CYBOS Plus 선물 과거 1분봉 데이터 수집기")
    # 10500: 대신증권 코스피 200 미니선물 연결 단축코드
    # 10100: 대신증권 코스피 200 선물 연결 단축코드
    parser.add_argument("--code", type=str, default="10500", help="대신증권 단축 종목코드 (기본: 10500 미니선물연결)")
    parser.add_argument("--type", type=str, default="m", choices=["m", "D"], help="차트 주기 구분 (m: 분봉, D: 일봉)")
    parser.add_argument("--interval", type=int, default=1, help="분봉 주기 간격 (기본: 1분)")
    
    args = parser.parse_args()
    
    collect_from_daishin(
        code=args.code,
        chart_type=args.type,
        interval=args.interval
    )
