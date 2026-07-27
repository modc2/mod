# ── Stage 1: FastAPI proxy deps ──────────────────────────────────
FROM python:3.12-slim AS api-builder
WORKDIR /build
COPY src/api/requirements.txt ./
RUN pip install --no-cache-dir --target /build/deps -r requirements.txt

# ── Stage 2: Build Next.js app (when present) ────────────────────
FROM node:20-slim AS app-builder
WORKDIR /build
COPY src/app/ ./
RUN if [ -f package.json ]; then npm ci --no-audit --no-fund && npm run build || echo "no app build script"; else echo "no app/"; fi

# ── Stage 3: Runtime ─────────────────────────────────────────────
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates nodejs npm && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY config.json .
COPY src/api/ /app/src/api/
COPY --from=api-builder /build/deps /app/deps
ENV PYTHONPATH=/app/deps

COPY --from=app-builder /build /app/src/app

COPY docker-entrypoint.sh /app/docker-entrypoint.sh
RUN chmod +x /app/docker-entrypoint.sh

# Owner-managed key store mount target (per-wallet ciphertexts)
RUN mkdir -p /home/node/.mod/dev

ENV API_PORT=8870
ENV APP_PORT=8871
EXPOSE 8870 8871

ENTRYPOINT ["/app/docker-entrypoint.sh"]
