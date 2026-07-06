#!/bin/bash

# PreFi Docker Start Script
# This script helps you start the PreFi application with Docker Compose

set -e

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${BLUE}"
echo "╔═══════════════════════════════════════════════════════════╗"
echo "║                                                           ║"
echo "║   🎯  PreFi Prediction Market - Docker Launcher  🎯      ║"
echo "║                                                           ║"
echo "║          Base Mainnet • Uniswap V3 • L2 Scoring          ║"
echo "║                                                           ║"
echo "╚═══════════════════════════════════════════════════════════╝"
echo -e "${NC}"

# Check if Docker is running
if ! docker info > /dev/null 2>&1; then
    echo -e "${RED}❌ Error: Docker is not running. Please start Docker Desktop.${NC}"
    exit 1
fi

echo -e "${GREEN}✅ Docker is running${NC}"

# Check if .env file exists
if [ ! -f .env ]; then
    echo -e "${YELLOW}⚠️  .env file not found. Copying from .env.example...${NC}"
    cp .env.example .env
    echo -e "${YELLOW}📝 Please edit .env and add your WalletConnect Project ID${NC}"
    echo -e "${YELLOW}   Get one at: https://cloud.walletconnect.com/${NC}"
    echo ""
    read -p "Press Enter after updating .env to continue..."
fi

# Check if WalletConnect Project ID is set
if grep -q "NEXT_PUBLIC_WALLETCONNECT_PROJECT_ID=$" .env || grep -q "NEXT_PUBLIC_WALLETCONNECT_PROJECT_ID=your_project_id" .env; then
    echo -e "${RED}❌ Error: WalletConnect Project ID is not set in .env${NC}"
    echo -e "${YELLOW}   Please update NEXT_PUBLIC_WALLETCONNECT_PROJECT_ID in .env${NC}"
    exit 1
fi

echo -e "${GREEN}✅ Environment configuration found${NC}"

# Build and start services
echo -e "\n${BLUE}🔨 Building Docker image...${NC}"
docker-compose build

echo -e "\n${BLUE}🚀 Starting PreFi application...${NC}"
docker-compose up -d

# Wait for the application to be healthy
echo -e "\n${YELLOW}⏳ Waiting for application to be healthy...${NC}"
sleep 5

MAX_RETRIES=12
RETRY_COUNT=0
while [ $RETRY_COUNT -lt $MAX_RETRIES ]; do
    if curl -s http://localhost:3000/api/health > /dev/null; then
        echo -e "${GREEN}✅ Application is healthy and running!${NC}"
        break
    fi
    RETRY_COUNT=$((RETRY_COUNT + 1))
    if [ $RETRY_COUNT -eq $MAX_RETRIES ]; then
        echo -e "${RED}❌ Application failed to start. Check logs with: docker-compose logs -f${NC}"
        exit 1
    fi
    echo -e "${YELLOW}   Still starting... (attempt $RETRY_COUNT/$MAX_RETRIES)${NC}"
    sleep 5
done

# Get health status
echo -e "\n${BLUE}📊 Application Status:${NC}"
curl -s http://localhost:3000/api/health | jq '.' 2>/dev/null || curl -s http://localhost:3000/api/health

echo -e "\n${GREEN}"
echo "╔═══════════════════════════════════════════════════════════╗"
echo "║                                                           ║"
echo "║   ✨  PreFi is now running on Base Mainnet!  ✨         ║"
echo "║                                                           ║"
echo "║   🌐  URL: http://localhost:3000                         ║"
echo "║   📊  Health: http://localhost:3000/api/health           ║"
echo "║   🔗  Network: Base Mainnet (Chain ID 8453)              ║"
echo "║                                                           ║"
echo "╚═══════════════════════════════════════════════════════════╝"
echo -e "${NC}"

echo -e "\n${BLUE}📝 Useful Commands:${NC}"
echo "  View logs:       docker-compose logs -f prefi-app"
echo "  Stop:            docker-compose down"
echo "  Restart:         docker-compose restart"
echo "  Rebuild:         docker-compose up --build -d"

echo -e "\n${YELLOW}💡 Next Steps:${NC}"
echo "  1. Open http://localhost:3000 in your browser"
echo "  2. Connect your wallet (MetaMask, Rainbow, etc.)"
echo "  3. Switch to Base Mainnet network"
echo "  4. Start making predictions!"

echo -e "\n${GREEN}Happy predicting! 🎯${NC}\n"
