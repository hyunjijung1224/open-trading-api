import os
import sys
import asyncio
import json
import websockets
import requests

# Root directory import path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config import config

async def get_approval_key():
    base_url = "https://openapivts.koreainvestment.com:29443" if config.KIS_IS_PAPER else "https://openapi.koreainvestment.com:9443"
    url = f"{base_url}/oauth2/Approval"
    payload = {
        "grant_type": "client_credentials",
        "appkey": config.KIS_APP_KEY,
        "secretkey": config.KIS_APP_SECRET
    }
    res = requests.post(url, json=payload, headers={"content-type": "application/json"})
    res.raise_for_status()
    return res.json()["approval_key"]

def make_subscribe_frame(approval_key, tr_id, code):
    return json.dumps({
        "header": {
            "approval_key": approval_key,
            "custtype": "P",
            "tr_type": "1",
            "content-type": "utf-8"
        },
        "body": {
            "input": {
                "tr_id": tr_id,
                "tr_key": code
            }
        }
    })

async def main():
    print("Fetching approval key...")
    try:
        approval_key = await get_approval_key()
        print(f"Approval Key: {approval_key}")
    except Exception as e:
        print(f"Failed to fetch approval key: {e}")
        return

    ws_url = "ws://ops.koreainvestment.com:31000" if config.KIS_IS_PAPER else "ws://ops.koreainvestment.com:21000"
    print(f"Connecting to KIS WebSocket: {ws_url}")

    # Let's test standard code 105V07
    std_code = "105V07"

    async with websockets.connect(ws_url, ping_interval=30, ping_timeout=10) as ws:
        print(f"Subscribing to H0IFCNT0 with {std_code}...")
        await ws.send(make_subscribe_frame(approval_key, "H0IFCNT0", std_code))
        
        print("\nListening for messages...")
        for i in range(10):
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=5.0)
                print(f"RECV {i}: {msg}")
            except asyncio.TimeoutError:
                print("Timeout waiting for message")
                break
            except Exception as e:
                print(f"Error reading message: {e}")
                break

if __name__ == "__main__":
    asyncio.run(main())
