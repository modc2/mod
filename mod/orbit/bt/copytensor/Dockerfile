# ── Stage 1: Build Rust API ──────────────────────────────────────
# subxt + transitive crates (ar_archive_writer, constant_time_eq) pull in
# nightly-stabilized features up through 1.93 — pin a recent stable.
FROM rust:1.93-slim AS api-builder

WORKDIR /build
COPY src/api/ .
RUN apt-get update && apt-get install -y pkg-config libssl-dev && rm -rf /var/lib/apt/lists/*
RUN cargo build --release

# ── Stage 2: Build Next.js frontend ─────────────────────────────
FROM node:20-slim AS app-builder

WORKDIR /build
COPY src/app/ .
RUN npm install --no-audit --no-fund
RUN npm run build

# ── Stage 3: Runtime ─────────────────────────────────────────────
FROM debian:bookworm-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends ca-certificates libssl3 curl && \
    rm -rf /var/lib/apt/lists/*

# Install Node.js for Next.js runtime
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y --no-install-recommends nodejs && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy Rust API binary
COPY --from=api-builder /build/target/release/copytensor-api /app/copytensor-api

# Copy Next.js app
COPY --from=app-builder /build/.next /app/frontend/.next
COPY --from=app-builder /build/node_modules /app/frontend/node_modules
COPY --from=app-builder /build/package.json /app/frontend/package.json
COPY --from=app-builder /build/next.config.js /app/frontend/next.config.js
COPY --from=app-builder /build/public /app/frontend/public

# Copy config
COPY config.json /app/config.json

# Data directory for SQLite
RUN mkdir -p /app/src/data

ENV PORT=50150
ENV APP_PORT=3150

EXPOSE 50150 3150

COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

ENTRYPOINT ["/app/entrypoint.sh"]
