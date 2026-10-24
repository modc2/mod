// pm2 process definition for the algorand module.
//   algorand-api : REST + MCP, api/api.py (:51340)
//   algorand-app : the console at /algorand, app/server.py, proxying _api to the API (:51341)
// Launch:  pm2 start ecosystem.config.js
const path = require("path");

const DIR = path.resolve(__dirname);
const API_PORT = process.env.ALGORAND_API_PORT || "51340";
const APP_PORT = process.env.ALGORAND_APP_PORT || "51341";

module.exports = {
  apps: [
    {
      name: "algorand-api",
      script: "python3",
      args: `api/api.py --port ${API_PORT}`,
      interpreter: "none",
      cwd: DIR,
      autorestart: true,
      max_restarts: 10,
      restart_delay: 2000,
      env: { ALGORAND_API_PORT: API_PORT },
    },
    {
      name: "algorand-app",
      script: "python3",
      args: `app/server.py --port ${APP_PORT} --api http://127.0.0.1:${API_PORT}`,
      interpreter: "none",
      cwd: DIR,
      autorestart: true,
      max_restarts: 10,
      restart_delay: 2000,
      env: { ALGORAND_APP_PORT: APP_PORT },
    },
  ],
};
