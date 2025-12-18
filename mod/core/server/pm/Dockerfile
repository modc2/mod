FROM mod:latest
# # DOCKER AND DOCKER-COMPOSE
RUN apt-get update && apt-get install -y \
    docker.io \
    && systemctl enable docker \
    && usermod -aG docker root
RUN curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-linux-x86_64" -o /usr/local/bin/docker-compose && \
    chmod +x /usr/local/bin/docker-compose