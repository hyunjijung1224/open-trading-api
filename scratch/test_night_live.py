import requests, os, time, sys
from dotenv import load_dotenv
sys.path.insert(0, r'E:\0-aiTrading\open-trading-api')
from config import config
load_dotenv()

base_url = 'https://openapi.koreainvestment.com:9443'
appkey = config.KIS_REAL_APP_KEY
appsecret = config.KIS_REAL_APP_SECRET

res = requests.post(f'{base_url}/oauth2/tokenP', json={'grant_type':'client_credentials','appkey':appkey,'appsecret':appsecret})
if res.status_code != 200:
    print(f'Token fail: {res.text}')
    sys.exit(1)
token = res.json()['access_token']
print('Token OK')
time.sleep(1.2)

headers = {
    'Content-Type': 'application/json',
    'authorization': f'Bearer {token}',
    'appkey': appkey,
    'appsecret': appsecret,
    'tr_id': 'FHKIF03020200'
}

# 오늘 19:00 이후 - 야간 캔들이 포함되는지 확인
for code in ['10500', '10100']:
    params = {
        'FID_COND_MRKT_DIV_CODE': 'F',
        'FID_INPUT_ISCD': code,
        'FID_HOUR_CLS_CODE': '60',
        'FID_PW_DATA_INCU_YN': 'Y',
        'FID_FAKE_TICK_INCU_YN': 'N',
        'FID_INPUT_DATE_1': '20260610',
        'FID_INPUT_HOUR_1': '190100'
    }
    res2 = requests.get(
        f'{base_url}/uapi/domestic-futureoption/v1/quotations/inquire-time-fuopchartprice',
        headers=headers, params=params
    )
    d = res2.json()
    rows = d.get('output2', [])
    rt = d.get('rt_cd')
    print(f'code={code}: {len(rows)} rows, rt={rt}')
    if rows:
        first = rows[0]
        last = rows[-1]
        print(f'  Latest: date={first.get("stck_bsop_date")} time={first.get("stck_cntg_hour")} close={first.get("futs_prpr")}')
        print(f'  Oldest: date={last.get("stck_bsop_date")}  time={last.get("stck_cntg_hour")} close={last.get("futs_prpr")}')
        night = [r for r in rows if int(r.get('stck_cntg_hour', '0')) >= 180000]
        print(f'  Night rows (>=18:00:00): {len(night)}')
        for r in night[:5]:
            print(f'    {r.get("stck_bsop_date")} {r.get("stck_cntg_hour")} close={r.get("futs_prpr")} vol={r.get("cntg_vol")}')
    time.sleep(1.5)
