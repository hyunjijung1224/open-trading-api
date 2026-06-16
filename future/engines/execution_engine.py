import logging
import requests
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from config import config

logger = logging.getLogger("ExecutionEngine")

class ExecutionEngine:
    """
    KIS Open API 연동 선물 주문 및 잔고 조회 집행 엔진 (Execution Engine)
    - KIS 파생상품 잔고조회 API 연동 -> active_positions 로드
    - 지정가 진입 주문 및 긴급 청산 시장가 주문 처리
    - 실전(J) / 모의(V) 서버 분기 및 헤더 조립 자동화
    """
    def __init__(self):
        self.base_url = config.KIS_BASE_URL
        self.app_key = config.KIS_APP_KEY
        self.app_secret = config.KIS_APP_SECRET
        self.account_no = config.KIS_ACCOUNT_NO
        self.access_token: Optional[str] = None
        self.token_expired_at: datetime = datetime.min
        self.real_access_token: Optional[str] = None
        self.real_token_expired_at: datetime = datetime.min
        self.current_capital: float = 100_000_000.0  # 실시간 평가자산 (예수금 + 평가손익)

    def _request_with_retry(self, method: str, url: str, **kwargs) -> requests.Response:
        """KIS API 요청 시 지연/타임아웃에 탄력적으로 대응하기 위한 15초 타임아웃 및 재시도(최대 3회) 래퍼"""
        import time
        max_retries = 3
        kwargs.setdefault("timeout", 15)
        
        for attempt in range(max_retries):
            try:
                if method.upper() == "POST":
                    res = requests.post(url, **kwargs)
                else:
                    res = requests.get(url, **kwargs)
                
                # 1. HTTP 5xx 또는 429 에러 발생 시 (GET 요청만 안전하게 재시도)
                if method.upper() == "GET" and res.status_code in [500, 502, 503, 504, 429]:
                    if attempt < max_retries - 1:
                        logger.warning(f"KIS REST API GET {res.status_code} 오류 발생. 재시도 중... ({attempt + 1}/{max_retries})")
                        time.sleep(1.0)
                        continue
                
                # 2. HTTP 200 이지만 KIS API Gateway 수준의 Rate Limit 거부 발생 시 (GET/POST 공통 재시도 가능)
                if res.status_code == 200:
                    try:
                        data = res.json()
                        msg_cd = data.get("msg_cd")
                        if msg_cd in ["EGW00201", "EGW00133"]:
                            if attempt < max_retries - 1:
                                sleep_time = 2.0 if msg_cd == "EGW00133" else 1.0
                                logger.warning(f"KIS REST API Gateway Rate Limit ({msg_cd}: {data.get('msg1')}) 발생. {sleep_time}초 후 재시도 중... ({attempt + 1}/{max_retries})")
                                time.sleep(sleep_time)
                                continue
                    except Exception:
                        pass
                
                return res
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
                if attempt == max_retries - 1:
                    logger.error(f"KIS REST API 최종 요청 실패 ({url}): {e}")
                    raise e
                logger.warning(f"KIS REST API 요청 지연/오류 발생. 재시도 중... ({attempt + 1}/{max_retries}): {e}")
                time.sleep(1.0)

    async def _ensure_token(self) -> str:
        """REST API 토큰이 유효한지 확인하고 없거나 만료된 경우 재발급"""
        now = datetime.now()
        if not self.access_token or now >= self.token_expired_at:
            url = f"{self.base_url}/oauth2/tokenP"
            payload = {
                "grant_type": "client_credentials",
                "appkey": self.app_key,
                "appsecret": self.app_secret
            }
            res = self._request_with_retry("POST", url, json=payload)
            if res.status_code == 200:
                data = res.json()
                self.access_token = data["access_token"]
                # 만료 시각 설정 (보통 24시간이나 안전하게 23시간으로 제한)
                self.token_expired_at = now + timedelta(hours=23)
                logger.info("KIS REST Access Token 신규 발급 완료.")
            else:
                raise RuntimeError(f"KIS REST Token 발급 오류: {res.text}")
        return self.access_token

    async def _ensure_real_token(self) -> str:
        """실서버 전용 토큰이 유효한지 확인하고 없거나 만료된 경우 재발급"""
        now = datetime.now()
        if not self.real_access_token or now >= self.real_token_expired_at:
            url = "https://openapi.koreainvestment.com:9443/oauth2/tokenP"
            payload = {
                "grant_type": "client_credentials",
                "appkey": config.KIS_REAL_APP_KEY,
                "appsecret": config.KIS_REAL_APP_SECRET
            }
            res = self._request_with_retry("POST", url, json=payload)
            if res.status_code == 200:
                data = res.json()
                self.real_access_token = data["access_token"]
                self.real_token_expired_at = now + timedelta(hours=23)
                logger.info("KIS Real REST Access Token 신규 발급 완료.")
            else:
                raise RuntimeError(f"KIS Real REST Token 발급 오류: {res.text}")
        return self.real_access_token

    def _get_headers(self, tr_id: str, tr_cont: str = "") -> dict:
        """API 호출용 기본 헤더 생성"""
        return {
            "Content-Type": "application/json",
            "authorization": f"Bearer {self.access_token}",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
            "tr_id": tr_id,
            "tr_cont": tr_cont
        }

    def _parse_account(self) -> tuple[str, str]:
        """계좌번호 파싱 (종합계좌번호 8자리, 상품코드 2자리)"""
        parts = self.account_no.split("-")
        if len(parts) == 2:
            return parts[0], parts[1]
        return self.account_no[:8], self.account_no[8:10]

    async def fetch_active_positions(self) -> List[Dict[str, Any]]:
        """KIS API를 호출하여 현재 보유 중인 선물 포지션 잔고 조회"""
        await self._ensure_token()
        cano, prdt_cd = self._parse_account()
        
        # 선물 잔고조회 TR ID: [실전] - CTFO6118R, [모의] - VTFO6118R
        tr_id = "VTFO6118R" if config.KIS_IS_PAPER else "CTFO6118R"
        url = f"{self.base_url}/uapi/domestic-futureoption/v1/trading/inquire-balance"
        
        headers = self._get_headers(tr_id)
        params = {
            "CANO": cano,
            "ACNT_PRDT_CD": prdt_cd,
            "MGNA_DVSN": "01",
            "EXCC_STAT_CD": "1",
            "CTX_AREA_FK200": "",
            "CTX_AREA_NK200": ""
        }
        
        res = self._request_with_retry("GET", url, headers=headers, params=params)
        positions = []
        if res.status_code == 200:
            data = res.json()
            if data.get("rt_cd") == "0":
                # KIS 선물 잔고 조회 응답 필드 파싱
                # VTFO6118R 실제 응답 필드:
                #   cblc_qty: 잔고수량, sll_buy_dvsn_name: "매수"/"매도"
                #   shtn_pdno: 단축종목코드(A05609), pdno: ISIN(KR4A05690008)
                #   ccld_avg_unpr1: 체결평균단가
                outputs = data.get("output1", [])
                for item in outputs:
                    qty = int(item.get("cblc_qty", 0))
                    if qty > 0:
                        # 매수/매도 구분 (텍스트 기반)
                        side_name = item.get("sll_buy_dvsn_name", "").strip()
                        side = "LONG" if side_name == "매수" else "SHORT"
                        # 단축종목코드 사용 (예: A05609 -> 105V09 매핑 가능)
                        code = item.get("shtn_pdno", item.get("pdno", ""))
                        avg_price = float(item.get("ccld_avg_unpr1", 0.0))
                        
                        # 지표 손절 기준선 계산을 위한 임시 초기값 설정
                        positions.append({
                            "position_id": f"P_{code}_{side}",
                            "futures_code": code,
                            "market": "day" if "05" in code else "night",
                            "side": side,
                            "quantity": qty,
                            "avg_price": avg_price,
                            "stop_loss": avg_price * 0.98 if side == "LONG" else avg_price * 1.02, # 실시간 루프에서 덮어씌워짐
                            "take_profit": avg_price * 1.05 if side == "LONG" else avg_price * 0.95,
                            "trailing_stop": None,
                            "highest_price": avg_price,
                            "lowest_price": avg_price,
                            "last_checked_price": avg_price,
                            "updated_at": datetime.now()
                        })
                        logger.info(f"KIS 잔고 파싱: {code} {side} {qty}계약 @ {avg_price:.2f}")
                
                if not positions and outputs:
                    logger.warning(f"KIS 잔고 응답에 {len(outputs)}개 항목이 있으나 파싱된 포지션이 0개입니다. 원본: {outputs}")
                    
                # output2에서 추정예탁금(실시간 평가자산) 파싱 및 동기화
                output2 = data.get("output2", {})
                if isinstance(output2, dict):
                    prsm_dpast = output2.get("prsm_dpast", "")
                    if prsm_dpast:
                        try:
                            self.current_capital = float(prsm_dpast)
                            logger.info(f"실시간 계좌 평가자산 동기화 완료: {self.current_capital:,.2f}원")
                        except ValueError:
                            pass
            else:
                logger.error(f"KIS 잔고 조회 실패 응답: {data.get('msg1')}")
        else:
            logger.error(f"KIS 잔고 조회 HTTP 오류: {res.status_code} {res.text}")
            
        return positions

    async def fetch_futures_price_rest(self, short_code: str) -> Optional[float]:
        """REST API를 통해 선물 현재가를 직접 조회 (웹소켓 틱 미유입 시 폴백용)
        
        Args:
            short_code: 단축코드 (예: A05609)
        
        Returns:
            현재가 float, 실패 시 None
        """
        try:
            await self._ensure_token()
            url = f"{self.base_url}/uapi/domestic-futureoption/v1/quotations/inquire-price"
            headers = self._get_headers("FHMIF10000000")
            params = {
                "FID_COND_MRKT_DIV_CODE": "F",
                "FID_INPUT_ISCD": short_code
            }
            res = self._request_with_retry("GET", url, headers=headers, params=params)
            if res.status_code == 200:
                data = res.json()
                if data.get("rt_cd") == "0":
                    output1 = data.get("output1", {})
                    price_str = output1.get("futs_prpr", "")
                    if price_str:
                        price = float(price_str)
                        logger.debug(f"REST 현재가 조회 성공: {short_code} = {price:.2f}")
                        return price
                else:
                    logger.warning(f"REST 현재가 조회 응답 실패: {data.get('msg1')}")
            else:
                logger.warning(f"REST 현재가 조회 HTTP 오류: {res.status_code} {res.text}")
        except Exception as e:
            logger.debug(f"REST 현재가 조회 중 예외: {e}")
        return None

    async def fetch_stock_price_rest(self, stock_code: str) -> Optional[Dict[str, Any]]:
        """REST API를 통해 주식 현재가(및 예상 체결가 등)를 직접 조회"""
        try:
            await self._ensure_token()
            url = f"{self.base_url}/uapi/domestic-stock/v1/quotations/inquire-price"
            headers = self._get_headers("FHKST01010100")
            params = {
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": stock_code
            }
            res = self._request_with_retry("GET", url, headers=headers, params=params)
            if res.status_code == 200:
                data = res.json()
                if data.get("rt_cd") == "0":
                    output = data.get("output", {})
                    return {
                        "stck_prpr": float(output.get("stck_prpr", 0.0) or 0.0),
                        "stck_sdpr": float(output.get("stck_sdpr", 0.0) or 0.0),
                        "antg_prc": float(output.get("antg_prc", 0.0) or 0.0),
                        "antg_vrss": float(output.get("antg_vrss", 0.0) or 0.0),
                        "antg_prdy_ctrt": float(output.get("antg_prdy_ctrt", 0.0) or 0.0)
                    }
                else:
                    logger.warning(f"REST 주식 현재가 조회 응답 실패: {data.get('msg1')}")
            else:
                logger.warning(f"REST 주식 현재가 조회 HTTP 오류: {res.status_code} {res.text}")
        except Exception as e:
            logger.error(f"REST 주식 현재가 조회 중 예외: {e}")
        return None

    async def fetch_futures_pre_market_price_rest(self, short_code: str) -> Optional[Dict[str, Any]]:
        """REST API를 통해 선물 현재가 및 전일 대비 정보를 조회 (장전용)"""
        try:
            await self._ensure_token()
            url = f"{self.base_url}/uapi/domestic-futureoption/v1/quotations/inquire-price"
            headers = self._get_headers("FHMIF10000000")
            params = {
                "FID_COND_MRKT_DIV_CODE": "F",
                "FID_INPUT_ISCD": short_code
            }
            res = self._request_with_retry("GET", url, headers=headers, params=params)
            if res.status_code == 200:
                data = res.json()
                if data.get("rt_cd") == "0":
                    output1 = data.get("output1", {})
                    futs_prpr = float(output1.get("futs_prpr", 0.0) or 0.0)
                    futs_prdy_vrss = float(output1.get("futs_prdy_vrss", 0.0) or 0.0)
                    # 전일종가 (futs_prdy_clpr 또는 계산값)
                    prev_close = float(output1.get("futs_prdy_clpr", 0.0) or 0.0)
                    if prev_close == 0.0:
                        prev_close = futs_prpr - futs_prdy_vrss
                    if prev_close == 0.0:
                        prev_close = futs_prpr or 1.0
                    return {
                        "futs_prpr": futs_prpr,
                        "prev_close": prev_close,
                        "futs_prdy_vrss": futs_prdy_vrss
                    }
                else:
                    logger.warning(f"REST 선물 시세 조회 실패: {data.get('msg1')}")
            else:
                logger.warning(f"REST 선물 시세 조회 HTTP 오류: {res.status_code} {res.text}")
        except Exception as e:
            logger.error(f"REST 선물 시세 조회 중 예외: {e}")
        return None

    async def fetch_investor_trend(self, code: str) -> Dict[str, Any]:
        """KIS API를 호출하여 시장별 투자자 매매동향(선물) 조회 (실서버 고정 호출)"""
        trend_data = {"foreign": 0, "institution": 0, "individual": 0, "foreign_oi": 0}
        
        if not config.KIS_REAL_APP_KEY or not config.KIS_REAL_APP_SECRET:
            logger.warning("실전투자 KIS_REAL_APP_KEY 또는 KIS_REAL_APP_SECRET 환경변수가 없어 실시간 수급 조회를 건너뜁니다.")
            return trend_data
            
        try:
            await self._ensure_real_token()
            
            # 수급 동향은 실전투자 TR(FHPTJ04030000) 및 실전서버 URL만 지원하므로 실서버 호출 고정
            url = "https://openapi.koreainvestment.com:9443/uapi/domestic-stock/v1/quotations/inquire-investor-time-by-market"
            
            headers = {
                "Content-Type": "application/json",
                "authorization": f"Bearer {self.real_access_token}",
                "appkey": config.KIS_REAL_APP_KEY,
                "appsecret": config.KIS_REAL_APP_SECRET,
                "tr_id": "FHPTJ04030000"
            }
            
            # 코스피 200 미니선물(105)과 일반선물(101)에 따른 시장구분 파라미터 동적 보정
            if code.startswith("105"):
                iscd = "MKI"   # 미니선물
                iscd2 = "F004"
            elif code.startswith("101"):
                iscd = "K2I"   # 코스피200 선물
                iscd2 = "F001"
            else:
                iscd = "MKI"   # 기본값 미니선물
                iscd2 = "F004"
                
            params = {
                "FID_INPUT_ISCD": iscd,
                "FID_INPUT_ISCD_2": iscd2
            }
            
            res = self._request_with_retry("GET", url, headers=headers, params=params)
            
            if res.status_code == 200:
                data = res.json()
                if data.get("rt_cd") == "0":
                    outputs = data.get("output", [])
                    if outputs:
                        latest = outputs[0]
                        trend_data = {
                            "foreign": int(latest.get("frgn_ntby_qty", latest.get("frgn_ntby_vol", 0))),
                            "institution": int(latest.get("orgn_ntby_qty", latest.get("orgn_ntby_vol", 0))),
                            "individual": int(latest.get("prsn_ntby_qty", latest.get("prsn_ntby_vol", 0))),
                            "foreign_oi": 0
                        }
                        logger.info(f"선물 수급 동향 조회 완료: 외인={trend_data['foreign']:+,}계약 기관={trend_data['institution']:+,}계약 개인={trend_data['individual']:+,}계약")
                else:
                    logger.error(f"선물 수급 동향 조회 실패 응답: {data.get('msg1')}")
            else:
                logger.error(f"선물 수급 동향 HTTP 오류: {res.status_code} {res.text}")
        except Exception as e:
            logger.error(f"선물 수급 동향 조회 중 예외 발생: {e}")
            
        return trend_data


    async def fetch_option_trend(self) -> Dict[str, Any]:
        """KIS API를 호출하여 시장별 투자자 매매동향(옵션) 조회 (콜옵션/풋옵션 순매수 계약수)"""
        import asyncio
        trend_data = {"foreign_call_net": 0, "foreign_put_net": 0}
        
        if not config.KIS_REAL_APP_KEY or not config.KIS_REAL_APP_SECRET:
            logger.warning("실전투자 KIS_REAL_APP_KEY 또는 KIS_REAL_APP_SECRET 환경변수가 없어 옵션 수급 조회를 건너뜁니다.")
            return trend_data
            
        try:
            await self._ensure_real_token()
            url = "https://openapi.koreainvestment.com:9443/uapi/domestic-stock/v1/quotations/inquire-investor-time-by-market"
            
            headers = {
                "Content-Type": "application/json",
                "authorization": f"Bearer {self.real_access_token}",
                "appkey": config.KIS_REAL_APP_KEY,
                "appsecret": config.KIS_REAL_APP_SECRET,
                "tr_id": "FHPTJ04030000"
            }
            
            # 1. 콜옵션 조회 (ISCD="OPT", ISCD_2="O001")
            params_call = {
                "FID_INPUT_ISCD": "OPT",
                "FID_INPUT_ISCD_2": "O001"
            }
            res_call = self._request_with_retry("GET", url, headers=headers, params=params_call)
            
            call_net = 0
            if res_call.status_code == 200:
                data = res_call.json()
                if data.get("rt_cd") == "0":
                    outputs = data.get("output", [])
                    if outputs:
                        latest = outputs[0]
                        call_net = int(latest.get("frgn_ntby_qty", latest.get("frgn_ntby_vol", 0)))
                else:
                    logger.error(f"콜옵션 수급 동향 조회 실패 응답: {data.get('msg1')}")
            else:
                logger.error(f"콜옵션 수급 동향 HTTP 오류: {res_call.status_code} {res_call.text}")
                
            # Rate limit 방지를 위해 0.2초 sleep
            await asyncio.sleep(0.2)
            
            # 2. 풋옵션 조회 (ISCD="OPT", ISCD_2="O002")
            params_put = {
                "FID_INPUT_ISCD": "OPT",
                "FID_INPUT_ISCD_2": "O002"
            }
            res_put = self._request_with_retry("GET", url, headers=headers, params=params_put)
            
            put_net = 0
            if res_put.status_code == 200:
                data = res_put.json()
                if data.get("rt_cd") == "0":
                    outputs = data.get("output", [])
                    if outputs:
                        latest = outputs[0]
                        put_net = int(latest.get("frgn_ntby_qty", latest.get("frgn_ntby_vol", 0)))
                else:
                    logger.error(f"풋옵션 수급 동향 조회 실패 응답: {data.get('msg1')}")
            else:
                logger.error(f"풋옵션 수급 동향 HTTP 오류: {res_put.status_code} {res_put.text}")
                
            trend_data = {
                "foreign_call_net": call_net,
                "foreign_put_net": put_net
            }
            logger.info(f"옵션 수급 동향 조회 완료: 외인 콜순매수={call_net:+,} 외인 풋순매수={put_net:+,}")
            
        except Exception as e:
            logger.error(f"옵션 수급 동향 조회 중 예외 발생: {e}")
            
        return trend_data


    async def execute_order(self, code: str, direction: str, qty: int, price: float, 
                            stop_loss: float, take_profit: float) -> Dict[str, Any]:
        """선물 지정가 진입 주문 전송
        
        Args:
            code: 표준코드(105V07) 또는 단축코드(A05607) 모두 허용. 내부에서 단축코드로 변환.
        """
        await self._ensure_token()
        cano, prdt_cd = self._parse_account()
        
        # KIS 선물 주문 API는 단축코드(A05607) 형식 필요
        # 표준코드(105V07) 형식이 들어오면 단축코드로 변환
        short_code = self._to_short_code(code)
        
        # 주문 TR ID: 모의 VTTO1101U, 실전 주간 TTTO1101U, 실전 야간 STTN1101U
        if config.KIS_IS_PAPER:
            tr_id = "VTTO1101U"
        else:
            now_hour = datetime.now().hour
            if now_hour >= 18 or now_hour < 5:
                tr_id = "STTN1101U"
            else:
                tr_id = "TTTO1101U"
            
        url = f"{self.base_url}/uapi/domestic-futureoption/v1/trading/order"
        
        headers = self._get_headers(tr_id)
        # 매수/매도 구분: BUY(02: 매수), SELL(01: 매도)
        side_cd = "02" if direction == "BUY" else "01"
        
        payload = {
            "ORD_PRCS_DVSN_CD": "02",  # 02: 주문전송
            "CANO": cano,
            "ACNT_PRDT_CD": prdt_cd,
            "SLL_BUY_DVSN_CD": side_cd,
            "SHTN_PDNO": short_code,
            "ORD_QTY": str(qty),
            "UNIT_PRICE": f"{price:.2f}",
            "NMPR_TYPE_CD": "02",      # 02: 지정가
            "KRX_NMPR_CNDT_CD": "0",   # 0: 일반
            "ORD_DVSN_CD": "01",       # 01: 개별주문
            "CTAC_TLNO": "",
            "FUOP_ITEM_DVSN_CD": ""
        }
        
        logger.info(f"선물 주문 요청: PDNO={short_code}, 방향={direction}({side_cd}), 수량={qty}, 단가={price:.2f}")
        res = self._request_with_retry("POST", url, json=payload, headers=headers)
        if res.status_code == 200:
            data = res.json()
            if data.get("rt_cd") == "0":
                odno = data.get("output", {}).get("ODNO", "")
                logger.info(f"선물 지정가 주문 성공: 종목={short_code}, 방향={direction}, 수량={qty}, 단가={price:.2f}, 주문번호={odno}")
                return {"success": True, "order_id": odno}
            else:
                logger.error(f"선물 주문 실패 응답: {data.get('msg1')}")
                return {"success": False, "error": data.get("msg1")}
        else:
            logger.error(f"선물 주문 HTTP 오류: {res.status_code} {res.text}")
            return {"success": False, "error": res.text}

    async def market_close_position(self, pos: Dict[str, Any], qty: Optional[int] = None) -> bool:
        """보유 중인 포지션 즉시 시장가 청산 주문 전송 (동시호가 시간대에는 지정가로 우회하여 청산)"""
        await self._ensure_token()
        cano, prdt_cd = self._parse_account()
        
        # 반대 매매 구분: 기존 LONG 포지션 -> SELL(01) 청산, SHORT 포지션 -> BUY(02) 청산
        opp_side = "01" if pos["side"] == "LONG" else "02"
        
        # 청산 주문 수량 결정 (지정 수량이 있으면 해당 수량만큼, 없으면 잔고 전량)
        close_qty = qty if qty is not None else pos["quantity"]
        
        # 청산 주문 TR ID: 모의 VTTO1101U, 실전 주간 TTTO1101U, 실전 야간 STTN1101U
        if config.KIS_IS_PAPER:
            tr_id = "VTTO1101U"
        else:
            now_hour = datetime.now().hour
            if now_hour >= 18 or now_hour < 5:
                tr_id = "STTN1101U"
            else:
                tr_id = "TTTO1101U"
            
        url = f"{self.base_url}/uapi/domestic-futureoption/v1/trading/order"
        
        headers = self._get_headers(tr_id)
        
        # 단축코드 변환 (105V07 -> A05607)
        short_code = self._to_short_code(pos["futures_code"])
        
        # ── 동시호가 시간대 판단 및 시장가 -> 지정가 우회 처리 ──
        from datetime import time as datetime_time
        now = config.get_kst_now()
        now_time = now.time()
        
        def is_final_trading_day(date_val) -> bool:
            import datetime
            first_day = datetime.date(date_val.year, date_val.month, 1)
            first_weekday = first_day.weekday()
            days_to_first_thursday = (3 - first_weekday) % 7
            second_thursday = first_day + datetime.timedelta(days=days_to_first_thursday + 7)
            return date_val.date() == second_thursday

        is_final_day = is_final_trading_day(now)
        call_start = datetime_time(15, 10) if is_final_day else datetime_time(15, 35)
        call_end = datetime_time(15, 20) if is_final_day else datetime_time(15, 45)
        
        is_call_auction = call_start <= now_time < call_end
        price_field = pos.get("last_checked_price") or pos.get("avg_price") or 0.0
        
        if is_call_auction and price_field > 0:
            # 동시호가 시간대: 시장가 전송 시 증거금이 상한가 기준으로 가산되어 거부되는 현상 방지.
            # 직전 체결가 기준 ±1.0포인트 버퍼를 적용한 지정가(01) 주문 전송.
            buffer = 1.0
            limit_price = (price_field + buffer) if opp_side == "02" else (price_field - buffer)
            limit_price = round(limit_price, 2)
            
            payload = {
                "ORD_PRCS_DVSN_CD": "02",  # 02: 주문전송
                "CANO": cano,
                "ACNT_PRDT_CD": prdt_cd,
                "SLL_BUY_DVSN_CD": opp_side,
                "SHTN_PDNO": short_code,
                "ORD_QTY": str(close_qty),
                "UNIT_PRICE": f"{limit_price:.2f}",
                "NMPR_TYPE_CD": "02",      # 02: 호가유형코드
                "KRX_NMPR_CNDT_CD": "0",   # 0: 일반
                "ORD_DVSN_CD": "01",       # 01: 지정가(개별주문)
                "CTAC_TLNO": "",
                "FUOP_ITEM_DVSN_CD": ""
            }
            logger.warning(
                f"[CALL AUCTION] 동시호가 청산 감지 (시간: {now_time.strftime('%H:%M:%S')}). "
                f"증거금 오류 방지를 위해 시장가 대신 지정가(보정가 {limit_price:.2f})로 청산합니다. (기준가: {price_field:.2f}, 수량: {close_qty})"
            )
        else:
            payload = {
                "ORD_PRCS_DVSN_CD": "02",  # 02: 주문전송
                "CANO": cano,
                "ACNT_PRDT_CD": prdt_cd,
                "SLL_BUY_DVSN_CD": opp_side,
                "SHTN_PDNO": short_code,
                "ORD_QTY": str(close_qty),
                "UNIT_PRICE": "0",         # 시장가는 가격 0
                "NMPR_TYPE_CD": "02",      # 02: 호가유형코드
                "KRX_NMPR_CNDT_CD": "0",   # 0: 일반
                "ORD_DVSN_CD": "02",       # 02: 시장가
                "CTAC_TLNO": "",
                "FUOP_ITEM_DVSN_CD": ""
            }
            logger.info(f"시장가 청산 주문 요청: PDNO={short_code}, 수량={pos['quantity']}, 방향={pos['side']} 청산")
            
        res = self._request_with_retry("POST", url, json=payload, headers=headers)
        if res.status_code == 200:
            data = res.json()
            if data.get("rt_cd") == "0":
                logger.info(f"긴급 청산 완료: 종목={short_code}, 수량={pos['quantity']}, 방향={pos['side']} 청산")
                return True
            else:
                logger.error(f"긴급 청산 실패 응답: {data.get('msg1')}")
                return False
        else:
            logger.error(f"긴급 청산 HTTP 오류: {res.status_code} {res.text}")
            return False

    @staticmethod
    def _to_short_code(code: str) -> str:
        """표준코드(105V07) -> KIS 단축코드(A05607) 변환.
        이미 단축코드 형태(A로 시작, 6자리)이면 그대로 반환."""
        if len(code) == 6 and code.startswith("1"):
            prod = code[1:3]      # '05' or '01'
            year_letter = code[3] # 'V'
            month = code[4:]      # '07'
            letter_to_digit = {
                "T": "4", "U": "5", "V": "6", "W": "7", "X": "8", "Y": "9",
                "Z": "0", "A": "1", "B": "2", "C": "3"
            }
            year_digit = letter_to_digit.get(year_letter.upper(), year_letter)
            return f"A{prod}{year_digit}{month}"
        return code  # 이미 단축코드이거나 알 수 없는 형식이면 그대로
