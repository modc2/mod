FROM mod:latest

# Install Node.js and npm
RUN apt-get update && apt-get install -y \
    curl \
    && curl -fsSL https://deb.nodesource.com/setup_18.x | bash - \
    && apt-get install -y nodejs \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Install Ganache globally
RUN npm install -g ganache

# Expose Ganache default port
EXPOSE 8545

# Set working directory
WORKDIR /app

# Copy ganache module
COPY . /app/ganache

# Default command
CMD ["ganache", "--host", "0.0.0.0", "--port", "8545"]
