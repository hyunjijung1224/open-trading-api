"""
future/kiwoom_csv_loader.py
============================
키움 브리지가 생성한 CSV를 읽어 MariaDB에 Upsert하는 64비트 스크립트

실행 방법:
  .venv\Scripts\python.exe future/kiwoom_csv_loader.py
"""

import os
import sys
import csv
import logging
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv()

from future.store.mariadb_store import MariaDBStore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("KiwoomCSVLoader")

# 키움 브리지가 저장한 CSV 경로
CSV_PATH = os.path.join(os.path.dirname(__file__), "kiwoom_night_candles.csv")


def load_csv_to_db(csv_path: str):
    if not os.path.exists(csv_path):
        logger.error(f"CSV 파일 없음: {csv_path}")
        logger.error("먼저 32비트 Python으로 kiwoom_bridge.py를 실행하세요:")
        logger.error("  C:\\Python39-32\\python.exe future/kiwoom_bridge.py")
        return

    db = MariaDBStore(
        host=os.getenv("MARIADB_HOST", "127.0.0.1"),
        port=int(os.getenv("MARIADB_PORT", 3306)),
        user=os.getenv("MARIADB_USER", "coretel"),
        password=os.getenv("MARIADB_PASSWORD", "coretel1!"),
        database=os.getenv("MARIADB_DATABASE", "kis_trading")
    )

    candles = []
    skipped = 0

    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                dt = datetime.strptime(row["datetime"], "%Y-%m-%d %H:%M:%S")
                open_v  = float(row["open"]  or 0)
                high_v  = float(row["high"]  or 0)
                low_v   = float(row["low"]   or 0)
                close_v = float(row["close"] or 0)
                vol     = int(row["volume"]  or 0)

                if close_v <= 0:
                    skipped += 1
                    continue

                candles.append({
                    "futures_code":  row.get("symbol", "10500"),
                    "candle_time":   dt,
                    "open":          open_v,
                    "high":          high_v,
                    "low":           low_v,
                    "close":         close_v,
                    "volume":        vol,
                    "open_interest": 0,
                    "accum_amount":  None,
                })
            except Exception as e:
                logger.warning(f"행 파싱 실패: {row} / {e}")
                skipped += 1

    logger.info(f"CSV 로드: {len(candles)}건 유효 / {skipped}건 스킵")

    if candles:
        # 배치 단위로 적재
        batch_size = 5000
        total = 0
        for i in range(0, len(candles), batch_size):
            batch = candles[i:i + batch_size]
            db.save_candles(batch)
            total += len(batch)
            logger.info(f"  {total}/{len(candles)}건 적재 완료")

        logger.info(f"✅ MariaDB Upsert 완료: 총 {len(candles)}건")
    else:
        logger.warning("적재할 데이터 없음")

    db.close()


if __name__ == "__main__":
    load_csv_to_db(CSV_PATH)
