# -*- coding: utf-8 -*-
import os
import sys
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "examples_user"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "examples_user", "domestic_futureoption"))

from config import config
import kis_auth as ka

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

# env mapping
ka._cfg["paper_app"] = config.KIS_APP_KEY
ka._cfg["paper_sec"] = config.KIS_APP_SECRET
ka._cfg["my_paper_future"] = config.KIS_ACCOUNT_NO.split("-")[0]
ka._cfg["my_prod"] = config.KIS_ACCOUNT_NO.split("-")[1]

# auth
ka.auth(svr="vps", product=config.KIS_ACCOUNT_NO.split("-")[1])

print("Querying Standard Futures board directly...")
api_url = "/uapi/domestic-futureoption/v1/quotations/display-board-futures"
tr_id = "FHPIF05030200"
params = {
    "FID_COND_MRKT_DIV_CODE": "F",
    "FID_COND_SCR_DIV_CODE": "20503",
    "FID_COND_MRKT_CLS_CODE": ""
}
res = ka._url_fetch(api_url, tr_id, "", params)
if res.isOK():
    df_fut = pd.DataFrame(res.getBody().output)
    print("=== Standard Futures ===")
    print(df_fut[['futs_shrn_iscd', 'hts_kor_isnm']])
else:
    print("Failed to fetch standard futures:")
    res.printError(url=api_url)

print("\nQuerying Mini Futures board directly...")
params["FID_COND_MRKT_CLS_CODE"] = "MKI"
res = ka._url_fetch(api_url, tr_id, "", params)
if res.isOK():
    df_mki = pd.DataFrame(res.getBody().output)
    print("=== Mini Futures ===")
    print(df_mki[['futs_shrn_iscd', 'hts_kor_isnm']])
else:
    print("Failed to fetch mini futures:")
    res.printError(url=api_url)
