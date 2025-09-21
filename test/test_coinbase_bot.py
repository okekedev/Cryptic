import pytest
import requests_mock
import json
from coinbase_trade_bot import CoinbaseTradeBot

def test_bot_initialization():
    bot = CoinbaseTradeBot()
    assert bot.base_url == BASE_URL
    assert bot.config['max_position_percentage'] == 100
    assert bot.config['min_trade_usd'] == 10.00
    assert bot.config['reserve_percentage'] == 1.0
    assert bot.config['defualt_order_type'] == 'market_market_ioc'
    
    custom_config = {
        'max_position_percentage': 50,
        'min_trade_usd': 20.00,
        'reserve_percentage': 2.0,
        'default_order_type': 'limit_limit_gtc ',
    }
    bot_custom = CoinbaseTradeBot(config=custom_config)
    assert bot_custom.config == custom_config
    print("Bot initialization tests passed.")

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
    "has_nest": False, 
    "cursor": ""
}

