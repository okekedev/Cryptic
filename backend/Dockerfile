FROM node:20-alpine

WORKDIR /app

COPY package*.json ./
RUN npm install

COPY . .

WORKDIR /app/frontend
RUN npm install --legacy-peer-deps && npm run build || (echo "Frontend build failed, check logs" && exit 1)
WORKDIR /app
RUN cp -r frontend/build ./public || echo "Failed to copy build"

EXPOSE 3000

CMD ["node", "src/index.js"]