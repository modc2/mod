# ── Stage 1: Build Rust API ──────────────────────────────────────
FROM rust:slim AS api-builder
RUN apt-get update && apt-get install -y --no-install-recommends pkg-config libssl-dev && rm -rf /var/lib/apt/lists/*
WORKDIR /build
COPY Cargo.toml Cargo.lock* ./
COPY src/ ./src/
RUN cargo build --release

# ── Stage 2: Build Next.js app ──────────────────────────────────
FROM node:20-slim AS app-builder
WORKDIR /build
COPY app/package.json app/package-lock.json* ./
RUN npm ci --no-audit --no-fund
COPY app/ ./
ENV NEXT_PUBLIC_API_URL=/api/multisig
ENV NEXT_PUBLIC_BASE_PATH=/multisig
RUN npm run build

# ── Stage 3: Runtime ────────────────────────────────────────────
FROM node:20-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    jq curl ca-certificates \
    && ARCH=$(dpkg --print-architecture) \
    && curl -fsSL "https://caddyserver.com/api/download?os=linux&arch=${ARCH}" -o /usr/local/bin/caddy \
    && chmod +x /usr/local/bin/caddy \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY config.json .

# Rust API binary
COPY --from=api-builder /build/target/release/multisig /app/bin/multisig

# Next.js production build + runtime deps
COPY --from=app-builder /build/.next /app/app/.next
COPY --from=app-builder /build/node_modules /app/app/node_modules
COPY --from=app-builder /build/package.json /app/app/package.json
COPY --from=app-builder /build/next.config.* /app/app/

COPY docker-entrypoint.sh /app/docker-entrypoint.sh
RUN chmod +x /app/docker-entrypoint.sh

ENV GATEWAY_PORT=3100
EXPOSE 3100

ENTRYPOINT ["/app/docker-entrypoint.sh"]
