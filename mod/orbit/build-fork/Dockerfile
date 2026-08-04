# Build on mod:latest which already has Rust 1.93, Node 20, build-essential, libssl-dev
FROM mod:latest

# Create node user (mod:latest runs as root)
RUN useradd -u 1000 -m -s /bin/bash node \
    && mkdir -p /home/node/.claude /home/node/.mod/build-fork \
    && chown -R node:node /home/node

# Install the Claude Code CLI globally so the Rust API can spawn it.
# Without this, jobs.rs falls back to bare "claude" on PATH; the inherited
# PATH includes /root/.cargo/bin which the node (uid 1000) user can't
# traverse, producing a misleading "Permission denied (os error 13)".
RUN npm install -g @anthropic-ai/claude-code \
    && which claude \
    && claude --version

# Build Rust API (uses vendored deps — no network needed)
WORKDIR /app
COPY src/api/ /app/src/api/
RUN cd /app/src/api \
    && export OPENSSL_LIB_DIR=/usr/lib/$(uname -m)-linux-gnu OPENSSL_INCLUDE_DIR=/usr/include \
    && cargo build --release \
    && mkdir -p /app/bin \
    && cp target/release/build-fork-jobs /app/bin/build-fork-jobs \
    && rm -rf target vendor

# Copy Next.js App (with node_modules pre-installed)
COPY src/app/ /app/src/app/
ENV NEXT_PUBLIC_BASE_PATH=/build-fork
RUN cd /app/src/app && npx next build

# Entrypoint
COPY docker-entrypoint.sh /app/docker-entrypoint.sh
RUN chmod +x /app/docker-entrypoint.sh /app/bin/build-fork-jobs

ARG BUILD_FORK_CID=""
ARG BUILD_FORK_TIME=""
ENV BUILD_FORK_CID=${BUILD_FORK_CID} BUILD_FORK_TIME=${BUILD_FORK_TIME}

WORKDIR /app
ENTRYPOINT ["/app/docker-entrypoint.sh"]
