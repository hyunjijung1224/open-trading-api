# -*- coding: utf-8 -*-
"""
오늘(6월 11일)의 KIS 선물 분봉 조회 및 컬럼명 확인 디버그 스크립트
"""
import os
import sys
import pandas as pd

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "examples_user"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "examples_user", "domestic_futureoption"))

from config import config
import kis_auth as ka

# 설정 및 인증 구성
ka._cfg["paper_app"] = config.KIS_APP_KEY
ka._cfg["paper_sec"] = config.KIS_APP_SECRET
cano = config.KIS_ACCOUNT_NO.split("-")[0]
prod = config.KIS_ACCOUNT_NO.split("-")[1]
ka._cfg["my_paper_future"] = cano
ka._cfg["my_prod"] = prod

ka.auth(svr="vps", product=prod)

from domestic_futureoption_functions import inquire_time_fuopchartprice

target_code = "A05609"
print(f"📡 KIS 분봉 조회 API 호출 시작 | 종목: {target_code}")
try:
    df1, df2 = inquire_time_fuopchartprice(
        fid_cond_mrkt_div_code="F",
        fid_input_iscd=target_code,
        fid_hour_cls_code="60",
        fid_pw_data_incu_yn="Y",
        fid_fake_tick_incu_yn="N",
        fid_input_date_1="20260611",
        fid_input_hour_1="132300"
    )
    
    print("\n[ df1 (요약 데이터) ]")
    print(df1.head() if not df1.empty else "Empty")
    
    print("\n[ df2 (봉 데이터) ]")
    print(df2.head() if not df2.empty else "Empty")
    print(f"총 봉 개수: {len(df2)}")
    print("컬럼 목록:", df2.columns.tolist())
    
except Exception as e:
    print(f"❌ 오류 발생: {e}")
