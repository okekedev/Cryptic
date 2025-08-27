# src/server.py

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from contextlib import asynccontextmanager
import asyncio
import logging
import json

try:
    from src.channels import get_channels, get_product_ids
    from src.processor import process_message
    from src.utils import setup_logging
    from src.handlers.trade_alerts import last_logged  # For broadcast data
except ImportError as e:
    logging.error(f"Import error: {e}. Check paths and installations.")
    raise

import websockets

app = FastAPI(title="Crypto Monitor Live Server")

# WebSocket URI and headers from original main.py
URI = 'wss://ws-feed.exchange.coinbase.com'
extra_headers = {'Sec-WebSocket-Extensions': 'permessage-deflate'}

# Message queue for decoupling (from previous main.py)
queue = asyncio.Queue()

async def consumer():
    logging.info("Starting consumer task.")
    while True:
        message = await queue.get()
        try:
            process_message(message)
        except Exception as e:
            logging.error(f"Error processing message: {e}")
        finally:
            queue.task_done()

async def websocket_listener():
    logging.info("Starting WebSocket listener task.")
    product_ids = get_product_ids()
    channels = get_channels()
    
    subscribe_message = json.dumps({
        'type': 'subscribe',
        'product_ids': product_ids,
        'channels': channels
    })
    
    while True:
        try:
            async with websockets.connect(
                URI, 
                additional_headers=extra_headers, 
                ping_interval=None,
                max_size=2**24
            ) as websocket:
                await websocket.send(subscribe_message)
                logging.info("Subscribed to public channels.")
                
                while True:
                    response = await websocket.recv()
                    json_response = json.loads(response)
                    await queue.put(json_response)
        
        except websockets.exceptions.ConnectionClosed as e:
            logging.warning(f"Connection closed (code: {e.code}, reason: {e.reason or 'unknown'}), retrying...")
            await asyncio.sleep(1)
        except Exception as e:
            logging.error(f'Error: {e}')
            await asyncio.sleep(1)

# Lifespan for startup: Setup logging, start consumer and listener as background tasks
@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    logging.info("Lifespan startup: Initializing tasks.")
    consumer_task = asyncio.create_task(consumer())
    listener_task = asyncio.create_task(websocket_listener())
    yield  # Server runs here
    # Cleanup on shutdown
    logging.info("Lifespan shutdown: Canceling tasks.")
    consumer_task.cancel()
    listener_task.cancel()
    await queue.join()

app.lifespan = lifespan

# Craigslist-style HTML template (embedded; can move to a file later)
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Crypto Monitor</title>
    <style>
        body { font-family: arial, sans-serif; background-color: #fff; color: #000; margin: 20px; }
        h1 { font-size: 18pt; color: #000; }
        table { width: 100%; border-collapse: collapse; border: 1px solid #ccc; }
        th, td { padding: 8px; text-align: left; border: 1px solid #ccc; }
        th { background-color: #eee; font-weight: bold; }
        .positive { color: green; }
        .negative { color: red; }
        a { color: #00f; text-decoration: underline; }
        .footer { font-size: 10pt; color: #999; margin-top: 20px; }
    </style>
    <script>
        const ws = new WebSocket(`ws://${window.location.host}/ws`);
        ws.onmessage = function(event) {
            const data = JSON.parse(event.data);f
            const tableBody = document.querySelector('tbody');
            tableBody.innerHTML = '';  // Clear and rebuild table
            for (const [product, info] of Object.entries(data)) {
                const row = document.createElement('tr');
                row.innerHTML = `
                    <td>${product}</td>
                    <td>$${info.price}</td>
                    <td>$${info.high_24h}</td>
                    <td>$${info.low_24h}</td>
                    <td>${info.volume_24h} ${product.split('-')[0]}</td>
                    <td class="${info.percent_change_24h.startsWith('-') ? 'negative' : 'positive'}">${info.percent_change_24h}</td>
                    <td>${info.timestamp}</td>
                `;
                tableBody.appendChild(row);
            }
        };
    </script>
</head>
<body>
    <h1>Crypto Prices - Real-Time from Coinbase</h1>
    <table>
        <thead>
            <tr>
                <th>Product</th>
                <th>Live Price</th>
                <th>24hr High</th>
                <th>24hr Low</th>
                <th>24hr Volume</th>
                <th>24hr % Change</th>
                <th>Last Updated</th>
            </tr>
        </thead>
        <tbody>
            <!-- Populated via JS/WebSocket -->
        </tbody>
    </table>
    <div class="footer">Data via Coinbase WebSocket. <a href="https://www.coinbase.com">More info</a></div>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
async def root():
    logging.info("Serving root path.")
    return HTML_TEMPLATE

# WebSocket for live updates (broadcast on data change)
websocket_clients = set()  # Track connected clients

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    websocket_clients.add(websocket)
    
    data = {pid: {**data, 'timestamp': data['timestamp'].isoformat()} for pid, data in last_logged_items()}
    if data: 
        await websocket.send_text(json.dumps(data))
        logging.debug("Sent initial data to new client.")
    else:
        logging.debug("No data available yet for initial send to new client")
        
    try:
        while True:
            client_message = await websocket.receive_text()
            logging.debug(f"Received message from client: {client_message}")
    except WebSocketDisconnect:
        websocket_clients.remove(websocket)
        logging.info("Client disconnected from /ws")

async def broadcast_update():
    """
    Broadcast latest data to all connected clients.
    Call this from handle_ticker after updating last_logged.
    """
    if websocket_clients:
        data = {pid: {**data, 'timestamp': data['timestamp'].isoformat()} for pid, data in last_logged.items()}
        message = json.dumps(data)
        await asyncio.gather(*(client.send_text(message) for client in websocket_clients if not client.closed))
        logging.debug("Broadcasted update to clients.")