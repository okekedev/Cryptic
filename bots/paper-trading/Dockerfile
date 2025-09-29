FROM python:3.11-slim

WORKDIR /app

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy bot code
COPY paper_trading_bot.py .

# Create data directory
RUN mkdir -p /app/data

# Run the bot
CMD ["python", "-u", "paper_trading_bot.py"]