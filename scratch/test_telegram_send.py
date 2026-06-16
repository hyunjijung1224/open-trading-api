import os
import sys
import requests
import json
from dotenv import load_dotenv

# Reconfigure stdout to support UTF-8 characters on Windows terminal
try:
    sys.stdout.reconfigure(encoding='utf-8')
except AttributeError:
    pass

# Add parent directory to sys.path to load config if needed
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
load_dotenv()

def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    
    print("=== Telegram Test Sender ===")
    if not token:
        print("[ERROR] TELEGRAM_BOT_TOKEN is not set in .env")
        return
    if not chat_id:
        print("[ERROR] TELEGRAM_CHAT_ID is not set in .env")
        return
        
    masked_token = token[:6] + "..." + token[-6:] if len(token) > 12 else token
    print(f"TELEGRAM_BOT_TOKEN: {masked_token}")
    print(f"TELEGRAM_CHAT_ID: {chat_id}")
    
    message = "🔔 [시스템 테스트] 텔레그램 알림 발송 테스트에 성공했습니다! 🚀"
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message
    }
    
    print("\nSending request to Telegram API...")
    try:
        response = requests.post(url, json=payload, timeout=10)
        print(f"Status Code: {response.status_code}")
        
        try:
            res_json = response.json()
            # Safely print json response
            print("Response JSON:")
            print(json.dumps(res_json, indent=2, ensure_ascii=False))
        except Exception as je:
            print(f"Raw Response: {response.text}")
        
        if response.status_code == 200:
            print("\n[SUCCESS] Telegram message sent successfully!")
        else:
            print("\n[FAILURE] Telegram API returned an error.")
    except Exception as e:
        print(f"\n[ERROR] Request failed: {e}")

if __name__ == "__main__":
    main()
