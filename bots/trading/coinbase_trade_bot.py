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
            'default_order_type': 'market_market_ioc',
        }
        
    def check_account_balances(self, limit: int = 49, cursor: Optional[str] = None) -> Dict:
        params = {'limit': limit}
        if cursor: 
            params['cursor'] = cursor
            
        response = requests.get(f"{self.base_url}/accounts", params=params)
        response.raise_for_status()
        return response.json()