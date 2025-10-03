#!/usr/bin/env python3
"""
Simple Coinbase API client for dump trading bot
Uses the same authentication as trading_manager.py
"""
import os
import time
import logging
import jwt
import requests
from typing import Dict, Optional
from cryptography.hazmat.primitives import serialization
from datetime import datetime

logger = logging.getLogger(__name__)


class CoinbaseClient:
    """Simple Coinbase Advanced Trade API client"""

    def __init__(self):
        self.api_key = os.getenv('COINBASE_API_KEY')
        self.signing_key = os.getenv('COINBASE_SIGNING_KEY')

        if not self.api_key or not self.signing_key:
            raise ValueError('COINBASE_API_KEY and COINBASE_SIGNING_KEY must be set')

        # Replace escaped newlines
        self.signing_key = self.signing_key.replace('\\n', '\n')
        self.base_url = "https://api.coinbase.com"

        logger.info("Coinbase API client initialized")

    def _generate_jwt(self, method: str, path: str) -> str:
        """Generate JWT token for authentication"""
        try:
            # Load private key
            private_key = serialization.load_pem_private_key(
                self.signing_key.encode(),
                password=None
            )

            # Create JWT URI (method + host + path)
            uri = f"{method} api.coinbase.com{path}"

            # Create JWT payload
            current_time = int(time.time())
            payload = {
                'sub': self.api_key,
                'iss': 'coinbase-cloud',
                'nbf': current_time,
                'exp': current_time + 120,
                'uri': uri
            }

            # Generate JWT token
            token = jwt.encode(
                payload,
                private_key,
                algorithm='ES256',
                headers={'kid': self.api_key, 'nonce': str(current_time)}
            )

            return token

        except Exception as e:
            raise Exception(f"Failed to generate JWT: {e}")

    def _make_request(self, method: str, path: str, json_data: Optional[Dict] = None) -> Dict:
        """Make authenticated request to Coinbase API"""
        token = self._generate_jwt(method, path)

        headers = {
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json'
        }

        url = f"{self.base_url}{path}"

        try:
            if method == 'GET':
                response = requests.get(url, headers=headers, timeout=10)
            elif method == 'POST':
                response = requests.post(url, headers=headers, json=json_data, timeout=10)
            else:
                raise ValueError(f"Unsupported HTTP method: {method}")

            if response.status_code == 200:
                return response.json()
            else:
                error_msg = f"API request failed ({response.status_code}): {response.text}"
                logger.error(error_msg)
                return {'error': error_msg, 'status_code': response.status_code}

        except requests.exceptions.RequestException as e:
            logger.error(f"Request exception: {e}")
            return {'error': str(e)}

    def get_account_balance(self, currency: str = "USD") -> Optional[float]:
        """Get account balance for a currency"""
        try:
            response = self._make_request('GET', '/api/v3/brokerage/accounts')

            if 'error' in response:
                logger.error(f"Error fetching balance: {response['error']}")
                return None

            accounts = response.get('accounts', [])
            for account in accounts:
                if account.get('currency') == currency:
                    available_balance = account.get('available_balance', {})
                    return float(available_balance.get('value', 0))

            logger.warning(f"No {currency} account found")
            return None

        except Exception as e:
            logger.error(f"Exception fetching balance: {e}")
            return None

    def market_buy(self, product_id: str, usd_amount: float) -> Dict:
        """
        Place a market buy order

        Args:
            product_id: Trading pair (e.g., "BTC-USD")
            usd_amount: Amount in USD to spend

        Returns:
            Dict with success status and order details
        """
        try:
            # Round to 2 decimal places for Coinbase precision requirements
            usd_amount = round(usd_amount, 2)

            client_order_id = f"dump_buy_{product_id}_{int(datetime.now().timestamp())}"

            order_data = {
                "client_order_id": client_order_id,
                "product_id": product_id,
                "side": "BUY",
                "order_configuration": {
                    "market_market_ioc": {
                        "quote_size": str(usd_amount)
                    }
                }
            }

            logger.info(f"Placing market BUY: {product_id} for ${usd_amount:.2f}")
            response = self._make_request('POST', '/api/v3/brokerage/orders', json_data=order_data)

            if 'error' in response:
                logger.error(f"Buy order failed: {response['error']}")
                return {'success': False, 'error': response['error']}

            # Log the actual response for debugging
            logger.info(f"Coinbase API response: {response}")

            # Extract order_id - match working telegram bot implementation
            order_id = None
            if response.get('success') and 'success_response' in response:
                order_id = response['success_response'].get('order_id')
                logger.info(f"Extracted order_id from success_response: {order_id}")

            if not order_id:
                order_id = response.get('order_id', 'unknown')
                logger.info(f"Fallback order_id from root: {order_id}")

            if order_id == 'unknown' or not order_id:
                logger.error(f"Could not extract order_id. Response keys: {list(response.keys())}")
                return {
                    'success': False,
                    'error': 'Could not extract order_id',
                    'raw_response': response
                }

            logger.info(f"✅ Buy order placed: {order_id}")
            return {
                'success': True,
                'order_id': order_id,
                'product_id': product_id,
                'usd_amount': usd_amount
            }

        except Exception as e:
            logger.error(f"Exception placing buy order: {e}")
            return {'success': False, 'error': str(e)}

    def market_sell(self, product_id: str, base_amount: float) -> Dict:
        """
        Place a market sell order

        Args:
            product_id: Trading pair (e.g., "BTC-USD")
            base_amount: Amount of base currency to sell (e.g., BTC quantity)

        Returns:
            Dict with success status and order details
        """
        try:
            client_order_id = f"dump_sell_{product_id}_{int(datetime.now().timestamp())}"

            order_data = {
                "client_order_id": client_order_id,
                "product_id": product_id,
                "side": "SELL",
                "order_configuration": {
                    "market_market_ioc": {
                        "base_size": str(base_amount)
                    }
                }
            }

            logger.info(f"Placing market SELL: {base_amount:.6f} of {product_id}")
            response = self._make_request('POST', '/api/v3/brokerage/orders', json_data=order_data)

            if 'error' in response:
                logger.error(f"Sell order failed: {response['error']}")
                return {'success': False, 'error': response['error']}

            # Log the actual response for debugging
            logger.info(f"Coinbase API response: {response}")

            # Extract order_id - match working telegram bot implementation
            order_id = None
            if response.get('success') and 'success_response' in response:
                order_id = response['success_response'].get('order_id')
                logger.info(f"Extracted order_id from success_response: {order_id}")

            if not order_id:
                order_id = response.get('order_id', 'unknown')
                logger.info(f"Fallback order_id from root: {order_id}")

            if order_id == 'unknown' or not order_id:
                logger.error(f"Could not extract order_id. Response keys: {list(response.keys())}")
                return {
                    'success': False,
                    'error': 'Could not extract order_id',
                    'raw_response': response
                }

            logger.info(f"✅ Sell order placed: {order_id}")
            return {
                'success': True,
                'order_id': order_id,
                'product_id': product_id,
                'base_amount': base_amount
            }

        except Exception as e:
            logger.error(f"Exception placing sell order: {e}")
            return {'success': False, 'error': str(e)}

    def get_current_price(self, product_id: str) -> Optional[float]:
        """Get current market price for a product"""
        try:
            path = f"/api/v3/brokerage/products/{product_id}"
            response = self._make_request('GET', path)

            if 'error' in response:
                return None

            price = response.get('price')
            if price:
                return float(price)

            return None

        except Exception as e:
            logger.error(f"Exception fetching price: {e}")
            return None
