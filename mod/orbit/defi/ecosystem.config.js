// pm2 process definitions for the defi module.
//   defi-api : Rust axum composer — catalog, validation, solc, planning, MCP (:50500)
//   defi-app : Next.js canvas served under /defi (:50501)
//
// Launch:  pm2 start ecosystem.config.js   (or ./start.sh, which builds first)
//
// The API holds no keys and signs nothing — deployment transactions are built
// here and signed by the user's browser wallet — so it runs as an ordinary
// service with no sudo path and no privileged filesystem access.
const path = require("path");

const DIR = __dirname;
const API_DIR = path.join(DIR, "src", "api");
const APP_DIR = path.join(DIR, "src", "app");

const API_PORT = process.env.DEFI_API_PORT || "50500";
const APP_PORT = process.env.DEFI_APP_PORT || "50501";
const BASE_PATH = process.env.NEXT_PUBLIC_BASE_PATH || "/defi";

module.exports = {
  apps: [
    {
      name: "defi-api",
      script: path.join(API_DIR, "target", "release", "defi-api"),
      args: API_PORT,
      cwd: API_DIR,
      autorestart: true,
      max_restarts: 10,
      restart_delay: 2000,
      env: {
        BIND_HOST: process.env.BIND_HOST || "0.0.0.0",
        DEFI_MODULE_DIR: DIR,
        DEFI_BLOCKS_DIR: path.join(API_DIR, "blocks"),
        // The prompt library and the compose brain. Everything else works
        // without it.
        DEFI_AGENT_URL: process.env.DEFI_AGENT_URL || "http://localhost:50117",
      },
    },
    {
      name: "defi-app",
      script: "node_modules/.bin/next",
      args: `start -p ${APP_PORT} -H 0.0.0.0`,
      cwd: APP_DIR,
      autorestart: true,
      max_restarts: 10,
      restart_delay: 2000,
      env: {
        NEXT_PUBLIC_BASE_PATH: BASE_PATH,
        NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL || "",
      },
    },
  ],
};
