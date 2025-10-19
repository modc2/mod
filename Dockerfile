# CI image for ModNet monorepo workflows
# Includes: Nix (single-user), common build deps (Rust builds, Node builds), and utilities.

FROM debian:bookworm-slim

LABEL org.opencontainers.image.source="https://github.com/mod-chain/modsdk"
LABEL org.opencontainers.image.description="CI image for ModNet (Nix + build deps)"
LABEL org.opencontainers.image.licenses="Apache-2.0"

ENV DEBIAN_FRONTEND=noninteractive

# Base deps for builds and Nix install
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        xz-utils \
        bash \
        git \
        build-essential \
        pkg-config \
        libssl-dev \
        protobuf-compiler \
        cmake \
        clang \
        sudo \
    && rm -rf /var/lib/apt/lists/*

ENV USER=root
ENV NIX_INSTALLER_NO_MODIFY_PROFILE=1

# Install Nix (single-user) using Determinate Systems installer (robust in containers)
RUN set -eux; \
    curl -fsSL https://install.determinate.systems/nix -o /tmp/nix-installer.sh; \
    sh /tmp/nix-installer.sh install --no-confirm; \
    rm -f /tmp/nix-installer.sh

# Ensure nix is on PATH for both login and non-login shells
ENV PATH=/root/.nix-profile/bin:/nix/var/nix/profiles/default/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
ENV NIX_CONFIG=experimental-features\ =\ nix-command\ flakes
SHELL ["/bin/bash", "-lc"]

RUN nix --version || (echo "nix not found on PATH" && exit 1)

# Optional: Node 20 + pnpm preinstall to speed up JS tasks if needed outside Nix
# You can rely solely on Nix if preferred; keeping these for convenience
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get update \
    && apt-get install -y --no-install-recommends nodejs \
    && npm i -g pnpm \
    && rm -rf /var/lib/apt/lists/*

# Warm up nix by querying version (already done above)

WORKDIR /workspace
