// pm2 process definition for the eth module.
//   ethdesk-api : FastAPI — chain reads, keystore accounts, compile + deploy (:50750)
//   ethdesk-app : stdlib console at /eth, proxying _api to the API (:50751)
// Launch:  pm2 start ecosystem.config.js   (or ./serve.sh, which wraps this)
const path = require("path");

const DIR = path.resolve(__dirname);            // .../mod/orbit/eth
const REPO = path.resolve(DIR, "../../..");     // repo root (holds the `mod` package)

const API_PORT = process.env.ETHDESK_API_PORT || "50750";
const APP_PORT = process.env.ETHDESK_APP_PORT || "50751";

module.exports = {
  apps: [
    {
      name: "ethdesk-api",
      script: "python3",
      args: `api/api.py --port ${API_PORT}`,
      interpreter: "none",
      cwd: DIR,
      autorestart: true,
      max_restarts: 10,
      restart_delay: 2000,
      env: {
        // Honor an externally-set PYTHONPATH (docker uses /opt/mod); else repo root.
        PYTHONPATH: process.env.PYTHONPATH || REPO,
        ETHDESK_API_PORT: API_PORT,
      },
    },
    {
      name: "ethdesk-app",
      script: "python3",
      // The API url is passed as an argument, not just the env, so the console
      // keeps pointing at the right API across a pm2 resurrect.
      args: `app/server.py --port ${APP_PORT} --api http://127.0.0.1:${API_PORT}`,
      interpreter: "none",
      cwd: DIR,
      autorestart: true,
      max_restarts: 10,
      restart_delay: 2000,
      env: {
        PYTHONPATH: process.env.PYTHONPATH || REPO,
        ETHDESK_APP_PORT: APP_PORT,
      },
    },
  ],
};
