// pm2 process definitions for the dev module.
//   dev-api : Rust axum gateway (modular OpenAI-standard providers) on :8870
//   dev-app : Next.js frontend on :8871, served under /dev
// Launch:  pm2 start ecosystem.config.js   (or ./start.sh)
//
// Per-provider backend keys (the funded fallback path) are read from the env
// that launches pm2, each named by the provider's `backend_env` in config.json:
//   VENICE_API_KEY, OPENROUTER_API_KEY, OPENAI_API_KEY, ANTHROPIC_API_KEY, …
// Optional:
//   DEV_OWNER        owner address (else config.json:owner) — gates add/remove provider
//   DEV_MASTER_KEY   pin BYOK at-rest encryption (64 hex chars)
//   DEV_DATA_DIR     persist BYOK keystore + providers.json across restarts
const path = require("path");

const DIR = __dirname; // .../mod/orbit/dev
const API_DIR = path.join(DIR, "src", "api");
const APP_DIR = path.join(DIR, "src", "app");

const API_PORT = process.env.DEV_API_PORT || "8870";
const APP_PORT = process.env.DEV_APP_PORT || "8871";
const MODE = process.env.DEV_MODE || "dev"; // "dev" | "prod"

module.exports = {
  apps: [
    {
      name: "dev-api",
      script: path.join(API_DIR, "target", "release", "dev-api"),
      cwd: API_DIR,
      autorestart: true,
      max_restarts: 10,
      restart_delay: 2000,
      env: {
        PORT: API_PORT,
        // Persist BYOK keystore + provider registry under the repo by default
        // so they survive restarts. Override with a mounted volume in prod.
        DEV_DATA_DIR: process.env.DEV_DATA_DIR || path.join(DIR, ".dev-data"),
        DEV_CONFIG: path.join(DIR, "config.json"),
      },
    },
    {
      name: "dev-app",
      script: "node_modules/.bin/next",
      args: MODE === "prod" ? `start -p ${APP_PORT}` : `dev -p ${APP_PORT}`,
      cwd: APP_DIR,
      autorestart: true,
      max_restarts: 10,
      restart_delay: 2000,
      env: {
        PORT: APP_PORT,
        NEXT_PUBLIC_BASE_PATH: "/dev",
        DEV_API_URL: `http://localhost:${API_PORT}`,
      },
    },
  ],
};
