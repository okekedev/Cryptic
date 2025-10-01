FROM python:3.11-slim

WORKDIR /app

# Copy requirements and install dependencies (without TA-Lib for now)
COPY requirements.txt .
RUN pip install --no-cache-dir python-socketio==5.9.0 requests==2.31.0 websocket-client==1.6.4 numpy==1.24.3

# Note: TA-Lib indicators disabled - bot will use time and price-based exits only

# Copy bot code
COPY dump_trading_bot.py .

# Create data directory
RUN mkdir -p /app/data

# Run the bot
CMD ["python", "-u", "dump_trading_bot.py"]
