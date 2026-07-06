# ── Stage 1: Build Next.js app ───────────────────────────────
FROM node:20-slim AS app-builder
WORKDIR /build
COPY app/package.json app/package-lock.json* ./
RUN npm ci --no-audit --no-fund
COPY app/ ./
ENV NEXT_PUBLIC_API_URL=/api/dns
ENV NEXT_PUBLIC_BASE_PATH=/dns
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

# Next.js app
COPY --from=app-builder /build/.next /app/app/.next
COPY --from=app-builder /build/node_modules /app/app/node_modules
COPY --from=app-builder /build/package.json /app/app/package.json
COPY --from=app-builder /build/next.config.* /app/app/

COPY docker-entrypoint.sh /app/docker-entrypoint.sh
RUN chmod +x /app/docker-entrypoint.sh

ENV GATEWAY_PORT=5382
EXPOSE 5382

ENTRYPOINT ["/app/docker-entrypoint.sh"]

