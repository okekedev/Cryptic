# src/utils.py

import logging
import os

def setup_logging(log_level=logging.INFO, log_file='logs/app.log'):
    """
    Set up logging configuration for the application.
    Logs to both console and file.
    """
    # Create logs directory if it doesn't exist
    os.makedirs(os.path.dirname(log_file), exist_ok=True)
    
    # Create logger
    logger = logging.getLogger()
    logger.setLevel(log_level)
    
    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(log_level)
    console_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    console_handler.setFormatter(console_formatter)
    logger.addHandler(console_handler)
    
    # File handler
    file_handler = logging.FileHandler(log_file)
    file_handler.setLevel(log_level)
    file_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    file_handler.setFormatter(file_formatter)
    logger.addHandler(file_handler)
    
    logging.info("Logging setup complete.")

def check_sequence_gap(prev_sequence, current_sequence, product_id):
    """
    Check for sequence gaps in messages to detect drops.
    Logs warnings if gaps or out-of-order sequences are found.
    """
    if prev_sequence is not None:
        if current_sequence <= prev_sequence:
            logging.warning(f"Out-of-order sequence for {product_id}: {current_sequence} <= {prev_sequence}")
        elif current_sequence > prev_sequence + 1:
            logging.warning(f"Sequence gap detected for {product_id}: Missed {current_sequence - prev_sequence - 1} messages")
    return current_sequence