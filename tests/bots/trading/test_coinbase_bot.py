import pytest
import requests_mock
import json
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../../bots/trading')))

from coinbase_trade_bot import CoinbaseTradeBot, BASE_URL

def test_bot_initialization():
    bot = CoinbaseTradeBot()
    assert bot.base_url == BASE_URL
    assert bot.config['max_position_percentage'] == 100
    assert bot.config['min_trade_usd'] == 10.00
    assert bot.config['reserve_percentage'] == 1.0
    assert bot.config['default_order_type'] == 'market_market_ioc'
    
    custom_config = {
        'max_position_percentage': 50,
        'min_trade_usd': 20.00,
        'reserve_percentage': 2.0,
        'default_order_type': 'limit_limit_gtc',
    }
    bot_custom = CoinbaseTradeBot(config=custom_config)
    assert bot_custom.config == custom_config
    print('Bot initialization tests passed.')
    
def test_check_account_balances():
    bot = CoinbaseTradeBot()
    
    with requests_mock.Mocker() as m:
        
        m.get(f"{BASE_URL}/accounts", json=MOCK_ACCOUNTS_RESPONSE)
        
        balances = bot.check_account_balances()
        assert 'accounts' in balances
        assert len(balances['accounts']) == 2
        assert balances['accounts'][0]['currency'] == 'USD'
        assert balances['accounts'][0]['available_balance']['value'] == '1000.00'
        
def _test_get_account_balance_for_currency():
    bot = CoinbaseTradeBot()
    
    with requests_mock.Mocker() as m:
        m.get(f"{BASE_URL}/accounts", json=MOCK_ACCOUNTS_RESPONSE)
        
        usd_balance, usd_uuid = bot.get_account_balance_for_currency('USD')
        assert usd_balance == 1000.00
        assert usd_uuid == "abc123-def456-789012"
        
        btc_balance, btc_uuid = bot.get_account_balance_for_currency('BTC')
        assert btc_balance == 0.05
        assert btc_uuid == "xyz789-uvw456-123456"
        
      
        eth_balance, eth_uuid = bot.get_account_balance_for_currency('ETH')
        assert eth_balance == 0.0
        assert eth_uuid is None
        
        print("✅ Get currency balance tests passed")

    test_get_account_balance_for_currency()
        
# test_coinbase_bot.py - Test 4
def test_calculate_trade_parameters():
    """Test trade parameter calculation with various scenarios"""
    bot = CoinbaseTradeBot(config={
        'max_position_percentage': 100,
        'min_trade_usd': 10.00,
        'reserve_percentage': 1.0,
        'default_order_type': 'market_market_ioc'
    })
    
    with requests_mock.Mocker() as m:
        m.get(f"{BASE_URL}/accounts", json=MOCK_ACCOUNTS_RESPONSE)
        
        # Test 1: Valid BUY order with 50% of balance
        params = bot.calculate_trade_parameters('BTC-USD', 'BUY', 50)
        assert params['validation_passed'] == True
        assert params['currency'] == 'USD'
        assert float(params['trade_amount']) == 495.00  # 50% of (1000 - 10 reserved)
        assert params['reserved_balance'] == 10.00
        assert 'quote_size' in params['order_params']
        
        # Test 2: Valid SELL order (no reserve for crypto)
        params = bot.calculate_trade_parameters('BTC-USD', 'SELL', 100)
        assert params['validation_passed'] == True
        assert params['currency'] == 'BTC'
        assert params['trade_amount'] == '0.05000000'
        assert params['reserved_balance'] == 0.0
        assert 'base_size' in params['order_params']
        
        # Test 3: Below minimum trade amount
        params = bot.calculate_trade_parameters('BTC-USD', 'BUY', 0.5)  # 0.5% of balance
        assert params['validation_passed'] == False
        assert 'below minimum' in params['message']
        
        # Test 4: Would violate reserve requirement
        params = bot.calculate_trade_parameters('BTC-USD', 'BUY', 99.5)
        assert params['validation_passed'] == False
        assert 'reserved balance' in params['message']
        
        # Test 5: No balance for currency
        params = bot.calculate_trade_parameters('ETH-USD', 'SELL', 50)
        assert params['validation_passed'] == False
        assert 'Insufficient ETH balance' in params['message']
        
        print("✅ Calculate trade parameters tests passed")

test_calculate_trade_parameters()
    
MOCK_ACCOUNTS_RESPONSE = {
    "accounts": [
        {
            "uuid": "abc123-def456-789012",
            "name": "USD Wallet",
            "currency": "USD",
            "available_balance": {
                "value": "1000.00",
                "currency": "USD"
            },
            "default": True,
            "active": True,
            "created_at": "2024-01-01T00:00:00Z",
            "updated_at": "2024-01-01T00:00:00Z",
            "type": "ACCOUNT_TYPE_FIAT"
        },
        {
            "uuid": "xyz789-uvw456-123456",
            "name": "BTC Wallet",
            "currency": "BTC",
            "available_balance": {
                "value": "0.05000000",
                "currency": "BTC"
            },
            "default": False,
            "active": True,
            "created_at": "2024-01-01T00:00:00Z",
            "updated_at": "2024-01-01T00:00:00Z",
            "type": "ACCOUNT_TYPE_CRYPTO"
        }
    ],
    "has_next": False,  # Fixed from "has_nest"
    "cursor": ""
}

if __name__ == "__main__":
    test_bot_initialization()
    