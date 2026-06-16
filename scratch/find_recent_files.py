import os
import time

now = time.time()
one_day = 24 * 3600

print("=== Files modified in the last 24 hours ===")
for root, dirs, files in os.walk("e:\\0-aiTrading\\open-trading-api"):
    # Skip .venv and .git
    if ".venv" in root or ".git" in root:
        continue
    for f in files:
        path = os.path.join(root, f)
        try:
            mtime = os.path.getmtime(path)
            if now - mtime < one_day:
                print(f"{path} - {time.ctime(mtime)}")
        except Exception:
            pass
