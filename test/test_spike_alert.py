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
            'symbol': 'TEST-USD',
            'spike_type': 'pump',
            'pct_change': 8.75,
            'old_price': 100.0,
            'new_price': 108.75,
            'time_span_seconds': 240,
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
    print("Triggering spike alert via Socket.IO...")
    trigger_spike_alert_via_socketio()