# Dockerfile
FROM node:18-alpine

# Install dumb-init for proper signal handling
RUN apk add --no-cache dumb-init

# Create app directory
WORKDIR /app

# Copy package files
COPY package*.json ./

# Install dependencies
RUN npm ci --only=production && npm cache clean --force

# Copy TypeScript config and source
COPY tsconfig.json ./
COPY src ./src
COPY public ./public

# Build TypeScript
RUN npm install -g typescript && \
    tsc && \
    npm uninstall -g typescript

# Create volume for persistent update tracking
VOLUME ["/app/.last-pair-update.json", "/app/src/constants/UsdPairs.ts"]

# Use non-root user
RUN addgroup -g 1001 -S nodejs && \
    adduser -S nodejs -u 1001
USER nodejs

# Expose port
EXPOSE 3000

# Use dumb-init to handle signals properly
ENTRYPOINT ["dumb-init", "--"]
CMD ["node", "dist/server.js"]