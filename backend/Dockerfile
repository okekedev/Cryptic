FROM node:18-alpine

WORKDIR /app

# Install Python and pip for multi-connection WebSocket handler
RUN apk add --no-cache python3 py3-pip python3-dev gcc musl-dev && \
    ln -sf python3 /usr/bin/python

# Copy package.json (and package-lock.json if it exists)
COPY package*.json ./

# Install Node.js dependencies
# Use npm install instead of npm ci to handle missing package-lock.json
RUN npm install --only=production && npm cache clean --force

# Copy application code
COPY src/ ./src/

# Install Python dependencies for enhanced WebSocket handler
COPY src/websocket-python/requirements_multi_ws.txt ./src/websocket-python/
RUN pip3 install --no-cache-dir --break-system-packages -r src/websocket-python/requirements_multi_ws.txt

# Create data directory
RUN mkdir -p /app/data

# Expose port
EXPOSE 5000

# Run the application with multi-connection support
CMD ["node", "src/index.js"]