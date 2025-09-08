FROM node:18-alpine

WORKDIR /app

# Copy package.json (and package-lock.json if it exists)
COPY package*.json ./

# Install dependencies
# Use npm install instead of npm ci to handle missing package-lock.json
RUN npm install --only=production && npm cache clean --force

# Copy application code
COPY src/ ./src/

# Create data directory
RUN mkdir -p /app/data

# Expose port
EXPOSE 5000

# Run the application
CMD ["node", "src/index.js"]