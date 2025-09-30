#!/usr/bin/env python3
"""
Test script for health monitoring endpoints
"""

import requests
import time
import json

def test_health_endpoint():
    """Test the health endpoint"""
    base_url = "http://localhost:5001"

    endpoints = {
        'health': '/health',
        'connections': '/connections',
        'prices': '/prices?limit=5',
        'btc_price': '/price/BTC-USD'
    }

    print("Testing Health Monitor Endpoints")
    print("="*50)

    for name, endpoint in endpoints.items():
        try:
            url = base_url + endpoint
            print(f"\nTesting {name}: {url}")

            response = requests.get(url, timeout=5)

            print(f"Status: {response.status_code}")

            if response.headers.get('content-type', '').startswith('application/json'):
                data = response.json()
                print(f"Response: {json.dumps(data, indent=2)}")
            else:
                print(f"Response: {response.text[:200]}...")

        except requests.exceptions.ConnectionError:
            print(f"❌ Connection failed to {url}")
        except requests.exceptions.Timeout:
            print(f"⏰ Timeout connecting to {url}")
        except Exception as e:
            print(f"❌ Error: {e}")

    print("\n" + "="*50)
    print("Health endpoint testing completed")

if __name__ == "__main__":
    # Wait a moment for the service to be ready
    print("Waiting 5 seconds for service to be ready...")
    time.sleep(5)

    test_health_endpoint()