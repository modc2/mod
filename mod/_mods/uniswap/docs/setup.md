# 🐳 Docker Deployment Guide

## Quick Start

### Prerequisites
- Docker & Docker Compose installed
- WalletConnect Project ID from https://cloud.walletconnect.com/

### Setup

1. **Clone and navigate to the directory:**
```bash
cd /root/mod/mod/_mods/uniswap
```

2. **Create environment file:**
```bash
cp .env.example .env
```

3. **Edit `.env` and add your WalletConnect Project ID:**
```bash
WALLETCONNECT_PROJECT_ID=your_actual_project_id
```

4. **Build and run:**
```bash
docker-compose up -d --build
```

5. **Access the app:**
- Frontend: http://localhost:3000
- MCP Server: http://localhost:8000

### Commands

**Start services:**
```bash
docker-compose up -d
```

**Stop services:**
```bash
docker-compose down
```

**View logs:**
```bash
docker-compose logs -f
```

**Rebuild after changes:**
```bash
docker-compose up -d --build
```

**Check service health:**
```bash
docker-compose ps
```

## Architecture

### Services

1. **Frontend (Port 3000)**
   - Next.js 14 application
   - RainbowKit wallet integration
   - Optimized production build
   - Health checks enabled

2. **MCP Server (Port 8000)**
   - Python backend for blockchain interactions
   - Web3 integration
   - Quote and swap execution
   - Balance checking

### Network
- Both services communicate via `uniswap-network` bridge
- Frontend connects to MCP server at `http://mcp-server:8000`

## Environment Variables

| Variable | Description | Required |
|----------|-------------|----------|
| `WALLETCONNECT_PROJECT_ID` | WalletConnect Cloud Project ID | Yes |
| `BASE_RPC_URL` | Base network RPC endpoint | No (default provided) |
| `PRIVATE_KEY` | Private key for automated swaps | No (optional) |

## Production Deployment

### Security Best Practices

1. **Never commit `.env` file**
2. **Use secrets management** for production
3. **Enable HTTPS** with reverse proxy (nginx/traefik)
4. **Set resource limits** in docker-compose.yml

### Example with Resource Limits

```yaml
services:
  frontend:
    deploy:
      resources:
        limits:
          cpus: '1'
          memory: 1G
        reservations:
          cpus: '0.5'
          memory: 512M
```

### Reverse Proxy (Nginx)

```nginx
server {
    listen 80;
    server_name yourdomain.com;

    location / {
        proxy_pass http://localhost:3000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host $host;
        proxy_cache_bypass $http_upgrade;
    }
}
```

## Troubleshooting

**Frontend not connecting to wallet:**
- Verify `WALLETCONNECT_PROJECT_ID` is set correctly
- Check browser console for errors

**MCP Server connection failed:**
- Ensure both services are running: `docker-compose ps`
- Check logs: `docker-compose logs mcp-server`

**Build failures:**
- Clear Docker cache: `docker-compose build --no-cache`
- Remove old containers: `docker-compose down -v`

**Port conflicts:**
- Change ports in `docker-compose.yml` if 3000/8000 are in use

## Monitoring

**View real-time logs:**
```bash
# All services
docker-compose logs -f

# Specific service
docker-compose logs -f frontend
docker-compose logs -f mcp-server
```

**Resource usage:**
```bash
docker stats
```

## Updates

**Pull latest changes and rebuild:**
```bash
git pull
docker-compose down
docker-compose up -d --build
```

## Support

For issues or questions:
- Check logs first: `docker-compose logs`
- Verify environment variables
- Ensure Docker daemon is running
- Check network connectivity

---

**Built with ❤️ for the decentralized future**