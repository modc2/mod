FROM mod:latest
WORKDIR /root/mod/mod/orbit/bridge

# Install Node.js app dependencies
COPY app/package.json app/package-lock.json* app/
RUN cd app && npm install

# Copy bridge source
COPY api/ api/
COPY app/ app/
COPY config.json .
COPY scripts/ scripts/
COPY snapshot/ snapshot/

# Build Next.js app for production
RUN cd app && npx next build

EXPOSE 8840 8841

ENTRYPOINT ["bash", "scripts/entrypoint.sh"]
