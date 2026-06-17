import json
import logging
import asyncio
import websockets
from datetime import datetime
from typing import Dict, List, Callable, Optional, Any

logger = logging.getLogger("WebSocketManager")

# KIS 웹소켓 지수선물 실시간 컬럼 데이터 정의 (ws_domestic_future.py 기준)
FUTURES_EXECUTION_COLUMNS = [
    "선물단축종목코드", "영업시간", "선물전일대비", "전일대비부호", "선물전일대비율", "선물현재가",
    "선물시가", "선물최고가", "선물최저가", "최종거래량", "누적거래량", "누적거래대금",
    "HTS이론가", "시장베이시스", "괴리율", "근월물약정가", "원월물약정가", "스프레드",
    "미결제약정수량", "미결제약정수량증감", "시가시간", "시가대비현재가부호", "시가대비지수현재가",
    "최고가시간", "최고가대비현재가부호", "최고가대비지수현재가", "최저가시간", "최저가대비현재가부호",
    "최저가대비지수현재가", "매수비율", "체결강도", "괴리도", "미결제약정직전수량증감", "이론베이시스",
    "선물매도호가", "선물매수호가", "매도호가잔량", "매수호가잔량", "매도체결건수", "매수체결건수",
    "순매수체결건수", "총매도수량", "총매수수량", "총매도호가잔량", "총매수호가잔량", "전일거래량대비등락율",
    "협의대량거래량", "실시간상한가", "실시간하한가", "실시간가격제한구분"
]

FUTURES_ORDERBOOK_COLUMNS = [
    "선물단축종목코드", "영업시간",
    "선물매도호가1", "선물매도호가2", "선물매도호가3", "선물매도호가4", "선물매도호가5",
    "선물매수호가1", "선물매수호가2", "선물매수호가3", "선물매수호가4", "선물매수호가5",
    "매도호가건수1", "매도호가건수2", "매도호가건수3", "매도호가건수4", "매도호가건수5",
    "매수호가건수1", "매수호가건수2", "매수호가건수3", "매수호가건수4", "매수호가건수5",
    "매도호가잔량1", "매도호가잔량2", "매도호가잔량3", "매도호가잔량4", "매도호가잔량5",
    "매수호가잔량1", "매수호가잔량2", "매수호가잔량3", "매수호가잔량4", "매수호가잔량5",
    "총매도호가건수", "총매수호가건수", "총매도호가잔량", "총매수호가잔량",
    "총매도호가잔량증감", "총매수호가잔량증감"
]

class WebSocketManager:
    """
    한국투자증권 API 웹소켓 상시 연결 관리 클래스
    - 주간/야간 선물 실시간 데이터 수집
    - 체결통보(내 주문 완료건) 수신
    - 재연결(Keep-Alive/Ping-Pong) 및 예외 처리
    """
    def __init__(self, ws_url: str, app_key: str, app_secret: str, approval_key: str = None, hts_id: str = None):
        self.ws_url = ws_url
        self.app_key = app_key
        self.app_secret = app_secret
        self.approval_key = approval_key
        self.hts_id = hts_id or "urbanist"
        
        self.ws: Optional[websockets.ClientConnection] = None
        self.is_running = False
        
        # 실시간 데이터 콜백 함수 등록용
        self.on_execution_callback: Optional[Callable[[Dict[str, Any]], None]] = None
        self.on_orderbook_callback: Optional[Callable[[Dict[str, Any]], None]] = None
        self.on_my_order_callback: Optional[Callable[[Dict[str, Any]], None]] = None
        
        # 현재 구독 목록 {"tr_id": [종목코드1, 종목코드2]}
        self.subscriptions: Dict[str, List[str]] = {}
        
        # 최신 시세 데이터 보관 (메인 스레드 폴링용)
        self.latest_data: Dict[str, Dict[str, Any]] = {}

    async def connect(self):
        """웹소켓 서버 연결 및 메인 리스너 태스크 가동"""
        if not self.approval_key:
            # 실시간 접속용 approval_key 발급 (REST API 사전 획득)
            self.approval_key = await self._fetch_approval_key()
            
        self.is_running = True
        asyncio.create_task(self._main_loop())

    async def _fetch_approval_key(self) -> str:
        """KIS REST API를 통해 웹소켓 접속용 approval_key 획득"""
        import requests
        from config import config
        base_url = config.KIS_BASE_URL
        api_url = f"{base_url}/oauth2/Approval"
        payload = {
            "grant_type": "client_credentials",
            "appkey": self.app_key,
            "secretkey": self.app_secret
        }
        headers = {"content-type": "application/json"}
        
        res = requests.post(api_url, json=payload, headers=headers)
        if res.status_code == 200:
            return res.json()["approval_key"]
        else:
            raise RuntimeError(f"Approval Key 획득 실패: {res.text}")

    async def _main_loop(self):
        """자동 재연결 루프가 도는 메인 웹소켓 커넥션 유지 루프"""
        while self.is_running:
            try:
                logger.info(f"웹소켓 연결 시도: {self.ws_url}")
                async with websockets.connect(self.ws_url, ping_interval=30, ping_timeout=10) as ws:
                    self.ws = ws
                    logger.info("웹소켓 서버와 정상적으로 연결되었습니다.")
                    
                    # 연결 유실 후 재접속 시 기존 구독 내역 재등록
                    await self._resubscribe_all()
                    
                    # 수신 대기 루프
                    async for message in ws:
                        await self._handle_message(message)
                        
            except (websockets.exceptions.ConnectionClosed, Exception) as e:
                logger.error(f"웹소켓 연결 끊김 혹은 예외 발생: {e}")
                self.ws = None
                await asyncio.sleep(5)  # 5초 후 재시도

    @property
    def is_connected(self) -> bool:
        """웹소켓이 현재 연결되어 있고 열려 있는지 여부 반환 (websockets v14+ 호환)"""
        if not self.ws:
            return False
        if hasattr(self.ws, "open"):
            return self.ws.open
        if hasattr(self.ws, "state"):
            return self.ws.state.name == "OPEN"
        return False

    async def subscribe(self, tr_id: str, code: str):
        """특정 TR ID와 종목코드로 실시간 데이터 구독 신청"""
        if tr_id not in self.subscriptions:
            self.subscriptions[tr_id] = []
        if code not in self.subscriptions[tr_id]:
            self.subscriptions[tr_id].append(code)
            
        if self.is_connected:
            await self._send_subscription(tr_id, code, is_subscribe=True)

    async def unsubscribe(self, tr_id: str, code: str):
        """구독 해지 신청"""
        if tr_id in self.subscriptions and code in self.subscriptions[tr_id]:
            self.subscriptions[tr_id].remove(code)
            if self.is_connected:
                await self._send_subscription(tr_id, code, is_subscribe=False)

    async def _send_subscription(self, tr_id: str, code: str, is_subscribe: bool = True):
        """구독/해지 JSON 프레임 전송"""
        tr_type = "1" if is_subscribe else "2"
        # 체결통보(CNI0, CNI9)의 tr_key는 HTS ID가 필수입니다.
        tr_key = code if tr_id not in ["H0STCNI0", "H0STCNI9", "H0IFCNI0", "H0IFCNI9"] else (self.hts_id or "urbanist")
        
        payload = {
            "header": {
                "approval_key": self.approval_key,
                "custtype": "P",
                "tr_type": tr_type,
                "content-type": "utf-8"
            },
            "body": {
                "input": {
                    "tr_id": tr_id,
                    "tr_key": tr_key
                }
            }
        }
        await self.ws.send(json.dumps(payload))
        logger.info(f"구독 프레임 송신: tr_id={tr_id}, tr_key={tr_key}, subscribe={is_subscribe}")
        await asyncio.sleep(0.1)  # KIS Rate Limit 준수

    async def _resubscribe_all(self):
        """연결 유실 재접속 후 모든 구독 데이터 재신청"""
        for tr_id, codes in self.subscriptions.items():
            for code in codes:
                await self._send_subscription(tr_id, code, is_subscribe=True)

    async def _handle_message(self, message: str):
        """수신된 원바이트 메세지 파싱 및 콜백 라우팅"""
        try:
            # 1. PINGPONG 처리
            if message.startswith("PING"):
                await self.ws.send("PONG")
                return
                
            # 2. 시스템/결과 데이터 응답 처리 (JSON 형태)
            if message.startswith("{"):
                data = json.loads(message)
                # PINGPONG JSON 래핑인 경우 처리
                if data.get("header", {}).get("tr_id") == "PINGPONG":
                    await self.ws.send(message)
                    return
                # 일반 시스템 응답 로그 기록
                logger.info(f"시스템 수신 메시지: {data}")
                return

            # 3. 실시간 시세 데이터 처리 (구분자 '|' 포맷)
            parts = message.split("|")
            if len(parts) >= 4:
                # header info
                # parts[0]: 수신구분 (0: 암호화 안됨, 1: 암호화됨)
                # parts[1]: TR ID
                # parts[2]: 수신건수
                # parts[3]: 실제 데이터 본문 (구분자 '^')
                tr_id = parts[1]
                data_body = parts[3]
                
                # 실시간 체결 (H0IFCNT0)
                if tr_id == "H0IFCNT0":
                    fields = data_body.split("^")
                    if len(fields) >= len(FUTURES_EXECUTION_COLUMNS):
                        item = dict(zip(FUTURES_EXECUTION_COLUMNS, fields))
                        code = item["선물단축종목코드"]
                        
                        # 지표 변환 및 보관
                        parsed_data = {
                            "code": code,
                            "price": float(item["선물현재가"]),
                            "volume": int(item["누적거래량"]),
                            "open_interest": int(item["미결제약정수량"]),
                            "high": float(item["선물최고가"]),
                            "low": float(item["선물최저가"]),
                            "open": float(item["선물시가"]),
                            "time": item["영업시간"],
                            "timestamp": datetime.now(),
                            # --- Order Flow / CVD 필드 ---
                            "last_volume": int(item["최종거래량"]),
                            "total_buy_vol": int(item["총매수수량"]),
                            "total_sell_vol": int(item["총매도수량"]),
                            "buy_ratio": float(item["매수비율"]),
                            "exec_strength": float(item["체결강도"]),
                            "net_buy_count": int(item["순매수체결건수"]),
                            "ask_price": float(item["선물매도호가"]),
                            "bid_price": float(item["선물매수호가"]),
                            "ask_remain": int(item["매도호가잔량"]),
                            "bid_remain": int(item["매수호가잔량"]),
                        }
                        self.latest_data[code] = parsed_data
                        
                        # 실시간 체결 콜백 트리거 (손절용)
                        if self.on_execution_callback:
                            self.on_execution_callback(parsed_data)
                            
                # 실시간 호가 (H0IFASP0)
                elif tr_id == "H0IFASP0":
                    fields = data_body.split("^")
                    if len(fields) >= len(FUTURES_ORDERBOOK_COLUMNS):
                        item = dict(zip(FUTURES_ORDERBOOK_COLUMNS, fields))
                        code = item["선물단축종목코드"]
                        parsed_data = {
                            "code": code,
                            "ask": float(item["선물매도호가1"]),
                            "bid": float(item["선물매수호가1"]),
                            "ask_vol": int(item["매도호가잔량1"]),
                            "bid_vol": int(item["매수호가잔량1"]),
                            "total_ask_vol": int(item["총매도호가잔량"]),
                            "total_bid_vol": int(item["총매수호가잔량"]),
                            "time": item["영업시간"],
                            "timestamp": datetime.now()
                        }
                        # 호가 정보 콜백 트리거 (스프레드 감시용)
                        if self.on_orderbook_callback:
                            self.on_orderbook_callback(parsed_data)
                            
                # 실시간 나의 주문 체결통보 (H0STCNI0/H0IFCNI0: 실전, H0STCNI9/H0IFCNI9: 모의)
                elif tr_id in ["H0STCNI0", "H0STCNI9", "H0IFCNI0", "H0IFCNI9"]:
                    # 체결통보 포맷 파싱 (구분자 '^')
                    fields = data_body.split("^")
                    # 체결 통보는 암호화되어 전송되는 경우도 있으나, 복호화 로직은 base64/aes로 대입 가능
                    # KIS websocket sample에 따르면, TR_ID가 CNI0/CNI9 일 때 평문 형태 파싱 대응
                    parsed_data = {
                        "tr_id": tr_id,
                        "raw_fields": fields,
                        "timestamp": datetime.now()
                    }
                    if self.on_my_order_callback:
                        self.on_my_order_callback(parsed_data)

        except Exception as e:
            logger.error(f"메시지 처리 실패: {e}, message={message[:100]}")

    def get_latest_price(self, code: str) -> Optional[float]:
        """특정 종목의 메모리 상 최신 선물현재가 조회"""
        data = self.latest_data.get(code)
        return data["price"] if data else None

    def close(self):
        """웹소켓 세션 닫기"""
        self.is_running = False
        if self.ws:
            asyncio.create_task(self.ws.close())
            logger.info("WebSocketManager 자원을 닫았습니다.")
