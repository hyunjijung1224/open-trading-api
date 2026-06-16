import os
import sys
import asyncio
import logging

# Enable DEBUG logging to see ExecutionEngine and requests output
logging.basicConfig(level=logging.DEBUG)

# Root directory import path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from future.engines.execution_engine import ExecutionEngine

async def main():
    print("Initializing ExecutionEngine...")
    engine = ExecutionEngine()
    
    print("\nQuerying investor trend for KOSPI 200 Mini Futures (105V07)...")
    res1 = await engine.fetch_investor_trend("105V07")
    print(f"Result (Mini): {res1}")

    print("\nQuerying investor trend for KOSPI 200 Regular Futures (101W09)...")
    res2 = await engine.fetch_investor_trend("101W09")
    print(f"Result (Regular): {res2}")

if __name__ == "__main__":
    asyncio.run(main())
