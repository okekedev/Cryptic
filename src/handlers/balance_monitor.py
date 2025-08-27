# src/handlers/balance_monitor.py

import logging

logger = logging.getLogger(__name__)

def handle_balance(message):
    """
    Handle balance channel messages.
    Logs updates and can be extended for alerts (e.g., low balance thresholds).
    Note: This is for private/authenticated channels; not used in public-only mode.
    """
    account_id = message.get('account_id', 'N/A')
    currency = message.get('currency', 'N/A')
    holds = message.get('holds', 'N/A')
    available = message.get('available', 'N/A')
    updated = message.get('updated', 'N/A')
    timestamp = message.get('timestamp', 'N/A')
    
    logger.info(
        f"Balance update - Account: {account_id}, Currency: {currency}, "
        f"Holds: {holds}, Available: {available}, Updated: {updated}, Timestamp: {timestamp}"
    )
    
    # Example: Alert if available balance is low (extend with email/Slack integration)
    try:
        if float(available) < 100.0:  # Hypothetical threshold
            logger.warning(f"Low balance alert for {currency}: Available = {available}")
    except ValueError:
        logger.error(f"Invalid available balance value: {available}")