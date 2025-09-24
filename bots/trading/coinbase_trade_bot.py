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
    
    def get_account_balance_for_currency(self, currency: str)-> Tuple[float, str]:
        
        accounts = self.check_account_balances()
        
        for account in accounts.get('accounts', []):
            if account['currency'] == currency:
                balance = float(account['available_balance']['value'])
                return balance, account['uuid']
            
            return 0.0, None
        
    def calculate_trade_parameters(self, 
                                product_id: str, 
                                side: str,
                                position_percentage: Optional[float] = None) -> Dict:

        base_currency, quote_currency = product_id.split('-')
        
        if side.upper() == 'BUY':
            required_currency = quote_currency
            order_param_key = 'quote_size'
        else:
            required_currency = base_currency
            order_param_key = 'base_size'
        
        balance, account_uuid = self.get_account_balance_for_currency(required_currency)
        
        if balance <= 0:
            return {
                'validation_passed': False,
                'message': f'Insufficient {required_currency} balance: {balance}',
                'available_balance': balance,
                'required_currency': required_currency
            }
            
        percentage = position_percentage or self.config['max_position_percentage']
        reserved_balance = balance * (self.config['reserve_percentage'] / 100.0) if required_currency == 'USD' else 0.0
        available_for_trade = balance - reserved_balance
        trade_amount = available_for_trade * (percentage / 100.0)
        
        if required_currency == 'USD' and trade_amount < self.config['min_trade_usd']:
            return {
                'validation_passed': False,
                'message': f'Trade amount ${trade_amount:.2f} below minimum ${self.config["min_trade_usd"]}',
                'available_balance': balance,
                'calculated_amount': trade_amount,
                'reserved_balance': reserved_balance
            }
            
        if required_currency in ['USD', 'EUR', 'GBP']:
            formatted_amount = f"{trade_amount:.8f}"
            
            else:
               formatted_amount = f"{trade_amount:.8f}"
               
            return { 
            'validation_passed': True,
            'trade_amount': formatted_amount,
            'currency': required_currency,
            'account_uuid': account_uuid,
            'available_balance': balance,
            'percentage_used': percentage,
            'order_params': {order_param_key: formatted_amount},
            'reserved_balance': reserved_balance,
            'message': f'Ready to {side} {formatted_amount} {required_currency}, reserving {self.config["reserve_percentage"]}% (${reserved_balance:.2f})'
        }