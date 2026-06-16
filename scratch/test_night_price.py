# -*- coding: utf-8 -*-
import os
import sys
import requests
import asyncio
from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config import config
from future.engines.execution_engine import ExecutionEngine

load_dotenv()

async def check_price(engine, code):
    await engine._ensure_token()
    url = f"{engine.base_url}/uapi/domestic-futureoption/v1/quotations/inquire-price"
    headers = engine._get_headers("FHMIF10000000")
    params = {
        "FID_COND_MRKT_DIV_CODE": "F",
        "FID_INPUT_ISCD": code
    }
    res = requests.get(url, headers=headers, params=params)
    print(f"Code: {code} | Status: {res.status_code}")
    if res.status_code == 200:
        data = res.json()
        rt = data.get("rt_cd")
        msg = data.get("msg1")
        output1 = data.get("output1", {})
        price = output1.get("futs_prpr")
        print(f"  rt_cd: {rt} | msg: {msg} | Price: {price}")
    else:
        print(f"  Error: {res.text}")

async def main():
    engine = ExecutionEngine()
    
    # Test candidates
    codes = [
        "A01W09",  # Night standard futures Sep 2026?
        "A01609",  # Day standard futures Sep 2026
        "A05609",  # Day mini futures Sep 2026
        "A01W06",  # Night standard futures Jun 2026?
        "A01606",  # Day standard futures Jun 2026
        "A05606",  # Day mini futures Jun 2026
    ]
    
    for c in codes:
        await check_price(engine, c)
        await asyncio.sleep(1.5)

if __name__ == "__main__":
    asyncio.run(main())
