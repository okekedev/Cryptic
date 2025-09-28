FROM python:3.11-slim

WORKDIR /app

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy all bot files including trading modules
COPY *.py .

# Set environment variables
ENV PYTHONUNBUFFERED=1

# Run the bot
CMD ["python", "telegram_bot.py"]