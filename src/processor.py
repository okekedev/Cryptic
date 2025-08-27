import logging

from src.handlers.trade_alerts import handle_ticker

logger = logging.getLogger(__name__)

def process_message(message):
    """
    Process incoming WebSocket message based on its type.
    Dispatches only to ticker handler for this simplified setup.
    """
    msg_type = message.get('type')
    product_id = message.get('product_id', 'N/A')
    
    if msg_type is None:
        logger.warning("Received message without 'type' field.")
        return
    
    logger.debug(f"Processing message type: {msg_type} for product: {product_id}")
    
    if msg_type == 'ticker':
        handle_ticker(message)
    
    elif msg_type == 'subscriptions':
        # Log successful subscriptions
        channels = message.get('channels', [])
        logger.info(f"Subscriptions confirmed: {', '.join([ch['name'] for ch in channels])}")
    
    else:
        # Ignore/log other types minimally
        logger.debug(f"Ignoring non-ticker message type: {msg_type}")