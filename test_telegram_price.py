#!/usr/bin/env python3
"""
Test script to send a live price update to Telegram
"""
import os
import requests
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
BACKEND_URL = "http://localhost:5000"

def get_live_price(product_id: str):
    """Get live price from backend"""
    try:
        response = requests.get(f"{BACKEND_URL}/tickers/{product_id}")
        if response.status_code == 200:
            return response.json()
        else:
            print(f"Error: {response.status_code}")
            return None
    except Exception as e:
        print(f"Failed to get price: {e}")
        return None

def send_telegram_message(message: str):
    """Send message to Telegram"""
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        data = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "Markdown"
        }
        response = requests.post(url, json=data)
        if response.status_code == 200:
            print("SUCCESS: Message sent to Telegram!")
            return True
        else:
            print(f"FAILED: {response.status_code}")
            print(response.text)
            return False
    except Exception as e:
        print(f"ERROR: {e}")
        return False

def main():
    print("Fetching live price from WebSocket feed...")

    # Get multiple tickers to show variety
    pairs = ["BTC-USD", "ETH-USD", "SOL-USD"]

    message_lines = ["🔴 *LIVE PRICE UPDATE FROM WEBSOCKET FEED*\n"]

    for pair in pairs:
        ticker = get_live_price(pair)
        if ticker:
            price = ticker.get('price', 0)
            bid = ticker.get('bid', 0)
            ask = ticker.get('ask', 0)

            message_lines.append(f"*{pair}*")
            message_lines.append(f"💰 Price: ${price:,.2f}")
            message_lines.append(f"📊 Bid: ${bid:,.2f} | Ask: ${ask:,.2f}\n")

    message_lines.append("✅ Data sourced from live multi-connection WebSocket feed")
    message_lines.append("⚡ 20 simultaneous connections monitoring 300+ pairs")

    message = "\n".join(message_lines)

    print("\nSending message to Telegram...")

    send_telegram_message(message)

if __name__ == '__main__':
    main()