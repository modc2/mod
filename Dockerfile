FROM ubuntu:22.04
ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y  curl git build-essential nano jq vim software-properties-common 

# NodeJS + NPM
RUN apt-get install -y nodejs npm
RUN npm install -g pm2

# Rust via rustup (more modern than apt)
RUN curl https://sh.rustup.rs -sSf | sh -s -- -y
ENV PATH="/root/.cargo/bin:$PATH"

RUN apt-get install -y \
    python3 python3-pip python3-venv \
    && ln -sf /usr/bin/python3 /usr/bin/python \
    && ln -sf /usr/bin/pip3 /usr/bin/pip

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

# Upgrade pip, setuptools, wheel
RUN pip install --upgrade pip setuptools wheel

# Workdir + Install App
WORKDIR /root/mod
COPY . .
RUN pip install -e ./

# # # DOCKER AND DOCKER-COMPOSE
# RUN apt-get update && apt-get install -y \
#     docker.io \
#     && systemctl enable docker \
#     && usermod -aG docker root
# RUN curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-linux-x86_64" -o /usr/local/bin/docker-compose && \
#     chmod +x /usr/local/bin/docker-compose

CMD ["tail", "-f", "/dev/null"]

