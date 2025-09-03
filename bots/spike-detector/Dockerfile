FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt .

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY spike_bot.py .

# Set environment variables
ENV PYTHONUNBUFFERED=1

# Run the bot
CMD ["python", "spike_bot.py"]