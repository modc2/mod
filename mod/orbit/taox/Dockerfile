# ── Stage 1: Build Rust API ──────────────────────────────────────
FROM rust:slim AS api-builder
RUN apt-get update && apt-get install -y --no-install-recommends pkg-config libssl-dev && rm -rf /var/lib/apt/lists/*
WORKDIR /build
COPY src/api/Cargo.toml src/api/Cargo.lock ./
COPY src/api/src ./src
RUN cargo build --release

# ── Stage 2: Build Next.js app ──────────────────────────────────
FROM node:20-slim AS app-builder
WORKDIR /build
COPY src/app/package.json ./
RUN npm install --no-audit --no-fund
COPY src/app/app/ ./app/
COPY src/app/next.config.js src/app/tsconfig.json ./
ENV NEXT_PUBLIC_BASE_PATH=/taox
RUN npm run build

# ── Stage 3: Caddy binary ───────────────────────────────────────
FROM caddy:2-alpine AS caddy-bin

# ── Stage 4: Runtime ────────────────────────────────────────────
FROM node:20-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Caddy
COPY --from=caddy-bin /usr/bin/caddy /usr/local/bin/caddy
COPY Caddyfile /etc/caddy/Caddyfile

# Config
COPY config.json .

# Rust API binary
COPY --from=api-builder /build/target/release/taox-api /app/bin/taox-api

# Next.js production build + runtime deps
COPY --from=app-builder /build/.next /app/src/app/.next
COPY --from=app-builder /build/node_modules /app/src/app/node_modules
COPY --from=app-builder /build/package.json /app/src/app/package.json
COPY --from=app-builder /build/next.config.js /app/src/app/next.config.js
COPY --from=app-builder /build/app/ /app/src/app/app/

# Entrypoint
COPY docker-entrypoint.sh /app/docker-entrypoint.sh
RUN chmod +x /app/docker-entrypoint.sh

# APP_PORT is the only exposed port (Caddy front).
# API_PORT and APP_INTERNAL_PORT are in-container only.
ENV APP_PORT=3870
ENV API_PORT=8870
ENV APP_INTERNAL_PORT=3871
EXPOSE 3870

ENTRYPOINT ["/app/docker-entrypoint.sh"]
