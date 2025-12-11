# Start from an official Node image with version >=16 (e.g., 18 or LTS)
FROM mod:latest
WORKDIR /app
COPY ./package.json .
RUN npm install
# For development, we don't need to build
# The dev server will handle compilation on the fly
ENTRYPOINT ["npm", "run", "dev", "--", "--hostname", "0.0.0.0"]