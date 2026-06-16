# -*- coding: utf-8 -*-
"""
KIS 모의투자 인증 연결 테스트
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "examples_user"))

import kis_auth as ka

print("=" * 55)
print("  KIS 모의투자 인증 연결 테스트")
print("=" * 55)

# 1. 경로 확인
config_path = os.path.normpath(ka.config_root)
yaml_path = os.path.join(config_path, "kis_devlp.yaml")
token_path = os.path.normpath(ka.token_tmp)

print(f"\n[1] config_root : {config_path}")
print(f"    yaml 존재    : {os.path.exists(yaml_path)}")
print(f"    token 파일   : {token_path}")

# 2. 기존 저장 토큰 확인
saved = ka.read_token()
print(f"\n[2] 저장된 토큰  : {'있음 (재사용)' if saved else '없음 (신규 발급 필요)'}")

# 3. 모의투자 인증
print("\n[3] 모의투자 인증 시도 중 (svr=vps, product=01)...")
ka.auth(svr="vps", product="01")

# 4. 인증 결과 확인
env = ka.getTREnv()
token = ka.read_token()  # 파일에서 직접 읽어 재확인

print(f"\n[4] 인증 결과")
print(f"    서버 URL     : {env.my_url}")
print(f"    계좌번호     : {env.my_acct}-{env.my_prod}")
print(f"    HTS ID       : {env.my_htsid}")
print(f"    토큰 발급    : {'성공 ✅' if token else '실패 ❌'}")
if token:
    print(f"    토큰(앞30자) : {token[:30]}...")

# 5. 삼성전자 현재가 조회로 실제 API 통신 확인
print("\n[5] 삼성전자(005930) 현재가 조회 테스트...")
try:
    import json

    import requests

    url = f"{env.my_url}/uapi/domestic-stock/v1/quotations/inquire-price"
    headers = {
        "Content-Type": "application/json",
        "authorization": f"Bearer {token}",
        "appkey": env.my_app,
        "appsecret": env.my_sec,
        "tr_id": "FHKST01010100",
    }
    params = {
        "fid_cond_mrkt_div_code": "J",
        "fid_input_iscd": "005930",
    }
    res = requests.get(url, headers=headers, params=params, timeout=10)
    data = res.json()

    if res.status_code == 200 and data.get("rt_cd") == "0":
        output = data.get("output", {})
        print(f"    종목명       : 삼성전자")
        print(f"    현재가       : {int(output.get('stck_prpr', 0)):,} 원")
        print(f"    전일대비     : {output.get('prdy_vrss', '-')} 원")
        print(f"    등락률       : {output.get('prdy_ctrt', '-')} %")
        print(f"\n✅ 증권사 연동 테스트 완료! 모든 단계 정상.")
    else:
        print(f"    ❌ API 오류: {data.get('msg1', data)}")

except Exception as e:
    print(f"    ❌ 예외 발생: {e}")
