"""
future/night_ws_collector.py
==============================
KRX 야간선물 실시간 WebSocket 체결 수신 → 1분봉 조립 → MariaDB 저장

- TR ID: H0MFCNT0 (KRX야간선물 실시간종목체결)
- 종목코드: 101W9000 (코스피200 야간선물), 106F9000 (미니코스피200 야간선물)
- 야간장 운영시간: 평일 18:00 ~ 익일 06:00

실행방법:
  .venv\\Scripts\\python.exe future/night_ws_collector.py

환경변수:
  .env에 KIS_REAL_APP_KEY, KIS_REAL_APP_SECRET 필수
"""

import asyncio
import json
import logging
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional

import requests
import websockets
from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
load_dotenv()

from config import config
from future.store.mariadb_store import MariaDBStore

# ─── 로거 설정 ────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("night_ws_collector.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("NightWSCollector")

# ─── 상수 ────────────────────────────────────────────────────
KRX_NGT_FUTURES_TR = "H0MFCNT0"   # 야간선물 실시간체결
WS_REAL_URL = "ws://ops.koreainvestment.com:21000"  # KIS 실전 WebSocket (kis_devlp.yaml 기준)

# KRX 야간선물 체결 컬럼 순서 (H0MFCNT0)
# krx_ngt_futures_ccnl.py 기준
NGT_CCNL_COLUMNS = [
    "futs_shrn_iscd",       # 0  유선물단축종목코드
    "bsop_hour",            # 1  영업시간
    "futs_prdy_vrss",       # 2  전일대비
    "prdy_vrss_sign",       # 3  전일대비부호
    "futs_prdy_ctrt",       # 4  전일대비율
    "futs_prpr",            # 5  현재가
    "futs_oprc",            # 6  시가
    "futs_hgpr",            # 7  최고가
    "futs_lwpr",            # 8  최저가
    "last_cnqn",            # 9  최근체결수량
    "acml_vol",             # 10 누적거래량
    "acml_tr_pbmn",         # 11 누적거래대금
    "hts_thpr",             # 12 이론가격
    "mrkt_basis",           # 13 시장베이시스
    "dprt",                 # 14 괴리도
    "nmsc_fctn_stpl_prc",   # 15 근월물결제가격
    "fmsc_fctn_stpl_prc",   # 16 원월물결제가격
    "spead_prc",            # 17 스프레드가격
    "hts_otst_stpl_qty",    # 18 미결제약정수량
    "otst_stpl_qty_icdc",   # 19 미결제약정수량증감
    "oprc_hour",            # 20 시가시간
    "oprc_vrss_prpr_sign",  # 21 시가대비현재가부호
    "oprc_vrss_nmix_prpr",  # 22 시가대비현재가
    "hgpr_hour",            # 23 최고가시간
    "hgpr_vrss_prpr_sign",  # 24 최고가대비현재가부호
    "hgpr_vrss_nmix_prpr",  # 25 최고가대비현재가
    "lwpr_hour",            # 26 최저가시간
    "lwpr_vrss_prpr_sign",  # 27 최저가대비현재가부호
    "lwpr_vrss_nmix_prpr",  # 28 최저가대비현재가
    "shnu_rate",            # 29 매수비율
    "cttr",                 # 30 체결강도
    "esdg",                 # 31 괴리율
    "otst_stpl_rgbf_qty_icdc",  # 32 미결제약정직전대비수량
    "thpr_basis",           # 33 이론베이시스
    "futs_askp1",           # 34 매도호가1
    "futs_bidp1",           # 35 매수호가1
    "askp_rsqn1",           # 36 매도잔량1
    "bidp_rsqn1",           # 37 매수잔량1
    "seln_cntg_csnu",       # 38 매도체결건수
    "shnu_cntg_csnu",       # 39 매수체결건수
    "ntby_cntg_csnu",       # 40 순매수체결건수
    "seln_cntg_smtn",       # 41 매도체결합계
    "shnu_cntg_smtn",       # 42 매수체결합계
    "total_askp_rsqn",      # 43 총매도잔량
    "total_bidp_rsqn",      # 44 총매수잔량
    "prdy_vol_vrss_acml_vol_rate",  # 45 전일거래량대비누적거래량비율
    "dynm_mxpr",            # 46 동적상한가
    "dynm_llam",            # 47 동적하한가
    "dynm_prc_limt_yn",     # 48 동적가격제한여부
]

# ─── 구독 대상 야간선물 종목 ───────────────────────────────────
# ★ 실제 운영 중인 월물 코드는 KIS HTS에서 확인 후 수정하세요
# 형식: 101W{YY}{MM}0 or 101W9000(연결)
NIGHT_SYMBOLS = {
    "101W9000": "코스피200_야간연결",
    "106F9000": "미니코스피200_야간연결",
}


# ─── 1분봉 조립기 ─────────────────────────────────────────────
class MinuteBarBuilder:
    """
    실시간 체결 틱 → 1분봉 OHLCV 조립
    분이 바뀌면 완성된 봉을 emit
    """
    def __init__(self):
        # code → { "minute_key": str, "open": float, "high": float, "low": float, "close": float, "volume": int }
        self._bars = {}

    def feed(self, code: str, price: float, volume: int, ts: datetime) -> Optional[dict]:
        """
        틱 데이터를 받아 분봉 조립.
        분이 바뀌면 완성된 봉 dict를 반환, 아직 진행 중이면 None 반환.
        """
        # 분 단위 키 (YYYY-MM-DD HH:MM)
        minute_key = ts.strftime("%Y-%m-%d %H:%M")

        if code not in self._bars:
            self._bars[code] = {
                "minute_key": minute_key,
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "volume": 0,
            }

        bar = self._bars[code]

        if bar["minute_key"] != minute_key:
            # 분이 바뀜 → 완성 봉 반환 후 새 봉 시작
            completed = dict(bar)
            completed["code"] = code
            self._bars[code] = {
                "minute_key": minute_key,
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "volume": volume,
            }
            return completed

        # 같은 분 → 업데이트
        bar["high"]   = max(bar["high"], price)
        bar["low"]    = min(bar["low"],  price)
        bar["close"]  = price
        bar["volume"] += volume
        return None

    def flush_all(self) -> list:
        """강제로 현재 진행 중인 모든 봉을 반환 (프로세스 종료 시)"""
        result = []
        for code, bar in self._bars.items():
            b = dict(bar); b["code"] = code
            result.append(b)
        self._bars.clear()
        return result


# ─── DB 저장 ──────────────────────────────────────────────────
def save_candle(db: MariaDBStore, bar: dict):
    dt_str = bar["minute_key"]  # "YYYY-MM-DD HH:MM"
    candle_time = datetime.strptime(dt_str, "%Y-%m-%d %H:%M")
    candle = [{
        "futures_code":  bar["code"],
        "candle_time":   candle_time,
        "open":          bar["open"],
        "high":          bar["high"],
        "low":           bar["low"],
        "close":         bar["close"],
        "volume":        bar["volume"],
        "open_interest": 0,
        "accum_amount":  None,
    }]
    try:
        db.save_candles(candle)
        logger.info(
            f"[DB] {bar['code']} {dt_str} "
            f"O={bar['open']} H={bar['high']} L={bar['low']} C={bar['close']} V={bar['volume']}"
        )
    except Exception as e:
        logger.error(f"DB 저장 실패: {e}")


# ─── 토큰/승인키 발급 ─────────────────────────────────────────
def get_real_token() -> str:
    base_url = "https://openapi.koreainvestment.com:9443"
    res = requests.post(f"{base_url}/oauth2/tokenP", json={
        "grant_type": "client_credentials",
        "appkey":     config.KIS_REAL_APP_KEY,
        "appsecret":  config.KIS_REAL_APP_SECRET,
    })
    res.raise_for_status()
    return res.json()["access_token"]


def get_approval_key() -> str:
    base_url = "https://openapi.koreainvestment.com:9443"
    res = requests.post(f"{base_url}/oauth2/Approval", json={
        "grant_type": "client_credentials",
        "appkey":     config.KIS_REAL_APP_KEY,
        "secretkey":  config.KIS_REAL_APP_SECRET,
    })
    res.raise_for_status()
    return res.json()["approval_key"]


def make_subscribe_frame(approval_key: str, tr_id: str, code: str) -> str:
    return json.dumps({
        "header": {
            "approval_key": approval_key,
            "custtype":     "P",
            "tr_type":      "1",
            "content-type": "utf-8",
        },
        "body": {
            "input": {
                "tr_id":  tr_id,
                "tr_key": code,
            }
        }
    })


# ─── WebSocket 메인 루프 ──────────────────────────────────────
async def run_collector():
    approval_key = get_approval_key()
    logger.info(f"Approval Key 발급 완료")

    db = MariaDBStore(
        host=os.getenv("MARIADB_HOST", "127.0.0.1"),
        port=int(os.getenv("MARIADB_PORT", 3306)),
        user=os.getenv("MARIADB_USER", "coretel"),
        password=os.getenv("MARIADB_PASSWORD", "coretel1!"),
        database=os.getenv("MARIADB_DATABASE", "kis_trading"),
    )

    builder = MinuteBarBuilder()
    reconnect_delay = 5

    while True:
        try:
            logger.info(f"웹소켓 연결 중: {WS_REAL_URL}")
            async with websockets.connect(
                WS_REAL_URL, ping_interval=30, ping_timeout=10
            ) as ws:
                reconnect_delay = 5  # 연결 성공 시 초기화

                # 야간선물 종목 구독
                for code in NIGHT_SYMBOLS:
                    frame = make_subscribe_frame(approval_key, KRX_NGT_FUTURES_TR, code)
                    await ws.send(frame)
                    logger.info(f"구독 신청: {KRX_NGT_FUTURES_TR} / {code} ({NIGHT_SYMBOLS[code]})")
                    await asyncio.sleep(0.2)

                # 수신 루프
                async for message in ws:
                    if message.startswith("PING"):
                        await ws.send("PONG")
                        continue

                    if message.startswith("{"):
                        data = json.loads(message)
                        if data.get("header", {}).get("tr_id") == "PINGPONG":
                            await ws.send(message)
                        else:
                            logger.debug(f"시스템 응답: {data}")
                        continue

                    # 파이프 구분 체결 데이터 파싱
                    parts = message.split("|")
                    if len(parts) < 4:
                        continue

                    tr_id = parts[1]
                    if tr_id != KRX_NGT_FUTURES_TR:
                        continue

                    data_body = parts[3]
                    fields = data_body.split("^")

                    if len(fields) < len(NGT_CCNL_COLUMNS):
                        logger.warning(f"필드 수 부족: {len(fields)} < {len(NGT_CCNL_COLUMNS)}")
                        continue

                    item = dict(zip(NGT_CCNL_COLUMNS, fields))

                    code       = item["futs_shrn_iscd"]
                    time_str   = item["bsop_hour"]    # HHMMSS
                    price_str  = item["futs_prpr"]
                    vol_str    = item["last_cnqn"]

                    try:
                        price  = float(price_str)
                        volume = int(vol_str) if vol_str else 0
                    except ValueError:
                        continue

                    # 타임스탬프 조립 (한국 시간 기준)
                    now = datetime.now()
                    try:
                        hh = int(time_str[0:2])
                        mm = int(time_str[2:4])
                        ss = int(time_str[4:6])
                        # 자정 이후(00~06시)는 익일 처리 필요할 수 있으나
                        # datetime.now()의 날짜를 그대로 사용
                        ts = now.replace(hour=hh, minute=mm, second=ss, microsecond=0)
                    except Exception:
                        ts = now

                    # 1분봉 조립
                    completed_bar = builder.feed(code, price, volume, ts)
                    if completed_bar:
                        save_candle(db, completed_bar)

                    logger.info(
                        f"[TICK] {code} {time_str} price={price} vol={volume}"
                    )

        except (websockets.exceptions.ConnectionClosed, OSError) as e:
            logger.error(f"웹소켓 끊김: {e}. {reconnect_delay}초 후 재시도")
            # 진행 중이던 봉 플러시
            for bar in builder.flush_all():
                if bar.get("volume", 0) > 0:
                    save_candle(db, bar)
            await asyncio.sleep(reconnect_delay)
            reconnect_delay = min(reconnect_delay * 2, 60)
        except Exception as e:
            logger.error(f"예기치 않은 오류: {e}", exc_info=True)
            await asyncio.sleep(reconnect_delay)


def main():
    logger.info("=" * 60)
    logger.info("KRX 야간선물 WebSocket 수집기 시작")
    logger.info(f"구독 종목: {NIGHT_SYMBOLS}")
    logger.info(f"운영시간: 평일 18:00 ~ 익일 06:00")
    logger.info("=" * 60)

    try:
        asyncio.run(run_collector())
    except KeyboardInterrupt:
        logger.info("사용자 종료 (Ctrl+C)")


if __name__ == "__main__":
    main()
