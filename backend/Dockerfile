FROM node:18-alpine

WORKDIR /app

# Copy package files
COPY package*.json ./

# Install dependencies
RUN npm ci --only=production

# Copy application code
COPY src/ ./src/

# Expose port
EXPOSE 3000

# Run the application
CMD ["node", "src/index.js"]2