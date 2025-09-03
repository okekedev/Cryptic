# import os
# import time
# import json
# import requests
# from collections import defaultdict, deque
# import websocket
# from threading import Thread
# import ccxt

# # Config from env
# CRYPTOS = os.getenv('MONITORING_CRYPTOS', 'BTC-USD,ETH-USD').split(',')
# THRESHOLD = float(os.getenv('VOLUME_THRESHOLD', 1.5))
# WINDOW_MINUTES = int(os.getenv('WINDOW_MINUTES', 5))
# WINDOW_SECONDS = WINDOW_MINUTES * 60
# BACKEND_URL = 'http://backend:3000/alert' 
# WS_URL = 'wss://ws-feed.exchange.coinbase.com'
# EXCHANGE = ccxt.coinbase()

# # Data structures: per crypto, deque of (timestamp, volume) for windows
# volume_windows = {crypto: deque() for crypto in CRYPTOS}
# historical_avgs = {crypto: 0.0 for crypto in CRYPTOS}

# def fetch_historical_volume(crypto):
#     """Fetch last 1h trades via CCXT to compute initial avg 5min volume."""
#     try:
#         since = int((time.time() - 3600) * 1000)  # 1h ago in ms
#         trades = EXCHANGE.fetch_trades(crypto, since=since)
#         window_vol = 0.0
#         window_start = time.time() - 3600
#         num_windows = 0
#         for trade in trades:
#             ts = trade.get('timestamp')  # to seconds
#             if ts is None or not isinstance(ts, (int, float, str)):
#                 print(f"Skipping trade with invalid timestamp for {crypto}")
#                 continue
            
#             if isinstance(ts, str):
#                 ts = float(ts)
#             elif isinstance(ts, (int, float)):
#                 ts = float(ts)
#             ts /= 1000
#             if ts >= window_start + WINDOW_SECONDS:
#                 if window_vol > 0:
#                     historical_avgs[crypto] += window_vol
#                     num_windows += 1    
#                 window_vol = 0.0
#                 window_start += WINDOW_SECONDS
                
#             amount = trade.get('amount')
#             if amount is not None and isinstance(amount, (int, float, str)):
#                 if isinstance(amount, str):
#                     amount = float(amount)
#                 window_vol += float(amount)
#             else: print(f"Skipping trade with invalid amount for {crypto}")
#             continue
#         if num_windows > 0:
#             historical_avgs[crypto] /= num_windows
#         print(f"Initial avg volume for {crypto}: {historical_avgs[crypto]}")
#     except Exception as e:
#         print(f"Error fetching historical for {crypto}: {e}")
#         historical_avgs[crypto] = 1.0

# def on_open(ws):
#     """Subscribe to matches channel for cryptos."""
#     sub_msg = {
#         "type": "subscribe",
#         "product_ids": CRYPTOS,
#         "channels": ["matches"]
#     }
#     ws.send(json.dumps(sub_msg))
#     print("Subscribed to matches channel")

# def on_message(ws, message):
#     """Process match messages, update volumes, check surges."""
#     data = json.loads(message)
#     if data['type'] == 'match':
#         crypto = data['product_id']
#         size = float(data['size'])
#         ts = time.time()
#         # Add to window
#         volume_windows[crypto].append((ts, size))
#         # Clean old entries
#         while volume_windows[crypto] and volume_windows[crypto][0][0] < ts - WINDOW_SECONDS:
#             volume_windows[crypto].popleft()
#         # Calc current volume
#         current_vol = sum(v for _, v in volume_windows[crypto])
#         avg_vol = historical_avgs[crypto]
#         if avg_vol > 0 and current_vol > THRESHOLD * avg_vol:
#             alert = {
#                 "crypto": crypto,
#                 "current_vol": current_vol,
#                 "avg_vol": avg_vol,
#                 "threshold": THRESHOLD
#             }
#             try:
#                 requests.post(BACKEND_URL, json=alert)
#                 print(f"Alert sent for {crypto}: {current_vol} > {THRESHOLD * avg_vol}")
#                 # Update historical avg (simple moving avg)
#                 historical_avgs[crypto] = (historical_avgs[crypto] * 0.9) + (current_vol * 0.1)
#             except Exception as e:
#                 print(f"Alert POST failed: {e}")

# def on_error(ws, error):
#     print(f"WS error: {error}")

# def on_close(ws, close_status_code, close_msg):
#     print(f"WS closed: {close_status_code} {close_msg}")

# def ws_thread():
#     """Run WebSocket in thread with reconnection."""
#     backoff = 1
#     while True:
#         try:
#             ws = websocket.WebSocketApp(WS_URL, on_open=on_open, on_message=on_message, on_error=on_error, on_close=on_close)
#             ws.run_forever()
#         except Exception as e:
#             print(f"WS exception: {e}")
#         time.sleep(backoff)
#         backoff = min(backoff * 2, 60)  # Exponential backoff

# # Bootstrap historical
# for crypto in CRYPTOS:
#     fetch_historical_volume(crypto)

# # Start WS thread
# Thread(target=ws_thread, daemon=True).start()

# # Main loop (keep alive)
# while True:
#     time.sleep(10)  # Can add periodic checks if needed