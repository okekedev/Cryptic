FROM python:3.11-slim

WORKDIR /app

# Install minimal system dependencies (curl for healthcheck)
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY websocket-service/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY websocket-service/*.py ./
COPY websocket-service/templates ./templates/

# Create data directory
RUN mkdir -p /app/data

# Set environment
ENV PYTHONUNBUFFERED=1

# Expose port
EXPOSE 8000

# Run the app
CMD ["python", "main.py"]
