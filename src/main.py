import asyncio
import json
import logging
import os

from src.channels import get_channels, get_product_ids
from src.processor import process_message
from src.utils import setup_logging

import websockets
print(f"Loaded websockets version: {websockets.__version__}")

import sys
print(f"Python version: {sys.version}")
# WebSocket URI
URI = 'wss://ws-feed.exchange.coinbase.com'

async def main():
    setup_logging()
    logger = logging.getLogger(__name__)

    product_ids = get_product_ids()
    channels = get_channels()
    
    subscribe_message = json.dumps({
        'type': 'subscribe',
        'product_ids': product_ids,
        'channels': channels
    })
    
    # Enable compression
    extra_headers = {'Sec-WebSocket-Extensions': 'permessage-deflate'}
    
    while True:
        try:
            async with websockets.connect(URI, additional_headers=extra_headers, ping_interval=None) as websocket:
                await websocket.send(subscribe_message)
                logger.info("Subscribed to public channels.")
                
                while True:
                    response = await websocket.recv()
                    json_response = json.loads(response)
                    # Process the message using processor module
                    process_message(json_response)
        
        except (websockets.exceptions.ConnectionClosedError, websockets.exceptions.ConnectionClosedOK):
            logger.warning('Connection closed, retrying...')
            await asyncio.sleep(1)
        except Exception as e:
            logger.error(f'Error: {e}')
            await asyncio.sleep(1)

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Exiting WebSocket listener.")