import os
import sys
import asyncio
from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config import config
from future.engines.execution_engine import ExecutionEngine

load_dotenv()

async def main():
    engine = ExecutionEngine()
    token = await engine._ensure_token()

    url = f"{engine.base_url}/uapi/domestic-futureoption/v1/quotations/inquire-price"
    headers = engine._get_headers("FHMIF10000000")
    
    params = {
        "FID_COND_MRKT_DIV_CODE": "F",
        "FID_INPUT_ISCD": "A05609"
    }
    
    import requests
    res = requests.get(url, headers=headers, params=params)
    if res.status_code == 200:
        data = res.json()
        output1 = data.get("output1", {})
        print("Output1 details:")
        for k, v in output1.items():
            print(f"  {k}: {v}")
    else:
        print(res.text)

if __name__ == "__main__":
    asyncio.run(main())
