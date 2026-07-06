# mod store — FastAPI gateway + Next.js MetaMask/SIWE app
# Multi-stage: build the Next.js app, then a slim runtime with Python + Node.

# ── Stage 1: build Next.js app ──────────────────────────────────
# Build context is the repo root (see docker-compose.yml).
FROM node:20-slim AS app-builder
WORKDIR /build
COPY mod/core/store/app/package.json ./
RUN npm install --no-audit --no-fund --legacy-peer-deps
COPY mod/core/store/app/ ./
ENV NEXT_TELEMETRY_DISABLED=1
ENV NEXT_PUBLIC_API_URL=/api/store
RUN npm run build

# ── Stage 2: runtime ────────────────────────────────────────────
FROM python:3.11-slim AS runtime

# Node (for `next start`) + pm2 process manager, plus minimal OS deps for
# eth-keys / pysha3 builds.
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates gnupg build-essential libssl-dev pkg-config \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && npm install -g pm2 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps. Build context is the repo root.
# The mod package needs its root requirements (psutil etc. used at import time);
# the store API adds python-multipart + boto3 on top.
COPY requirements.txt /tmp/mod-root-requirements.txt
COPY mod/core/store/requirements.txt /tmp/store-requirements.txt
RUN pip install --no-cache-dir -r /tmp/mod-root-requirements.txt -r /tmp/store-requirements.txt

# Copy the mod package (api proxies into orbit/filecoin + orbit/hippius via `import mod`).
# Build context is the repo root. The mod package expects a root config.json
# one directory above its package dir (lib_path = dirname(mod_path)).
COPY config.json /opt/mod/config.json
COPY mod/__init__.py /opt/mod/mod/__init__.py
COPY mod/core/ /opt/mod/mod/core/
COPY mod/orbit/filecoin/ /opt/mod/mod/orbit/filecoin/
COPY mod/orbit/hippius/ /opt/mod/mod/orbit/hippius/
COPY mod/orbit/dstore/ /opt/mod/mod/orbit/dstore/
ENV PYTHONPATH=/opt/mod
ENV MOD_LIB_PATH=/opt/mod/mod

# Copy the store module bits the runtime needs.
COPY mod/core/store/api/ /app/api/
COPY mod/core/store/config.json /app/config.json
COPY mod/core/store/ecosystem.config.js /app/ecosystem.config.js
COPY mod/core/store/docker-entrypoint.sh /app/docker-entrypoint.sh
RUN chmod +x /app/docker-entrypoint.sh

# Copy the prebuilt Next.js app.
COPY --from=app-builder /build/.next /app/app/.next
COPY --from=app-builder /build/node_modules /app/app/node_modules
COPY --from=app-builder /build/package.json /app/app/package.json
COPY --from=app-builder /build/next.config.mjs /app/app/next.config.mjs
COPY --from=app-builder /build/src /app/app/src

# Runtime state.
RUN mkdir -p /data /tmp/store
VOLUME ["/data"]

ENV STORE_API_PORT=50150
ENV STORE_APP_PORT=50151
ENV STORE_MODE=prod
ENV NEXT_PUBLIC_API_URL="http://localhost:50150"

EXPOSE 50150 50151

ENTRYPOINT ["/app/docker-entrypoint.sh"]
