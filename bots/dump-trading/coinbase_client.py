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
            logger.info(f"Found {len(accounts)} account(s) from Coinbase")

            # Log all accounts with balances > 0
            accounts_with_balance = []
            for account in accounts:
                currency_code = account.get('currency')
                available_balance = account.get('available_balance', {})
                balance_value = float(available_balance.get('value', 0))

                if balance_value > 0:
                    accounts_with_balance.append(f"{currency_code}: ${balance_value:,.2f}")
                    logger.info(f"  💰 {currency_code}: ${balance_value:,.2f}")

                if currency_code == currency:
                    logger.info(f"✅ Found {currency} account with balance: ${balance_value:,.2f}")
                    return balance_value

            if accounts_with_balance:
                logger.info(f"Accounts with balance: {', '.join(accounts_with_balance)}")

            logger.warning(f"No {currency} account found. Available currencies: {[a.get('currency') for a in accounts]}")
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
            # Get product details to determine correct precision
            product_details = self.get_product_details(product_id)
            base_increment = product_details.get('base_increment', 0.01)

            # Round to product's base_increment
            base_amount_rounded = self._round_to_increment(base_amount, base_increment)

            client_order_id = f"dump_sell_{product_id}_{int(datetime.now().timestamp())}"

            order_data = {
                "client_order_id": client_order_id,
                "product_id": product_id,
                "side": "SELL",
                "order_configuration": {
                    "market_market_ioc": {
                        "base_size": str(base_amount_rounded)
                    }
                }
            }

            logger.info(f"Placing market SELL: {base_amount_rounded} of {product_id}")
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

    def limit_buy(self, product_id: str, quote_amount: float, limit_price: float) -> Dict:
        """
        Place a limit buy order (lower fees - maker order)

        Args:
            product_id: Trading pair (e.g., "BTC-USD")
            quote_amount: Amount in quote currency (USD) to spend
            limit_price: Limit price to buy at

        Returns:
            Dict with success status and order details
        """
        try:
            # Get product specifications for proper rounding
            product_details = self.get_product_details(product_id)
            if not product_details:
                logger.error(f"Could not fetch product details for {product_id}")
                return {'success': False, 'error': 'Could not fetch product details'}

            # Calculate base size from quote amount and limit price
            base_size = quote_amount / limit_price

            # Round to product-specific increments
            base_increment = product_details['base_increment']
            quote_increment = product_details['quote_increment']

            base_size_str = self._round_to_increment(base_size, base_increment)
            limit_price_str = self._round_to_increment(limit_price, quote_increment)

            client_order_id = f"dump_limit_buy_{product_id}_{int(datetime.now().timestamp())}"

            order_data = {
                "client_order_id": client_order_id,
                "product_id": product_id,
                "side": "BUY",
                "order_configuration": {
                    "limit_limit_gtc": {
                        "base_size": base_size_str,
                        "limit_price": limit_price_str,
                        "post_only": True  # Maker-only orders for lower fees (~0.4% vs ~1.2%)
                    }
                }
            }

            logger.info(f"Placing LIMIT BUY: {base_size_str} {product_id} @ ${limit_price_str} (increment: {base_increment})")
            response = self._make_request('POST', '/api/v3/brokerage/orders', json_data=order_data)

            if 'error' in response:
                logger.error(f"Limit buy order failed: {response['error']}")
                return {'success': False, 'error': response['error']}

            logger.info(f"Coinbase API response: {response}")

            # Extract order_id
            order_id = None
            if response.get('success') and 'success_response' in response:
                order_id = response['success_response'].get('order_id')
                logger.info(f"Extracted order_id from success_response: {order_id}")

            if not order_id:
                order_id = response.get('order_id', 'unknown')
                logger.info(f"Fallback order_id from root: {order_id}")

            if order_id == 'unknown' or not order_id:
                logger.error(f"Could not extract order_id. Response keys: {list(response.keys())}")
                return {'success': False, 'error': 'Could not extract order_id', 'raw_response': response}

            logger.info(f"✅ Limit buy order placed: {order_id}")
            return {
                'success': True,
                'order_id': order_id,
                'product_id': product_id,
                'base_size': base_size_str,
                'limit_price': limit_price_str
            }

        except Exception as e:
            logger.error(f"Exception placing limit buy order: {e}")
            return {'success': False, 'error': str(e)}

    def limit_sell(self, product_id: str, base_amount: float, limit_price: float) -> Dict:
        """
        Place a limit sell order (lower fees - maker order)

        Args:
            product_id: Trading pair (e.g., "BTC-USD")
            base_amount: Amount of base currency to sell
            limit_price: Limit price to sell at

        Returns:
            Dict with success status and order details
        """
        try:
            # Get product specifications for proper rounding
            product_details = self.get_product_details(product_id)
            if not product_details:
                logger.error(f"Could not fetch product details for {product_id}")
                return {'success': False, 'error': 'Could not fetch product details'}

            # Round to product-specific increments
            base_increment = product_details['base_increment']
            quote_increment = product_details['quote_increment']

            base_amount_str = self._round_to_increment(base_amount, base_increment)
            limit_price_str = self._round_to_increment(limit_price, quote_increment)

            client_order_id = f"dump_limit_sell_{product_id}_{int(datetime.now().timestamp())}"

            order_data = {
                "client_order_id": client_order_id,
                "product_id": product_id,
                "side": "SELL",
                "order_configuration": {
                    "limit_limit_gtc": {
                        "base_size": base_amount_str,
                        "limit_price": limit_price_str,
                        "post_only": True  # Maker-only orders for lower fees (~0.4% vs ~1.2%)
                    }
                }
            }

            logger.info(f"Placing LIMIT SELL: {base_amount_str} {product_id} @ ${limit_price_str} (increment: {base_increment})")
            response = self._make_request('POST', '/api/v3/brokerage/orders', json_data=order_data)

            if 'error' in response:
                logger.error(f"Limit sell order failed: {response['error']}")
                return {'success': False, 'error': response['error']}

            logger.info(f"Coinbase API response: {response}")

            # Extract order_id
            order_id = None
            if response.get('success') and 'success_response' in response:
                order_id = response['success_response'].get('order_id')
                logger.info(f"Extracted order_id from success_response: {order_id}")

            if not order_id:
                order_id = response.get('order_id', 'unknown')
                logger.info(f"Fallback order_id from root: {order_id}")

            if order_id == 'unknown' or not order_id:
                logger.error(f"Could not extract order_id. Response keys: {list(response.keys())}")
                return {'success': False, 'error': 'Could not extract order_id', 'raw_response': response}

            logger.info(f"✅ Limit sell order placed: {order_id}")
            return {
                'success': True,
                'order_id': order_id,
                'product_id': product_id,
                'base_amount': base_amount_str,
                'limit_price': limit_price_str
            }

        except Exception as e:
            logger.error(f"Exception placing limit sell order: {e}")
            return {'success': False, 'error': str(e)}

    def get_order_status(self, order_id: str) -> Dict:
        """
        Get status of an order

        Args:
            order_id: The order ID to check

        Returns:
            Dict with order status details
        """
        try:
            path = f"/api/v3/brokerage/orders/historical/{order_id}"
            response = self._make_request('GET', path)

            if 'error' in response:
                return {'success': False, 'error': response['error']}

            order = response.get('order', {})
            status = order.get('status', 'UNKNOWN')
            filled_size = float(order.get('filled_size', 0))

            return {
                'success': True,
                'order_id': order_id,
                'status': status,  # OPEN, FILLED, CANCELLED, etc.
                'filled_size': filled_size,
                'order': order
            }

        except Exception as e:
            logger.error(f"Exception checking order status: {e}")
            return {'success': False, 'error': str(e)}

    def cancel_order(self, order_id: str) -> Dict:
        """
        Cancel an open order

        Args:
            order_id: The order ID to cancel

        Returns:
            Dict with cancellation result
        """
        try:
            path = f"/api/v3/brokerage/orders/batch_cancel"
            cancel_data = {"order_ids": [order_id]}

            response = self._make_request('POST', path, json_data=cancel_data)

            if 'error' in response:
                return {'success': False, 'error': response['error']}

            logger.info(f"✅ Order {order_id} cancelled")
            return {'success': True, 'order_id': order_id}

        except Exception as e:
            logger.error(f"Exception cancelling order: {e}")
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

    def get_product_details(self, product_id: str) -> Optional[Dict]:
        """Get product specifications including increment and size limits"""
        try:
            path = f"/api/v3/brokerage/products/{product_id}"
            response = self._make_request('GET', path)

            if 'error' in response:
                logger.error(f"Error fetching product details: {response['error']}")
                return None

            # Log full response for debugging
            logger.info(f"Product details for {product_id}: base_increment={response.get('base_increment')}, quote_increment={response.get('quote_increment')}")

            return {
                'base_increment': response.get('base_increment', '0.01'),
                'quote_increment': response.get('quote_increment', '0.01'),
                'base_min_size': response.get('base_min_size', '0'),
                'base_max_size': response.get('base_max_size', '999999999'),
                'quote_min_size': response.get('quote_min_size', '0'),
                'quote_max_size': response.get('quote_max_size', '999999999')
            }

        except Exception as e:
            logger.error(f"Exception fetching product details: {e}")
            return None

    def _round_to_increment(self, value: float, increment: str) -> str:
        """Round a value to the nearest increment"""
        try:
            from decimal import Decimal, ROUND_DOWN

            # Convert to Decimal for precise arithmetic
            inc_decimal = Decimal(str(increment))
            value_decimal = Decimal(str(value))

            # Round DOWN to nearest increment (floor)
            rounded = (value_decimal / inc_decimal).quantize(Decimal('1'), rounding=ROUND_DOWN) * inc_decimal

            # Normalize to remove trailing zeros, then convert to string
            result = str(rounded.normalize())

            logger.info(f"Rounding {value} to increment {increment}: {result}")

            return result
        except Exception as e:
            logger.error(f"Error rounding to increment: {e}")
            return str(round(value, 2))
