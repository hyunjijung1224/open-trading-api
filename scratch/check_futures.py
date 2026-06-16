# -*- coding: utf-8 -*-
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "examples_user"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "examples_user", "domestic_futureoption"))

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config import config
import kis_auth as ka
from domestic_futureoption_functions import display_board_futures

# env mapping
ka._cfg["paper_app"] = config.KIS_APP_KEY
ka._cfg["paper_sec"] = config.KIS_APP_SECRET
ka._cfg["my_paper_future"] = config.KIS_ACCOUNT_NO.split("-")[0]
ka._cfg["my_prod"] = config.KIS_ACCOUNT_NO.split("-")[1]

# auth
ka.auth(svr="vps", product=config.KIS_ACCOUNT_NO.split("-")[1])

# Query KOSPI 200 Mini Futures board
print("Querying KOSPI 200 Mini Futures board (MKI)...")
df = display_board_futures(
    fid_cond_mrkt_div_code="F",
    fid_cond_scr_div_code="20503",
    fid_cond_mrkt_cls_code="MKI"
)
print("=== Mini Futures Board Result ===")
print(df)
