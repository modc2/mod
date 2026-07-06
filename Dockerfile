FROM python:3.12-slim
ARG DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl build-essential nodejs npm gnupg \
    debian-keyring debian-archive-keyring apt-transport-https ca-certificates \
    && curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg \
    && curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | tee /etc/apt/sources.list.d/caddy-stable.list \
    && apt-get update && apt-get install -y caddy \
    && rm -rf /var/lib/apt/lists/*

ENV MOD_DOCKER=1

WORKDIR /root/mod
COPY . .

RUN chmod +x setup.sh start.sh stop.sh && bash setup.sh

EXPOSE 3000

ENTRYPOINT ["bash", "-c"]
CMD ["./stop.sh 2>/dev/null; ./start.sh && pm2 logs --raw"]
