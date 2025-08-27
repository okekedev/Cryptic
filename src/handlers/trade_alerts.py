import logging
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone
import os
import jinja2  # Add to requirements.txt: jinja2>=3.1.4

logger = logging.getLogger(__name__)

# State to track last logged data per product (to avoid duplicates)
last_logged = {}  # {product_id: {'timestamp': datetime, 'price': str, 'high_24h': str, 'low_24h': str, 'volume_24h': str, 'percent_change_24h': str}}

# Jinja2 template for index.html (Craigslist-style: simple table, sans-serif, minimal)
TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta http-equiv="refresh" content="5">  <!-- Auto-refresh every 5 seconds -->
    <title>Crypto Monitor</title>
    <style>
        body { font-family: arial, sans-serif; background-color: #fff; color: #000; margin: 20px; }
        h1 { font-size: 18pt; color: #000; }
        table { width: 100%; border-collapse: collapse; border: 1px solid #ccc; }
        th, td { padding: 8px; text-align: left; border: 1px solid #ccc; }
        th { background-color: #eee; font-weight: bold; }
        .positive { color: green; }
        .negative { color: red; }
        a { color: #00f; text-decoration: underline; }
        .footer { font-size: 10pt; color: #999; margin-top: 20px; }
    </style>
</head>
<body>
    <h1>Crypto Prices - Real-Time from Coinbase</h1>
    <table>
        <tr>
            <th>Product</th>
            <th>Live Price</th>
            <th>24hr High</th>
            <th>24hr Low</th>
            <th>24hr Volume</th>
            <th>24hr % Change</th>
            <th>Last Updated</th>
        </tr>
        {% for product, data in products.items() %}
        <tr>
            <td>{{ product }}</td>
            <td>${{ data.price }}</td>
            <td>${{ data.high_24h }}</td>
            <td>${{ data.low_24h }}</td>
            <td>{{ data.volume_24h }} {{ product.split('-')[0] }}</td>
            <td class="{% if data.percent_change_24h.startswith('-') %}negative{% else %}positive{% endif %}">{{ data.percent_change_24h }}</td>
            <td>{{ data.timestamp }}</td>
        </tr>
        {% endfor %}
    </table>
    <div class="footer">Data via Coinbase WebSocket. <a href="https://www.coinbase.com">More info</a></div>
</body>
</html>
"""

# Setup Jinja environment
env = jinja2.Environment()

def render_html(products_data):
    """
    Render the HTML file with current data, styled like Craigslist.
    Writes to project_root/index.html.
    """
    template = env.from_string(TEMPLATE)
    html_content = template.render(products=products_data)
    with open('index.html', 'w') as f:
        f.write(html_content)
    logger.info("Updated index.html with latest data.")

def handle_ticker(message):
    """
    Handle ticker messages.
    Extracts data, deduplicates, logs, and updates HTML if new.
    """
    product_id = message.get('product_id', 'N/A')
    price = message.get('price', 'N/A')
    high_24h = message.get('high_24h', 'N/A')
    low_24h = message.get('low_24h', 'N/A')
    volume_24h = message.get('volume_24h', 'N/A')
    open_24h = message.get('open_24h', 'N/A')
    time_str = message.get('time', 'N/A')
    
    # Parse timestamp
    try:
        current_time = datetime.fromisoformat(time_str.replace('Z', '+00:00'))
    except ValueError:
        current_time = datetime.now(timezone.utc)
        logger.debug(f"Invalid timestamp in ticker: {time_str}; using current UTC time.")
    
    # Calculate 24hr % change if possible
    percent_change_24h = 'N/A'
    try:
        price_dec = Decimal(price)
        open_dec = Decimal(open_24h)
        if open_dec != 0:
            percent_change_24h = ((price_dec - open_dec) / open_dec) * Decimal('100')
            percent_change_24h = f"{percent_change_24h:.2f}%"
    except (InvalidOperation, ValueError, TypeError):
        logger.error(f"Invalid values for % change calc: price={price}, open_24h={open_24h}")
    
    # Check for duplication
    if product_id in last_logged:
        last = last_logged[product_id]
        time_diff = (current_time - last['timestamp']).total_seconds()
        if (
            time_diff < 1
            or (
                price == last['price']
                and high_24h == last['high_24h']
                and low_24h == last['low_24h']
                and volume_24h == last['volume_24h']
                and percent_change_24h == last['percent_change_24h']
            )
        ):
            logger.debug(f"Skipping duplicate/rapid ticker for {product_id}")
            return
    
    # Log the data
    logger.info(
        f"Ticker for {product_id} ({time_str}): "
        f"Live Price: {price}, "
        f"24hr High: {high_24h}, "
        f"24hr Low: {low_24h}, "
        f"24hr Volume: {volume_24h}, "
        f"24hr % Change: {percent_change_24h}"
    )
    
    # Update last logged with new data
    last_logged[product_id] = {
        'timestamp': current_time,
        'price': price,
        'high_24h': high_24h,
        'low_24h': low_24h,
        'volume_24h': volume_24h,
        'percent_change_24h': percent_change_24h
    }
    
    # Render updated HTML (pass all products' data)
    products_data = {pid: {**data, 'timestamp': data['timestamp'].isoformat()} for pid, data in last_logged.items()}
    render_html(products_data)