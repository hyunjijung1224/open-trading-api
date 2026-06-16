import os
import sys
import gspread
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv

load_dotenv()

credentials_path = os.getenv("GOOGLE_SA_KEY_PATH")
sheet_id = os.getenv("GOOGLE_SHEET_ID")

print("Cred Path:", credentials_path)
print("Sheet ID:", sheet_id)

scopes = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

try:
    creds = Credentials.from_service_account_file(credentials_path, scopes=scopes)
    client = gspread.authorize(creds)
    spreadsheet = client.open_by_key(sheet_id)
    
    print("\n=== ActivePositions Sheet ===")
    sheet_pos = spreadsheet.worksheet("ActivePositions")
    print(sheet_pos.get_all_records())
    
    print("\n=== TradingHistory Sheet (Latest 10) ===")
    sheet_hist = spreadsheet.worksheet("TradingHistory")
    records = sheet_hist.get_all_records()
    for r in records[-10:]:
        print(r)
        
    print("\n=== BotHealth Sheet ===")
    sheet_health = spreadsheet.worksheet("BotHealth")
    print(sheet_health.get_all_records())
    
except Exception as e:
    print("Failed to read Google Sheets:", e)
