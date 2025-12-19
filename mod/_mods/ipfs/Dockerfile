FROM mod:latest

# Install IPFS
RUN wget https://dist.ipfs.tech/kubo/v0.24.0/kubo_v0.24.0_linux-amd64.tar.gz && \
    tar -xvzf kubo_v0.24.0_linux-amd64.tar.gz && \
    cd kubo && \
    bash install.sh && \
    cd .. && \
    rm -rf kubo kubo_v0.24.0_linux-amd64.tar.gz

# Initialize IPFS
RUN ipfs init

# Expose IPFS ports
EXPOSE 4001 5001 8080

# Start IPFS daemon
CMD ["ipfs", "daemon"]
