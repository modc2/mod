# syntax=docker/dockerfile:1.4
# ── Stage 1: Build Rust API ──────────────────────────────────────
FROM rust:slim AS api-builder
RUN apt-get update && apt-get install -y --no-install-recommends pkg-config libssl-dev && rm -rf /var/lib/apt/lists/*
# Make crates.io fetches resilient to flaky networks.
ENV CARGO_NET_RETRY=10
ENV CARGO_HTTP_TIMEOUT=180
ENV CARGO_HTTP_MULTIPLEXING=false
ENV CARGO_REGISTRIES_CRATES_IO_PROTOCOL=sparse
WORKDIR /build
COPY src/api/ .
# Reference strats (base.py / copytrader.py / example_ev_strat.py)
# get embedded into the binary via include_str! so the /user-strats/
# template route can serve them. Path matches what dev sees: from
# src/routes.rs, `../../strats/<file>.py` resolves to /strats/<file>.py
# inside the build context (and src/strats/ outside Docker).
COPY src/strats/ /strats/
# Cache mounts: the cargo registry/git caches and the build target dir
# persist across `docker build` runs. Without these, every rebuild
# re-downloads the entire dependency graph (~10+ min) — a single source
# edit then triggers a fresh full build. With them, rebuilds reuse cached
# crates and incremental compilation artifacts, dropping to ~30s for
# small edits.
RUN --mount=type=cache,target=/usr/local/cargo/registry \
    --mount=type=cache,target=/usr/local/cargo/git \
    --mount=type=cache,target=/build/target \
    cargo build --release && \
    cp /build/target/release/polymarket-api /tmp/polymarket-api
# Move the binary outside the target cache mount so the next stage can
# COPY it (cache mounts vanish after RUN completes).
RUN cp /tmp/polymarket-api /build/polymarket-api

# ── Stage 2: Build Next.js app ──────────────────────────────────
FROM node:20-slim AS app-builder
WORKDIR /build
COPY src/app/package.json src/app/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY src/app/app/ ./app/
COPY src/app/next.config.mjs src/app/tsconfig.json src/app/tailwind.config.js src/app/postcss.config.js ./
COPY src/strats/ /strats/
ENV NEXT_PUBLIC_API_URL=/api/polymarket
ENV NEXT_PUBLIC_BASE_PATH=/polymarket
ENV POLYMARKET_API_URL=http://localhost:50091
ARG STRAT_HMAC_SECRET=polymarket_strats_default_hmac_key_2024
ENV NEXT_PUBLIC_STRAT_HMAC_SECRET=${STRAT_HMAC_SECRET}
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

# Config
COPY config.json .

# Rust API binary (copied out of the cache-mounted target/ to a stable path)
COPY --from=api-builder /build/polymarket-api /app/bin/polymarket-api

# Next.js production build + runtime deps
COPY --from=app-builder /build/.next /app/src/app/.next
COPY --from=app-builder /build/node_modules /app/src/app/node_modules
COPY --from=app-builder /build/package.json /app/src/app/package.json
COPY --from=app-builder /build/next.config.mjs /app/src/app/next.config.mjs
COPY --from=app-builder /build/app/ /app/src/app/app/

# Entrypoint
COPY docker-entrypoint.sh /app/docker-entrypoint.sh
RUN chmod +x /app/docker-entrypoint.sh

ENV GATEWAY_PORT=3000
EXPOSE 3000

ENTRYPOINT ["/app/docker-entrypoint.sh"]
