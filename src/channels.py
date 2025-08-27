import yaml

def get_product_ids():
    with open('config/config.yaml', 'r') as f:
        config = yaml.safe_load(f)
    return config.get('products', ['BTC-USD', 'ETH-USD'])

# Define public products
def get_product_ids():
    return ['BTC-USD', 'ETH-USD']  # Configurable list of products

# Define public channels only
def get_channels():
    return [
        'ticker',
    ]