FROM python:3.11-slim

WORKDIR /app

# Copy requirements and install dependencies (excluding TA-Lib which requires system libraries)
COPY requirements.txt .
RUN pip install --no-cache-dir python-socketio==5.9.0 requests==2.31.0 websocket-client==1.6.4 numpy==1.24.3 PyJWT==2.8.0 cryptography==41.0.3

# Note: TA-Lib excluded - requires gcc and ta-lib system libraries. Bot uses time and price-based exits only

# Copy bot code
COPY dump_trading_bot.py .
COPY coinbase_client.py .

# Create data directory
RUN mkdir -p /app/data

# Run the bot
CMD ["python", "-u", "dump_trading_bot.py"]
