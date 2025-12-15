# Python MCP Server Dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY uniswap/server/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy server code
COPY . .

# Expose port for MCP server
EXPOSE 8000

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV BASE_RPC_URL=https://mainnet.base.org

# Run the server
CMD ["python", "server.py"]