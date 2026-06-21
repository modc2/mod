// pm2 process definitions for the venice module.
//   venice-api : Rust axum gateway (BYOK + x402 paid path) on :50880
//   venice-app : Next.js frontend on :3880, served under /venice
// Launch:  pm2 start ecosystem.config.js   (or ./start.sh)
//
// The paid (backend-funded) path activates only when BOTH are set in the
// environment that launches pm2:
//   VENICE_API_KEY        our Venice key, used to fund paid requests
//   VENICE_X402_RECEIVER  the address that receives USDC payments
// Optional: VENICE_X402_NETWORK (base|base-sepolia), VENICE_X402_PRICE,
//   VENICE_X402_FACILITATOR, VENICE_MASTER_KEY (pin BYOK at-rest encryption),
//   VENICE_DATA_DIR (persist BYOK keystore across restarts).
const path = require("path");

const DIR = __dirname; // .../mod/orbit/venice
const API_DIR = path.join(DIR, "src", "api");
const APP_DIR = path.join(DIR, "src", "app");

const API_PORT = process.env.VENICE_API_PORT || "50880";
const APP_PORT = process.env.VENICE_APP_PORT || "3880";
const MODE = process.env.VENICE_MODE || "dev"; // "dev" | "prod"

module.exports = {
  apps: [
    {
      name: "venice-api",
      script: path.join(API_DIR, "target", "release", "venice-api"),
      cwd: API_DIR,
      autorestart: true,
      max_restarts: 10,
      restart_delay: 2000,
      env: {
        PORT: API_PORT,
        // Persist BYOK keystore + master key under the repo by default so it
        // survives restarts. Override with a mounted volume in production.
        VENICE_DATA_DIR: process.env.VENICE_DATA_DIR || path.join(DIR, ".venice-data"),
      },
    },
    {
      name: "venice-app",
      script: "node_modules/.bin/next",
      args: MODE === "prod" ? `start -p ${APP_PORT}` : `dev -p ${APP_PORT}`,
      cwd: APP_DIR,
      autorestart: true,
      max_restarts: 10,
      restart_delay: 2000,
      env: {
        PORT: APP_PORT,
        NEXT_PUBLIC_BASE_PATH: "/venice",
        VENICE_API_URL: `http://localhost:${API_PORT}`,
      },
    },
  ],
};
