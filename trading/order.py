# -*- coding: utf-8 -*-
"""
OrderManager — 매수 / 매도 / 정정 / 취소 주문 모듈

[주문구분 코드 (ord_dvsn)]
    "00" : 지정가
    "01" : 시장가
    "05" : 조건부지정가
    "06" : 최유리지정가
    "07" : 최우선지정가

[포함 기능]
- buy()         : 매수 주문 (지정가 / 시장가)
- sell()        : 매도 주문 (지정가 / 시장가)
- cancel()      : 주문 취소
- modify()      : 주문 정정 (가격 변경)
- get_orders()  : 미체결 주문 조회
"""

import logging
import os
import sys
from dataclasses import dataclass
from typing import Optional

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "examples_user"))
import kis_auth as ka

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# 주문 결과 데이터클래스
# ──────────────────────────────────────────────
@dataclass
class OrderResult:
    success: bool  # 주문 성공 여부
    order_no: str  # 주문번호 (odno)
    org_no: str  # 거래소전송 주문조직번호 (krx_fwdg_ord_orgno) — 취소/정정 시 필요
    stock_code: str  # 종목코드
    order_type: str  # 주문유형 (buy/sell/cancel/modify)
    qty: int  # 주문수량
    price: int  # 주문단가 (시장가=0)
    message: str  # API 응답 메시지
    raw: Optional[pd.DataFrame] = None  # 원본 응답 DataFrame

    def __str__(self):
        env = "모의" if ka.isPaperTrading() else "실전"
        type_map = {"buy": "매수", "sell": "매도", "cancel": "취소", "modify": "정정"}
        label = type_map.get(self.order_type, self.order_type)
        status = "✅ 성공" if self.success else "❌ 실패"
        price_str = "시장가" if self.price == 0 else f"{self.price:,}원"
        return (
            f"[{env}] {label} {status} | "
            f"종목: {self.stock_code} | 수량: {self.qty:,}주 | 가격: {price_str} | "
            f"주문번호: {self.order_no} | {self.message}"
        )


class OrderManager:
    """
    KIS 국내주식 주문 관리 클래스

    Usage:
        import kis_auth as ka
        ka.auth(svr="vps", product="01")  # 모의투자

        from trading.order import OrderManager
        om = OrderManager()

        # 삼성전자 1주 시장가 매수
        result = om.buy("005930", qty=1)
        print(result)

        # 삼성전자 1주 지정가 매수
        result = om.buy("005930", qty=1, price=70000)
        print(result)

        # 매도
        result = om.sell("005930", qty=1)
        print(result)

        # 주문 취소
        result = om.cancel(order_no="0000123456", org_no="91011")
        print(result)
    """

    _URL_ORDER = "/uapi/domestic-stock/v1/trading/order-cash"
    _URL_RVSECNCL = "/uapi/domestic-stock/v1/trading/order-rvsecncl"
    _URL_INQUIRE = "/uapi/domestic-stock/v1/trading/inquire-psbl-rvsecncl"

    def __init__(self):
        env = ka.getTREnv()
        self.cano = env.my_acct
        self.acnt_prdt_cd = env.my_prod
        self.env_dv = "demo" if ka.isPaperTrading() else "real"
        self.is_paper = ka.isPaperTrading()

    # ──────────────────────────────────────────────
    # 내부 헬퍼: tr_id 결정
    # ──────────────────────────────────────────────
    def _get_order_tr_id(self, ord_dv: str) -> str:
        """매수/매도 tr_id 반환"""
        table = {
            ("real", "buy"): "TTTC0012U",
            ("real", "sell"): "TTTC0011U",
            ("demo", "buy"): "VTTC0012U",
            ("demo", "sell"): "VTTC0011U",
        }
        key = (self.env_dv, ord_dv)
        if key not in table:
            raise ValueError(f"잘못된 주문 유형: env_dv={self.env_dv}, ord_dv={ord_dv}")
        return table[key]

    def _get_rvsecncl_tr_id(self) -> str:
        """정정/취소 tr_id 반환"""
        return "VTTC0013U" if self.is_paper else "TTTC0013U"

    # ──────────────────────────────────────────────
    # 내부 헬퍼: 실제 API 호출 (매수/매도 공통)
    # ──────────────────────────────────────────────
    def _place_order(
        self,
        ord_dv: str,  # "buy" or "sell"
        stock_code: str,  # 종목코드 6자리
        qty: int,  # 주문수량
        price: int = 0,  # 주문단가 (0 = 시장가)
        ord_dvsn: str = "",  # 주문구분 (미지정 시 자동 결정)
    ) -> OrderResult:

        # 주문구분 자동 결정
        if ord_dvsn == "":
            ord_dvsn = "01" if price == 0 else "00"  # 시장가 / 지정가

        tr_id = self._get_order_tr_id(ord_dv)

        params = {
            "CANO": self.cano,
            "ACNT_PRDT_CD": self.acnt_prdt_cd,
            "PDNO": stock_code,
            "ORD_DVSN": ord_dvsn,
            "ORD_QTY": str(qty),
            "ORD_UNPR": str(price),
            "EXCG_ID_DVSN_CD": "KRX",
            "SLL_TYPE": "01" if ord_dv == "sell" else "",
            "CNDT_PRIC": "",
        }

        res = ka._url_fetch(self._URL_ORDER, tr_id, "", params, postFlag=True)

        if res.isOK():
            body = res.getBody().output
            # body가 dict인 경우와 namedtuple인 경우 모두 처리
            if isinstance(body, dict):
                logger.debug(f"주문응답 body keys: {list(body.keys())}")
                ord_no = body.get("odno", body.get("ODNO", ""))
                org_no = body.get(
                    "krx_fwdg_ord_orgno", body.get("KRX_FWDG_ORD_ORGNO", "")
                )
            else:
                logger.debug(
                    f"주문응답 body fields: {body._fields if hasattr(body, '_fields') else dir(body)}"
                )
                ord_no = getattr(body, "odno", getattr(body, "ODNO", ""))
                org_no = getattr(
                    body, "krx_fwdg_ord_orgno", getattr(body, "KRX_FWDG_ORD_ORGNO", "")
                )
            msg = getattr(res.getBody(), "msg1", "주문 완료")
            logger.info(f"[{ord_dv.upper()}] {stock_code} {qty}주 → 주문번호: {ord_no}")
            return OrderResult(
                success=True,
                order_no=ord_no,
                org_no=org_no,
                stock_code=stock_code,
                order_type=ord_dv,
                qty=qty,
                price=price,
                message=msg,
                raw=pd.DataFrame([body if isinstance(body, dict) else body._asdict()])
                if body
                else None,
            )
        else:
            msg = res.getErrorMessage()
            logger.error(f"[{ord_dv.upper()}] {stock_code} 주문 실패: {msg}")
            res.printError(url=self._URL_ORDER)
            return OrderResult(
                success=False,
                order_no="",
                org_no="",
                stock_code=stock_code,
                order_type=ord_dv,
                qty=qty,
                price=price,
                message=msg,
            )

    # ──────────────────────────────────────────────
    # 1. 매수
    # ──────────────────────────────────────────────
    def buy(
        self,
        stock_code: str,
        qty: int,
        price: int = 0,
        ord_dvsn: str = "",
    ) -> OrderResult:
        """
        매수 주문

        Args:
            stock_code (str): 종목코드 6자리 (ex. "005930")
            qty        (int): 주문수량
            price      (int): 주문단가. 0이면 시장가 주문 (default: 0)
            ord_dvsn   (str): 주문구분. 미지정 시 price=0→"01"(시장가), price>0→"00"(지정가) 자동 결정

        Returns:
            OrderResult
        """
        logger.info(
            f"매수 주문 요청 | {stock_code} {qty}주 {'시장가' if price == 0 else f'{price:,}원'}"
        )
        return self._place_order("buy", stock_code, qty, price, ord_dvsn)

    # ──────────────────────────────────────────────
    # 2. 매도
    # ──────────────────────────────────────────────
    def sell(
        self,
        stock_code: str,
        qty: int,
        price: int = 0,
        ord_dvsn: str = "",
    ) -> OrderResult:
        """
        매도 주문

        Args:
            stock_code (str): 종목코드 6자리 (ex. "005930")
            qty        (int): 주문수량
            price      (int): 주문단가. 0이면 시장가 주문 (default: 0)
            ord_dvsn   (str): 주문구분. 미지정 시 자동 결정

        Returns:
            OrderResult
        """
        logger.info(
            f"매도 주문 요청 | {stock_code} {qty}주 {'시장가' if price == 0 else f'{price:,}원'}"
        )
        return self._place_order("sell", stock_code, qty, price, ord_dvsn)

    # ──────────────────────────────────────────────
    # 3. 주문 취소
    # ──────────────────────────────────────────────
    def cancel(
        self,
        order_no: str,
        org_no: str,
        qty: int = 0,
        stock_code: str = "",
    ) -> OrderResult:
        """
        주문 취소 (미체결 주문에 한함)

        Args:
            order_no   (str): 주문번호 (odno) — buy()/sell() 반환값의 order_no
            org_no     (str): 거래소전송 주문조직번호 (krx_fwdg_ord_orgno) — buy()/sell() 반환값의 org_no
            qty        (int): 취소 수량. 0이면 전량 취소 (default: 0)
            stock_code (str): 종목코드 (로깅용, 생략 가능)

        Returns:
            OrderResult
        """
        qty_all = "Y" if qty == 0 else "N"
        tr_id = self._get_rvsecncl_tr_id()

        params = {
            "CANO": self.cano,
            "ACNT_PRDT_CD": self.acnt_prdt_cd,
            "KRX_FWDG_ORD_ORGNO": org_no,
            "ORGN_ODNO": order_no,
            "ORD_DVSN": "00",
            "RVSE_CNCL_DVSN_CD": "02",  # 02 = 취소
            "ORD_QTY": str(qty) if qty > 0 else "0",
            "ORD_UNPR": "0",
            "QTY_ALL_ORD_YN": qty_all,
            "EXCG_ID_DVSN_CD": "KRX",
        }

        res = ka._url_fetch(self._URL_RVSECNCL, tr_id, "", params, postFlag=True)

        if res.isOK():
            body = res.getBody().output
            new_no = getattr(body, "odno", "")
            msg = "취소 완료"
            logger.info(f"[CANCEL] 원주문번호: {order_no} → 취소주문번호: {new_no}")
            return OrderResult(
                success=True,
                order_no=new_no,
                org_no=org_no,
                stock_code=stock_code,
                order_type="cancel",
                qty=qty,
                price=0,
                message=msg,
            )
        else:
            msg = res.getErrorMessage()
            logger.error(f"[CANCEL] 주문취소 실패 (원주문번호: {order_no}): {msg}")
            res.printError(url=self._URL_RVSECNCL)
            return OrderResult(
                success=False,
                order_no="",
                org_no=org_no,
                stock_code=stock_code,
                order_type="cancel",
                qty=qty,
                price=0,
                message=msg,
            )

    # ──────────────────────────────────────────────
    # 4. 주문 정정 (가격 변경)
    # ──────────────────────────────────────────────
    def modify(
        self,
        order_no: str,
        org_no: str,
        new_price: int,
        qty: int = 0,
        ord_dvsn: str = "00",
        stock_code: str = "",
    ) -> OrderResult:
        """
        주문 정정 (미체결 주문의 가격 변경)

        Args:
            order_no  (str): 원주문번호
            org_no    (str): 거래소전송 주문조직번호
            new_price (int): 변경할 새 주문단가
            qty       (int): 정정 수량. 0이면 전량 정정 (default: 0)
            ord_dvsn  (str): 주문구분 (default: "00" 지정가)
            stock_code(str): 종목코드 (로깅용)

        Returns:
            OrderResult
        """
        qty_all = "Y" if qty == 0 else "N"
        tr_id = self._get_rvsecncl_tr_id()

        params = {
            "CANO": self.cano,
            "ACNT_PRDT_CD": self.acnt_prdt_cd,
            "KRX_FWDG_ORD_ORGNO": org_no,
            "ORGN_ODNO": order_no,
            "ORD_DVSN": ord_dvsn,
            "RVSE_CNCL_DVSN_CD": "01",  # 01 = 정정
            "ORD_QTY": str(qty) if qty > 0 else "0",
            "ORD_UNPR": str(new_price),
            "QTY_ALL_ORD_YN": qty_all,
            "EXCG_ID_DVSN_CD": "KRX",
        }

        res = ka._url_fetch(self._URL_RVSECNCL, tr_id, "", params, postFlag=True)

        if res.isOK():
            body = res.getBody().output
            new_no = getattr(body, "odno", "")
            msg = f"정정 완료 → {new_price:,}원"
            logger.info(
                f"[MODIFY] 원주문번호: {order_no} → 정정주문번호: {new_no} ({new_price:,}원)"
            )
            return OrderResult(
                success=True,
                order_no=new_no,
                org_no=org_no,
                stock_code=stock_code,
                order_type="modify",
                qty=qty,
                price=new_price,
                message=msg,
            )
        else:
            msg = res.getErrorMessage()
            logger.error(f"[MODIFY] 주문정정 실패 (원주문번호: {order_no}): {msg}")
            res.printError(url=self._URL_RVSECNCL)
            return OrderResult(
                success=False,
                order_no="",
                org_no=org_no,
                stock_code=stock_code,
                order_type="modify",
                qty=qty,
                price=new_price,
                message=msg,
            )

    # ──────────────────────────────────────────────
    # 5. 미체결 주문 조회
    # ──────────────────────────────────────────────
    def get_orders(self) -> pd.DataFrame:
        """
        정정/취소 가능한 미체결 주문 조회

        Returns:
            DataFrame: 미체결 주문 목록
                       (주문번호, 종목코드, 종목명, 주문구분, 주문수량, 미체결수량, 주문단가 등)
        """
        # 미체결 주문 조회는 모의투자에서 미지원 → 체결내역으로 대체 안내
        if self.is_paper:
            logger.warning(
                "미체결 주문 조회(inquire-psbl-rvsecncl)는 모의투자 미지원 API입니다."
            )
            return pd.DataFrame()

        tr_id = "TTTC0084R"

        params = {
            "CANO": self.cano,
            "ACNT_PRDT_CD": self.acnt_prdt_cd,
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
            "INQR_DVSN_1": "",
            "INQR_DVSN_2": "",
        }

        res = ka._url_fetch(self._URL_INQUIRE, tr_id, "", params)

        if res.isOK():
            df = pd.DataFrame(res.getBody().output)
            logger.info(f"미체결 주문 {len(df)}건 조회 완료")
            return df
        else:
            res.printError(url=self._URL_INQUIRE)
            return pd.DataFrame()

    def print_orders(self):
        """미체결 주문 현황 콘솔 출력"""
        df = self.get_orders()
        print("\n" + "=" * 60)
        print(f"  📋 미체결 주문 현황  [{self.env_dv.upper()}]")
        print("=" * 60)

        if self.is_paper:
            print("  ⚠️  모의투자는 미체결 주문 조회 미지원 (KIS API 정책)")
        elif df.empty:
            print("  미체결 주문 없음")
        else:
            cols_map = {
                "odno": "주문번호",
                "pdno": "종목코드",
                "prdt_name": "종목명",
                "sll_buy_dvsn_cd_name": "매수매도",
                "ord_qty": "주문수량",
                "rmn_qty": "미체결수량",
                "ord_unpr": "주문단가",
                "ord_tmd": "주문시각",
            }
            available = {k: v for k, v in cols_map.items() if k in df.columns}
            df_view = df[list(available.keys())].rename(columns=available)
            print(df_view.to_string(index=False))
        print("=" * 60 + "\n")
