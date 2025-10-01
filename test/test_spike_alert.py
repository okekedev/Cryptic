#!/usr/bin/env python3
"""
Test script to simulate a spike alert with buttons
"""
import os
import requests
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()

BACKEND_URL = "http://localhost:5000"
TELEGRAM_BOT_WEBHOOK = "http://localhost:8080/webhook"

def trigger_spike_alert_via_webhook():
    """Send spike alert directly to Telegram bot via webhook"""
    spike_data = {
        'symbol': 'BTC-USD',
        'spike_type': 'pump',
        'pct_change': 5.25,
        'old_price': 60000.0,
        'new_price': 63150.0,
        'time_span_seconds': 300,
        'timestamp': datetime.now().isoformat(),
        'spike_time': datetime.now().isoformat(),
        'event_type': 'spike_start'
    }

    print(f"Sending spike alert via webhook: {spike_data}")
    response = requests.post(TELEGRAM_BOT_WEBHOOK, json=spike_data)
    print(f"Response: {response.status_code} - {response.text}")

def trigger_spike_alert_via_socketio():
    """Emit a spike alert via Socket.IO to backend"""
    import socketio

    # Create Socket.IO client
    sio = socketio.Client()

    @sio.event
    def connect():
        print("Connected to backend Socket.IO")

        # Emit a spike alert
        spike_data = {
            'symbol': 'BTC-USD',
            'spike_type': 'pump',
            'pct_change': 5.25,
            'old_price': 60000.0,
            'new_price': 63150.0,
            'time_span_seconds': 300,
            'timestamp': datetime.now().isoformat(),
            'spike_time': datetime.now().isoformat(),
            'event_type': 'spike_start'
        }

        print(f"Emitting spike_alert: {spike_data}")
        sio.emit('spike_alert', spike_data)

        # Give it time to process
        import time
        time.sleep(2)
        sio.disconnect()
        print("Alert emitted successfully!")

    @sio.event
    def disconnect():
        print("Disconnected from backend")

    try:
        sio.connect(BACKEND_URL)
        sio.wait()
    except Exception as e:
        print(f"Error: {e}")

if __name__ == '__main__':
    print("Triggering spike alert via webhook...")
    trigger_spike_alert_via_webhook()