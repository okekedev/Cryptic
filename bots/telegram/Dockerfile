# Stage 1: Build Telegram Bot API server
FROM ubuntu:22.04 AS builder

RUN apt-get update && apt-get install -y \
    git \
    cmake \
    g++ \
    make \
    libssl-dev \
    zlib1g-dev \
    gperf \
    && rm -rf /var/lib/apt/lists/*

RUN git clone --recursive https://github.com/tdlib/telegram-bot-api.git /telegram-bot-api

WORKDIR /telegram-bot-api
RUN mkdir build && cd build \
    && cmake -DCMAKE_BUILD_TYPE=Release ..
RUN cd build \
    && cmake --build . --config Release --target install

# Stage 2: Create runtime image
FROM ubuntu:22.04

RUN apt-get update && apt-get install -y \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /usr/local/bin/telegram-bot-api /usr/local/bin/telegram-bot-api

WORKDIR /var/lib/telegram-bot-api

ENV TELEGRAM_LOCAL=true
ENV TELEGRAM_API_ID=""
ENV TELEGRAM_API_HASH=""
ENV TELEGRAM_HTTP_PORT=8081

CMD ["telegram-bot-api", "--api-id=${TELEGRAM_API_ID}", "--api-hash=${TELEGRAM_API_HASH}", "--local", "--http-port=${TELEGRAM_HTTP_PORT}"]