# Use Python 3.9 slim image
FROM python:3.9-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies directly (in case requirements.txt is missing)
RUN pip install --no-cache-dir \
    aiohttp==3.8.5 \
    docker==6.1.3

# Copy the application
COPY log_monitor.py .

# Create a non-root user to run the app
RUN useradd -m -u 1000 monitor && \
    chown -R monitor:monitor /app

# Switch to non-root user
USER monitor

# Run the application
CMD ["python", "-u", "log_monitor.py"]