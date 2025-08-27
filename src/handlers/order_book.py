# src/handlers/order_book.py

import logging
from collections import defaultdict
from decimal import Decimal

from src.utils import check_sequence_gap

logger = logging.getLogger(__name__)

# In-memory order books: {product_id: {'bids': {price: size}, 'asks': {price: size}, 'sequence': int}}
order_books = defaultdict(lambda: {'bids': {}, 'asks': {}, 'sequence': None})

def handle_level2(message):
    """
    Handle level2_batch messages: snapshot and l2update.
    Maintains an in-memory order book for each product.
    Checks for sequence gaps.
    """
    msg_type = message['type']
    product_id = message['product_id']
    book = order_books[product_id]
    
    if msg_type == 'snapshot':
        # Initialize order book with snapshot
        book['bids'] = {Decimal(price): Decimal(size) for price, size in message.get('bids', [])}
        book['asks'] = {Decimal(price): Decimal(size) for price, size in message.get('asks', [])}
        sequence = message.get('sequence')
        book['sequence'] = check_sequence_gap(book['sequence'], sequence, product_id)
        logger.info(f"Order book snapshot loaded for {product_id}: {len(book['bids'])} bids, {len(book['asks'])} asks")
    
    elif msg_type == 'l2update':
        # Apply updates
        sequence = message.get('sequence')
        prev_sequence = book['sequence']
        book['sequence'] = check_sequence_gap(prev_sequence, sequence, product_id)
        
        changes = message.get('changes', [])
        for change in changes:
            side, price_str, size_str = change
            price = Decimal(price_str)
            size = Decimal(size_str)
            side_dict = book['bids'] if side == 'buy' else book['asks']
            
            if size == 0:
                side_dict.pop(price, None)
            else:
                side_dict[price] = size
        
        logger.debug(f"Applied {len(changes)} changes to {product_id} order book")
        
        # Optional: Log top levels for monitoring
        if book['bids']:
            top_bid = max(book['bids'].keys())
            logger.debug(f"Top Bid for {product_id}: {top_bid} @ {book['bids'][top_bid]}")
        if book['asks']:
            top_ask = min(book['asks'].keys())
            logger.debug(f"Top Ask for {product_id}: {top_ask} @ {book['asks'][top_ask]}")
    
    else:
        logger.warning(f"Unexpected message type in handle_level2: {msg_type}")