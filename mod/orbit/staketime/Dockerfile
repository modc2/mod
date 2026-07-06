# ── Stage 1: Python API deps ────────────────────────────────────
FROM python:3.12-slim AS api-builder
WORKDIR /build
COPY src/api/requirements.txt* ./
RUN pip install --no-cache-dir --target /build/deps -r requirements.txt 2>/dev/null || true

# ── Stage 2: Build Next.js app ───────────────────────────────
FROM node:20-slim AS app-builder
WORKDIR /build
COPY src/app/package.json src/app/package-lock.json* ./
RUN npm ci --no-audit --no-fund
COPY src/app/ ./
ENV NEXT_PUBLIC_API_URL=/api/staketime
ENV NEXT_PUBLIC_BASE_PATH=/staketime
RUN npm run build

# ── Runtime ─────────────────────────────────────────────────────
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates  nodejs npm \
    && ARCH=$(dpkg --print-architecture) \
    && curl -fsSL "https://caddyserver.com/api/download?os=linux&arch=${ARCH}" -o /usr/local/bin/caddy \
    && chmod +x /usr/local/bin/caddy \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY config.json .

# Python API
COPY src/api/ /app/api/
COPY --from=api-builder /build/deps /app/deps
ENV PYTHONPATH=/app/deps:/app

# Next.js app
COPY --from=app-builder /build/.next /app/src/app/.next
COPY --from=app-builder /build/node_modules /app/src/app/node_modules
COPY --from=app-builder /build/package.json /app/src/app/package.json
COPY --from=app-builder /build/next.config.* /app/src/app/

COPY docker-entrypoint.sh /app/docker-entrypoint.sh
RUN chmod +x /app/docker-entrypoint.sh

ENV GATEWAY_PORT=8850
EXPOSE 8850

ENTRYPOINT ["/app/docker-entrypoint.sh"]

