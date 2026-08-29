// pm2 process definition for the logo module.
//   logo-api : FastAPI — the marks, and the owner gate on every write (:50760)
//   logo-app : stdlib console at /logo, proxying _api to the API (:50761)
// Launch:  pm2 start ecosystem.config.js   (or ./serve.sh, which wraps this)
const path = require("path");

const DIR = path.resolve(__dirname);            // .../mod/orbit/logo
const REPO = path.resolve(DIR, "../../..");     // repo root (holds the `mod` package)

const API_PORT = process.env.LOGO_API_PORT || "50760";
const APP_PORT = process.env.LOGO_APP_PORT || "50761";

module.exports = {
  apps: [
    {
      name: "logo-api",
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
        LOGO_API_PORT: API_PORT,
      },
    },
    {
      name: "logo-app",
      script: "python3",
      // The API url is an argument, not just the env, so the console keeps
      // pointing at the right API across a pm2 resurrect.
      args: `app/server.py --port ${APP_PORT} --api http://127.0.0.1:${API_PORT}`,
      interpreter: "none",
      cwd: DIR,
      autorestart: true,
      max_restarts: 10,
      restart_delay: 2000,
      env: {
        PYTHONPATH: process.env.PYTHONPATH || REPO,
        LOGO_APP_PORT: APP_PORT,
      },
    },
  ],
};
