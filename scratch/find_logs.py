import os

print("=== Searching for files modified today containing '체결' or 'order' ===")
for root, dirs, files in os.walk("e:\\0-aiTrading\\open-trading-api"):
    if ".venv" in root or ".git" in root:
        continue
    for f in files:
        if f.endswith(".py") or f.endswith(".json") or f.endswith(".md"):
            continue
        path = os.path.join(root, f)
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as file_handle:
                content = file_handle.read()
                if "체결" in content or "order" in content:
                    print(f"Match: {path}")
        except Exception:
            pass
