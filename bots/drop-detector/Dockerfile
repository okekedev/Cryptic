FROM python:3.11-slim

WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY drop_detector_bot.py .

# Create data directory
RUN mkdir -p /app/data

CMD ["python", "-u", "drop_detector_bot.py"]
