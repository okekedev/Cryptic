import requests
import json
import uuid
from typing import Dict, Optional, Tuple
from datetime import datetime

BASE_URL = "https://api-sandbox.coinbase.com/api/v3/brokerage"

class CoinbaseTradeBot: 
    
    def __init__(self, config: Optional[Dict] = None):
        
        self.base_url = BASE_URL
        self.config = config or {
            'max_position_percentage': 100,
            'min_trade_usd': 10.00,
            'reserve_percentage': 1.0,
            'defualt_order_type': 'market_market_ioc',
        }