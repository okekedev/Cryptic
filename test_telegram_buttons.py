#!/usr/bin/env python3
"""
Test script to send a live price update with trading buttons to Telegram
"""
import os
import requests
from dotenv import load_dotenv
from datetime import datetime

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

def send_telegram_message_with_buttons(product_id: str, ticker_data: dict):
    """Send message to Telegram with Buy, Chart, and Ignore buttons"""
    try:
        # Format the message like a spike alert
        price = ticker_data.get('price', 0)
        bid = ticker_data.get('bid', 0)
        ask = ticker_data.get('ask', 0)

        # Simulate a price change for the test
        old_price = price * 0.95  # Simulate 5% increase
        pct_change = ((price - old_price) / old_price) * 100

        message = (
            f"🔴 *LIVE PRICE TEST WITH BUTTONS*\n\n"
            f"*Symbol:* {product_id}\n"
            f"*Current Price:* ${price:,.2f}\n"
            f"*Bid:* ${bid:,.2f} | *Ask:* ${ask:,.2f}\n"
            f"*Simulated Change:* +{pct_change:.2f}%\n"
            f"*Time:* {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
            f"⚡ Live data from WebSocket feed"
        )

        # Create inline keyboard with Buy, Chart, and Ignore buttons
        # Chart button uses URL to open directly without callback
        chart_url = f"https://www.coinbase.com/advanced-trade/spot/{product_id}"
        inline_keyboard = {
            "inline_keyboard": [
                [
                    {
                        "text": "🚀 Buy",
                        "callback_data": f"buy:{product_id}"
                    },
                    {
                        "text": "📊 Chart",
                        "url": chart_url
                    }
                ],
                [
                    {
                        "text": "👁️ Ignore",
                        "callback_data": f"ignore:{product_id}"
                    }
                ]
            ]
        }

        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        data = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "Markdown",
            "reply_markup": inline_keyboard
        }

        response = requests.post(url, json=data)
        if response.status_code == 200:
            print("SUCCESS: Message with buttons sent to Telegram!")
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

    # Test with BTC-USD
    product_id = "BTC-USD"
    ticker = get_live_price(product_id)

    if ticker:
        print(f"Got live price for {product_id}: ${ticker.get('price', 0):,.2f}")
        print("\nSending message with Buy, Chart, and Ignore buttons to Telegram...")
        send_telegram_message_with_buttons(product_id, ticker)
    else:
        print("Failed to get ticker data")

if __name__ == '__main__':
    main()