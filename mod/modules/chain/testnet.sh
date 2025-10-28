#!/bin/bash

set -e

echo "Starting testnet without docker-compose..."

# Build the project if not already built
if [ ! -f "./target/release/node-template" ]; then
    echo "Building the node..."
    cargo build --release
fi

# Clean up any existing testnet data
echo "Cleaning up previous testnet data..."
rm -rf /tmp/testnet-data

# Start the testnet node
echo "Starting testnet node..."
./target/release/node-template \
    --dev \
    --rpc-external \
    --rpc-cors all \
    --unsafe-rpc-external \
    --ws-external \
    --rpc-port 9933 \
    --ws-port 9944 \
    --port 30333 \
    --base-path /tmp/testnet-data \
    --validator \
    --alice

echo "Testnet is running!"
