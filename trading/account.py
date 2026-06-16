# -*- coding: utf-8 -*-
"""
AccountManager — 잔고 및 계좌 조회 모듈

[포함 기능]
- get_balance()         : 보유 종목 잔고 조회 (output1: 종목별, output2: 계좌 총평가)
- get_account_summary() : 투자계좌 자산현황 조회 (총자산, 예수금 등 요약)
- print_balance()       : 잔고 현황 콘솔 출력 (디버깅/확인용)
"""

import logging
import os
import sys
from typing import Optional, Tuple

import pandas as pd

# kis_auth 경로 설정 (examples_user 기준)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "examples_user"))
import kis_auth as ka

logger = logging.getLogger(__name__)


class AccountManager:
    """
    KIS 국내주식 계좌/잔고 조회 클래스

    Usage:
        import kis_auth as ka
        ka.auth(svr="vps", product="01")  # 모의투자

        from trading.account import AccountManager
        acct = AccountManager()
        df_stocks, df_summary = acct.get_balance()
    """

    # API URL 상수
    _URL_BALANCE = "/uapi/domestic-stock/v1/trading/inquire-balance"
    _URL_ACCOUNT = "/uapi/domestic-stock/v1/trading/inquire-account-balance"

    def __init__(self):
        env = ka.getTREnv()
        self.cano = env.my_acct  # 계좌번호 앞 8자리
        self.acnt_prdt_cd = env.my_prod  # 계좌번호 뒤 2자리
        self.env_dv = "demo" if ka.isPaperTrading() else "real"

    # ──────────────────────────────────────────────
    # 1. 보유 종목 잔고 조회
    # ──────────────────────────────────────────────
    def get_balance(
        self,
        FK100: str = "",
        NK100: str = "",
        tr_cont: str = "",
        dataframe1: Optional[pd.DataFrame] = None,
        dataframe2: Optional[pd.DataFrame] = None,
        depth: int = 0,
        max_depth: int = 10,
    ) -> Tuple[pd.DataFrame, pd.DataFrame]:
        """
        보유 종목 잔고 조회 (연속조회 포함)

        Returns:
            df_stocks  (DataFrame): 보유 종목별 상세 (종목코드, 종목명, 수량, 평균단가, 현재가, 평가손익, 수익률 등)
            df_summary (DataFrame): 계좌 총평가 (총평가금액, 총매입금액, 총손익, 예수금 등)
        """
        if depth > max_depth:
            logger.warning("연속조회 최대 횟수 초과")
            return dataframe1 or pd.DataFrame(), dataframe2 or pd.DataFrame()

        tr_id = "VTTC8434R" if self.env_dv == "demo" else "TTTC8434R"

        params = {
            "CANO": self.cano,
            "ACNT_PRDT_CD": self.acnt_prdt_cd,
            "AFHR_FLPR_YN": "N",  # 시간외단일가 미포함
            "OFL_YN": "",
            "INQR_DVSN": "02",  # 종목별 조회
            "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N",
            "PRCS_DVSN": "00",  # 전일매매포함
            "CTX_AREA_FK100": FK100,
            "CTX_AREA_NK100": NK100,
        }

        res = ka._url_fetch(self._URL_BALANCE, tr_id, tr_cont, params)

        if not res.isOK():
            res.printError(url=self._URL_BALANCE)
            return pd.DataFrame(), pd.DataFrame()

        # output1: 종목별 잔고
        cur1 = pd.DataFrame(res.getBody().output1)
        dataframe1 = (
            pd.concat([dataframe1, cur1], ignore_index=True)
            if dataframe1 is not None
            else cur1
        )

        # output2: 계좌 총평가 (단일 row)
        cur2 = pd.DataFrame(res.getBody().output2)
        dataframe2 = (
            pd.concat([dataframe2, cur2], ignore_index=True)
            if dataframe2 is not None
            else cur2
        )

        # 연속조회 처리
        next_tr_cont = res.getHeader().tr_cont
        if next_tr_cont in ["M", "F"]:
            logger.info(f"잔고 연속조회 중... (depth={depth + 1})")
            ka.smart_sleep()
            return self.get_balance(
                FK100=res.getBody().ctx_area_fk100,
                NK100=res.getBody().ctx_area_nk100,
                tr_cont="N",
                dataframe1=dataframe1,
                dataframe2=dataframe2,
                depth=depth + 1,
                max_depth=max_depth,
            )

        logger.info("잔고 조회 완료")
        return dataframe1, dataframe2

    # ──────────────────────────────────────────────
    # 2. 투자계좌 자산현황 조회
    # ──────────────────────────────────────────────
    def get_account_summary(self) -> Tuple[pd.DataFrame, pd.DataFrame]:
        """
        투자계좌 자산현황 조회 (HTS [0891] 화면 기준)

        Returns:
            df1 (DataFrame): 자산구성 상세 (주식, 펀드, ETF 등 비중)
            df2 (DataFrame): 총자산 요약 (총자산금액, 순자산금액 등)
        """
        tr_id = "CTRP6548R"

        params = {
            "CANO": self.cano,
            "ACNT_PRDT_CD": self.acnt_prdt_cd,
            "INQR_DVSN_1": "",
            "BSPR_BF_DT_APLY_YN": "",
        }

        res = ka._url_fetch(self._URL_ACCOUNT, tr_id, "", params)

        if not res.isOK():
            res.printError(url=self._URL_ACCOUNT)
            return pd.DataFrame(), pd.DataFrame()

        df1 = pd.DataFrame(res.getBody().output1)
        df2 = pd.DataFrame([res.getBody().output2])
        logger.info("계좌 자산현황 조회 완료")
        return df1, df2

    # ──────────────────────────────────────────────
    # 3. 잔고 현황 출력 (확인용)
    # ──────────────────────────────────────────────
    def print_balance(self):
        """
        잔고 현황을 콘솔에 보기 좋게 출력

        출력 항목:
            - 보유 종목: 종목명, 수량, 평균단가, 현재가, 평가손익, 수익률
            - 계좌 요약: 총평가금액, 총매입금액, 총손익, 총수익률, 예수금
        """
        df_stocks, df_summary = self.get_balance()

        print("\n" + "=" * 60)
        print(
            f"  📊 보유 종목 현황  [{self.env_dv.upper()}]  계좌: {self.cano}-{self.acnt_prdt_cd}"
        )
        print("=" * 60)

        if df_stocks.empty:
            print("  보유 종목 없음")
        else:
            # 보유수량 > 0 인 종목만 표시
            cols_map = {
                "pdno": "종목코드",
                "prdt_name": "종목명",
                "hldg_qty": "보유수량",
                "pchs_avg_pric": "평균단가",
                "prpr": "현재가",
                "evlu_pfls_amt": "평가손익",
                "evlu_pfls_rt": "수익률(%)",
                "evlu_amt": "평가금액",
            }
            available = {k: v for k, v in cols_map.items() if k in df_stocks.columns}
            df_view = df_stocks[list(available.keys())].rename(columns=available)
            df_view = df_view[df_view["보유수량"].astype(float) > 0]

            if df_view.empty:
                print("  보유 종목 없음 (수량 0)")
            else:
                # 숫자 포맷 적용
                for col in ["평균단가", "현재가", "평가손익", "평가금액"]:
                    if col in df_view.columns:
                        df_view[col] = df_view[col].apply(
                            lambda x: (
                                f"{int(float(x)):,}" if x not in ["", None] else "-"
                            )
                        )
                for col in ["수익률(%)"]:
                    if col in df_view.columns:
                        df_view[col] = df_view[col].apply(
                            lambda x: f"{float(x):.2f}%" if x not in ["", None] else "-"
                        )
                print(df_view.to_string(index=False))

        # 계좌 요약 출력
        print("\n" + "-" * 60)
        print("  💰 계좌 총평가")
        print("-" * 60)
        if not df_summary.empty:
            row = df_summary.iloc[-1]  # 마지막 행 (합계)
            fields = {
                "tot_evlu_amt": ("총평가금액", True),
                "pchs_amt_smtl_amt": ("총매입금액", True),
                "evlu_pfls_smtl_amt": ("총평가손익", True),
                "asst_icdc_erng_rt": ("총수익률", False),
                "dnca_tot_amt": ("예수금", True),
            }
            for key, (label, is_int) in fields.items():
                if key in row.index and row[key] not in ["", None]:
                    val = row[key]
                    if is_int:
                        print(f"  {label:<12}: {int(float(val)):>15,} 원")
                    else:
                        print(f"  {label:<12}: {float(val):>14.2f} %")
        print("=" * 60 + "\n")
