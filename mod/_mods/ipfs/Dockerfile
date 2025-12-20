FROM mod:latest

# Set versions
ENV IPFS_VERSION=0.28.0

# Install dependencies
RUN apt-get update && apt-get install -y \
    curl \
    tar \
    ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Install IPFS (Kubo)
RUN curl -L https://dist.ipfs.tech/kubo/kubo_v${IPFS_VERSION}_linux-amd64.tar.gz \
    | tar -xz \
 && cd kubo \
 && bash install.sh \
 && cd / \
 && rm -rf kubo

# IPFS paths
ENV IPFS_PATH=/data/ipfs

# Create IPFS data dir
RUN mkdir -p /data/ipfs

# Expose IPFS ports
EXPOSE 4001 5001 8080

# Initialize IPFS repo at build time (optional)
RUN ipfs init --profile=server

# Default command
CMD ["ipfs", "daemon", "--migrate=true"]
