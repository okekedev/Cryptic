# Multi-stage Dockerfile for React app

# Stage 1: Development environment
FROM node:20-alpine AS development
WORKDIR /app

# Copy package files
COPY package*.json ./

# Install dependencies
RUN npm ci

# Copy all source files
COPY . .

# Expose port 3000 for development server
EXPOSE 3000

# Start development server
CMD ["npm", "start"]

# Stage 2: Build stage
FROM node:20-alpine AS builder
WORKDIR /app

# Copy package files
COPY package*.json ./

# Install dependencies (including devDependencies needed for build)
RUN npm ci

# Copy all source files
COPY . .

# Build the app
RUN npm run build

# Stage 3: Production stage with Nginx
FROM nginx:stable-alpine AS production
WORKDIR /usr/share/nginx/html

# Remove default nginx static assets
RUN rm -rf ./*

# Copy built assets from builder stage
COPY --from=builder /app/build .

# Copy custom nginx config if needed
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Expose port 80
EXPOSE 80

# Start nginx
CMD ["nginx", "-g", "daemon off;"]