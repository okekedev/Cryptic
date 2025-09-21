FROM python:3.10-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy bot code
COPY . .

# Run the trading bot
CMD ["python", "-u", "coinbase_trading_bot.py"]