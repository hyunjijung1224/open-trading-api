# -*- coding: utf-8 -*-
"""
어제 15:45 이후부터 현재까지의 미니선물(105V09) 1분봉 데이터를 KIS API로부터 수집하여
MariaDB market_candles 테이블에 적재하는 원타임 스크립트.
"""
import os
import sys
import pandas as pd
from datetime import datetime, timedelta

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "examples_user"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "examples_user", "domestic_futureoption"))

from config import config
import kis_auth as ka
from future.store.mariadb_store import MariaDBStore

# 1. KIS 인증 설정
ka._cfg["paper_app"] = config.KIS_APP_KEY
ka._cfg["paper_sec"] = config.KIS_APP_SECRET
cano = config.KIS_ACCOUNT_NO.split("-")[0]
prod = config.KIS_ACCOUNT_NO.split("-")[1]
ka._cfg["my_paper_future"] = cano
ka._cfg["my_prod"] = prod

ka.auth(svr="vps", product=prod)

from domestic_futureoption_functions import inquire_time_fuopchartprice

# 2. MariaDB 연결
db = MariaDBStore(
    host=os.getenv("MARIADB_HOST", "127.0.0.1"),
    port=int(os.getenv("MARIADB_PORT", 3306)),
    user=os.getenv("MARIADB_USER", "kis_user"),
    password=os.getenv("MARIADB_PASSWORD", "kis_password"),
    database=os.getenv("MARIADB_DATABASE", "kis_trading")
)

# KIS 분봉 조회용 단축코드와 DB 적재용 시스템 코드 설정
kis_code = "A05609"       # KIS API가 분봉 조회 시 요구하는 포맷
db_code = "105V09"        # 시스템(supervisor)에서 실시간으로 사용하는 포맷
target_datetime_limit = datetime(2026, 6, 10, 15, 45, 0) # 어제 15:45 기준선

print(f"🚀 KIS REST API에서 {kis_code} 종목의 분봉 데이터를 조회하여 DB({db_code})에 적재를 시작합니다.")
print(f"   기준 시점: {target_datetime_limit.strftime('%Y-%m-%d %H:%M:%S')} 이후")

collected_candles = []
current_date = datetime.now().strftime("%Y%m%d")
current_hour = datetime.now().strftime("%H%M%S")

# Pagination 루프
loop_count = 0
max_loops = 5
has_more = True

while has_more and loop_count < max_loops:
    loop_count += 1
    print(f"   [루프 {loop_count}] 조회 파라미터 - 날짜: {current_date}, 시간: {current_hour}")
    
    try:
        # F: 지수선물, 60: 1분봉, Y: 과거데이터포함, N: 허봉제외
        _, df = inquire_time_fuopchartprice(
            fid_cond_mrkt_div_code="F",
            fid_input_iscd=kis_code,
            fid_hour_cls_code="60",
            fid_pw_data_incu_yn="Y",
            fid_fake_tick_incu_yn="N",
            fid_input_date_1=current_date,
            fid_input_hour_1=current_hour
        )
        
        if df.empty:
            print("   조회된 데이터가 없습니다. 루프를 종료합니다.")
            break
            
        print(f"   조회 완료: {len(df)}개 봉 수신.")
        
        # 데이터 정렬 및 변환
        # KIS 응답 df의 인덱스 0이 가장 최신 봉, 뒤로 갈수록 과거 봉임
        for _, row in df.iterrows():
            date_str = str(row["stck_bsop_date"]).strip()
            time_str = str(row["stck_cntg_hour"]).strip().zfill(6) # 6자리 패딩 (HHMMSS)
            
            # candle_time 파싱
            try:
                candle_dt = datetime.strptime(f"{date_str} {time_str}", "%Y%m%d %H%M%S")
            except ValueError:
                continue
                
            # 기준 시점(어제 15:45) 이전의 데이터에 도달하면 루프 종료 조건 설정
            if candle_dt <= target_datetime_limit:
                has_more = False
                continue
                
            candle = {
                "futures_code": db_code,
                "candle_time": candle_dt.strftime("%Y-%m-%d %H:%M:%S"),
                "open": float(row["futs_oprc"]),
                "high": float(row["futs_hgpr"]),
                "low": float(row["futs_lwpr"]),
                "close": float(row["futs_prpr"]),
                "volume": int(row["cntg_vol"]),
                "open_interest": 0, # 분봉별 미결제약정은 없으므로 0
                "accum_amount": float(row.get("acml_tr_pbmn", 0.0))
            }
            collected_candles.append(candle)
            
        # 다음 페이지 조회를 위해 가장 오래된 봉의 날짜/시간으로 갱신
        # 1분 전 시점으로 요청해야 중복 조회를 피함
        last_row = df.iloc[-1]
        last_date = str(last_row["stck_bsop_date"]).strip()
        last_hour = str(last_row["stck_cntg_hour"]).strip().zfill(6)
        last_dt = datetime.strptime(f"{last_date} {last_hour}", "%Y%m%d %H%M%S")
        
        next_dt = last_dt - timedelta(minutes=1)
        current_date = next_dt.strftime("%Y%m%d")
        current_hour = next_dt.strftime("%H%M%S")
        
        # 만약 가장 오래된 데이터의 시간조차 타겟 제한시간보다 이전이면 더 이상 루프 불필요
        if last_dt <= target_datetime_limit:
            has_more = False
            
    except Exception as e:
        print(f"   ❌ 데이터 조회 중 에러 발생: {e}")
        break

# 중복 제거 및 DB 저장
if collected_candles:
    # candle_time 기준으로 유니크하게 정렬
    unique_candles = {}
    for c in collected_candles:
        unique_candles[c["candle_time"]] = c
    
    final_list = list(unique_candles.values())
    # 오름차순 정렬
    final_list.sort(key=lambda x: x["candle_time"])
    
    print(f"\n📊 필터링 완료: 총 {len(final_list)}개의 유니크한 1분봉 데이터가 정렬되었습니다.")
    print(f"   가장 과거 봉: {final_list[0]['candle_time']} | 가격: {final_list[0]['close']:.2f}")
    print(f"   가장 최근 봉: {final_list[-1]['candle_time']} | 가격: {final_list[-1]['close']:.2f}")
    
    try:
        # DB 벌크 적재 실행
        db.save_candles(final_list)
        print("✅ MariaDB 적재가 정상 완료되었습니다!")
    except Exception as e:
        print(f"❌ DB 적재 실패: {e}")
else:
    print("\n⚠️ 적재할 1분봉 데이터가 존재하지 않습니다.")

db.close()
