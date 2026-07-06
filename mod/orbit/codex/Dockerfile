# ── Stage 1: Build Rust API ──────────────────────────────────────
FROM rust:slim AS api-builder
RUN apt-get update && apt-get install -y --no-install-recommends pkg-config libssl-dev && rm -rf /var/lib/apt/lists/*
WORKDIR /build
COPY src/api/ .
RUN cargo build --release

# ── Stage 2: Install OpenAI codex CLI ────────────────────────────
FROM node:20-slim AS codex-cli
RUN npm install -g @openai/codex

# ── Stage 3: Runtime ─────────────────────────────────────────────
FROM node:20-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    jq curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Non-root user for spawned codex CLI (mirrors claude container pattern)
RUN useradd -m -u 1000 -s /bin/bash node 2>/dev/null || true

WORKDIR /app
COPY config.json .
COPY --from=api-builder /build/target/release/codex-jobs /app/bin/codex-jobs
COPY --from=codex-cli /usr/local/lib/node_modules/@openai/codex /app/codex-cli
RUN ln -s /app/codex-cli/bin/codex.js /usr/local/bin/codex && chmod +x /app/codex-cli/bin/codex.js
COPY docker-entrypoint.sh /app/docker-entrypoint.sh
RUN chmod +x /app/docker-entrypoint.sh

# Owner-managed key store mount target
RUN mkdir -p /home/node/.mod/codex && chown -R node:node /home/node/.mod

ENV API_PORT=8830
ENV APP_PORT=8831
EXPOSE 8830 8831

ENTRYPOINT ["/app/docker-entrypoint.sh"]
