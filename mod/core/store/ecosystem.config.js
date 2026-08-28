// pm2 process definition for the mod store module.
//   store-api : FastAPI gateway (protocol-auth + quota) on :50152
//   store-app : Next.js frontend on :50151
// Launch:  pm2 start ecosystem.config.js   (or ./serve.sh, which wraps this)
const path = require("path");

const DIR = __dirname;                       // .../mod/core/store
const REPO = path.resolve(DIR, "../../..");  // repo root (holds the `mod` package)

const API_PORT = process.env.STORE_API_PORT || "50152";
const APP_PORT = process.env.STORE_APP_PORT || "50151";
const MODE = process.env.STORE_MODE || "dev"; // "dev" | "prod"

module.exports = {
  apps: [
    {
      name: "store-api",
      script: "uvicorn",
      args: `api.api:app --host 0.0.0.0 --port ${API_PORT}`,
      interpreter: "none",
      cwd: DIR,
      autorestart: true,
      max_restarts: 10,
      restart_delay: 2000,
      env: {
        // Honor an externally-set PYTHONPATH (docker uses /opt/mod); else repo root.
        PYTHONPATH: process.env.PYTHONPATH || REPO,
        STORE_API_PORT: API_PORT,
      },
    },
    {
      name: "store-app",
      script: "node_modules/.bin/next",
      args:
        MODE === "prod"
          ? `start -p ${APP_PORT} -H 0.0.0.0`
          : `dev -p ${APP_PORT} -H 0.0.0.0`,
      cwd: path.join(DIR, "app"),
      autorestart: true,
      max_restarts: 10,
      restart_delay: 2000,
      env: {
        PORT: APP_PORT,
        NEXT_PUBLIC_API_URL: `http://localhost:${API_PORT}`,
      },
    },
  ],
};
