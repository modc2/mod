# 🚀 COMPLETE INSTALLATION GUIDE - UNISWAP V3 MCP SERVER

## QUICK START (5 MINUTES)

### Step 1: Get Your Wallet Ready
1. You need a wallet with ETH on Base Network
2. Get free Base ETH from bridge: https://bridge.base.org
3. Export your private key (see below)

### Step 2: Install
```bash
# Navigate to server directory
cd /root/mod/mod/_mods/uniswap/uniswap/server

# Install dependencies
pip install -r requirements.txt

# Setup environment
cp .env.example .env
```

### Step 3: Configure
Edit `.env` file:
```bash
nano .env
```

Add:
```
BASE_RPC_URL=https://mainnet.base.org
PRIVATE_KEY=your_actual_private_key_here
```

### Step 4: Run
```bash
python server.py
```

✅ **DONE! Server is running!**

---

## DETAILED INSTALLATION OPTIONS

### OPTION A: Python Virtual Environment (Recommended for Development)

```bash
# 1. Create virtual environment
python3 -m venv venv

# 2. Activate it
source venv/bin/activate  # Linux/Mac
# OR
venv\Scripts\activate  # Windows

# 3. Install dependencies
pip install --upgrade pip
pip install -r requirements.txt

# 4. Setup environment
cp .env.example .env
nano .env  # Add your credentials

# 5. Run server
python server.py
```

### OPTION B: Docker (Recommended for Production)

```bash
# 1. Setup environment
cp .env.example .env
nano .env  # Add your credentials

# 2. Build and run
docker-compose up -d

# 3. Check status
docker-compose ps

# 4. View logs
docker-compose logs -f

# 5. Stop server
docker-compose down
```

### OPTION C: System-Wide Python Installation

```bash
# 1. Install dependencies globally
sudo pip install -r requirements.txt

# 2. Setup environment
cp .env.example .env
nano .env

# 3. Run as service (optional)
sudo python server.py
```

---

## 🔑 HOW TO GET YOUR PRIVATE KEY

### MetaMask (Most Common)
1. Open MetaMask extension
2. Click three dots (⋮) in top right
3. Click "Account Details"
4. Click "Export Private Key"
5. Enter your MetaMask password
6. **Copy the key WITHOUT the 0x prefix**
7. Paste into `.env` file

### Coinbase Wallet
1. Open Coinbase Wallet app
2. Go to Settings → Security
3. Tap "Show Private Key"
4. Verify with biometrics/PIN
5. Copy the key (remove 0x if present)

### Trust Wallet
1. Open Trust Wallet
2. Go to Settings → Wallets
3. Tap the info icon next to your wallet
4. Tap "Export Private Key"
5. Enter password
6. Copy the key

### Ledger/Hardware Wallet
⚠️ **Cannot export private key** - Hardware wallets don't expose private keys for security. You'll need to use a software wallet for this server.

---

## 📦 DEPENDENCIES EXPLAINED

```txt
web3>=6.11.0          # Ethereum/Base blockchain interaction
eth-account>=0.10.0   # Wallet and transaction signing
aiohttp>=3.9.0        # Async HTTP for better performance
python-dotenv>=1.0.0  # Environment variable management
```

### Install Individual Packages
```bash
pip install web3
pip install eth-account
pip install aiohttp
pip install python-dotenv
```

---

## 🐳 DOCKER INSTALLATION DETAILS

### Install Docker (Ubuntu/Debian)
```bash
# Update package index
sudo apt update

# Install Docker
sudo apt install docker.io docker-compose -y

# Start Docker service
sudo systemctl start docker
sudo systemctl enable docker

# Add user to docker group (no sudo needed)
sudo usermod -aG docker $USER
newgrp docker
```

### Install Docker (Mac)
```bash
# Using Homebrew
brew install --cask docker

# Or download from:
# https://www.docker.com/products/docker-desktop
```

### Install Docker (Windows)
1. Download Docker Desktop: https://www.docker.com/products/docker-desktop
2. Run installer
3. Restart computer
4. Open Docker Desktop

---

## 🌐 RPC ENDPOINTS FOR BASE NETWORK

### Free Public RPCs
```bash
# Official Base RPC (Recommended)
BASE_RPC_URL=https://mainnet.base.org

# Llama RPC (Fast)
BASE_RPC_URL=https://base.llamarpc.com

# Blast API (Reliable)
BASE_RPC_URL=https://base-mainnet.public.blastapi.io

# Ankr (Good uptime)
BASE_RPC_URL=https://rpc.ankr.com/base
```

### Paid/Premium RPCs (Better Performance)
```bash
# Alchemy (Get free key at alchemy.com)
BASE_RPC_URL=https://base-mainnet.g.alchemy.com/v2/YOUR_API_KEY

# Infura (Get free key at infura.io)
BASE_RPC_URL=https://base-mainnet.infura.io/v3/YOUR_API_KEY

# QuickNode (Get free trial at quicknode.com)
BASE_RPC_URL=https://your-endpoint.base-mainnet.quiknode.pro/YOUR_TOKEN/
```

---

## ✅ VERIFICATION STEPS

### 1. Check Python Version
```bash
python --version  # Should be 3.11+
```

### 2. Check Dependencies
```bash
pip list | grep web3
pip list | grep eth-account
```

### 3. Test Connection
```python
from web3 import Web3
w3 = Web3(Web3.HTTPProvider('https://mainnet.base.org'))
print(f"Connected: {w3.is_connected()}")
print(f"Chain ID: {w3.eth.chain_id}")  # Should be 8453 for Base
```

### 4. Verify Server Running
```bash
# Should see:
# 🚀 Uniswap MCP Server Running on Base
# Router: 0x2626664c2603336E57B271c5C0b26F421741e481
# Connected: True
```

---

## 🔧 TROUBLESHOOTING INSTALLATION

### "pip: command not found"
```bash
# Install pip
sudo apt install python3-pip  # Ubuntu/Debian
brew install python3  # Mac
```

### "Permission denied" errors
```bash
# Use virtual environment instead of sudo
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### "Module not found" errors
```bash
# Reinstall dependencies
pip install --force-reinstall -r requirements.txt
```

### Docker "permission denied"
```bash
# Add user to docker group
sudo usermod -aG docker $USER
newgrp docker
```

### "Connection refused" to RPC
```bash
# Try different RPC endpoint
BASE_RPC_URL=https://base.llamarpc.com

# Check internet connection
ping mainnet.base.org
```

---

## 🎯 POST-INSTALLATION TESTING

### Test 1: Get Quote (No Transaction)
```python
import asyncio
from server import UniswapMCPServer

async def test():
    server = UniswapMCPServer(
        rpc_url="https://mainnet.base.org",
        private_key=None  # No key needed for quotes
    )
    
    quote = await server.get_quote(
        token_in="0x4200000000000000000000000000000000000006",  # WETH
        token_out="0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",  # USDC
        amount_in=0.001  # Small amount
    )
    print(quote)

asyncio.run(test())
```

### Test 2: Check Balance
```python
import asyncio
from server import UniswapMCPServer

async def test():
    server = UniswapMCPServer(
        rpc_url="https://mainnet.base.org"
    )
    
    balance = await server.get_balance(
        token_address="0x4200000000000000000000000000000000000006",
        wallet_address="YOUR_WALLET_ADDRESS"
    )
    print(balance)

asyncio.run(test())
```

---

## 🚀 PRODUCTION DEPLOYMENT

### Using systemd (Linux)

1. Create service file:
```bash
sudo nano /etc/systemd/system/uniswap-mcp.service
```

2. Add configuration:
```ini
[Unit]
Description=Uniswap MCP Server
After=network.target

[Service]
Type=simple
User=youruser
WorkingDirectory=/root/mod/mod/_mods/uniswap/uniswap/server
Environment="PATH=/usr/bin:/usr/local/bin"
EnvironmentFile=/root/mod/mod/_mods/uniswap/uniswap/server/.env
ExecStart=/usr/bin/python3 server.py
Restart=always

[Install]
WantedBy=multi-user.target
```

3. Enable and start:
```bash
sudo systemctl daemon-reload
sudo systemctl enable uniswap-mcp
sudo systemctl start uniswap-mcp
sudo systemctl status uniswap-mcp
```

### Using PM2 (Node.js Process Manager)
```bash
# Install PM2
npm install -g pm2

# Start server
pm2 start server.py --name uniswap-mcp --interpreter python3

# Auto-restart on boot
pm2 startup
pm2 save

# Monitor
pm2 monit
```

---

## 📊 RESOURCE REQUIREMENTS

### Minimum
- CPU: 1 core
- RAM: 512 MB
- Disk: 100 MB
- Network: Stable internet

### Recommended
- CPU: 2 cores
- RAM: 1 GB
- Disk: 1 GB
- Network: Low latency (<100ms to RPC)

---

## 🎓 NEXT STEPS AFTER INSTALLATION

1. ✅ Test with small amounts (0.001 ETH)
2. ✅ Monitor gas costs
3. ✅ Set up monitoring/alerts
4. ✅ Review security best practices
5. ✅ Read API documentation in README.md
6. ✅ Join Base community for support

---

**Installation complete! You're ready to swap on Base! 🎉**