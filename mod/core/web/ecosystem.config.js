// pm2 process definitions for the mod "web" front door (served at /web).
//   mod-web-api : Rust axum catalog gateway on :50420
//   mod-web-app : Next.js frontend on :3420, served under /web
// Launch:  ./start.sh   (or pm2 start ecosystem.config.js)
const path = require("path");

const DIR = __dirname; // .../mod/core/web
const API_DIR = path.join(DIR, "api");
const APP_DIR = path.join(DIR, "app");
// The orbit tree the catalog API scans: mod/core/web -> ../../orbit.
const ORBIT_DIR = path.resolve(DIR, "..", "..", "orbit");

const API_PORT = process.env.MOD_WEB_API_PORT || "50420";
const APP_PORT = process.env.MOD_WEB_APP_PORT || "3420";
const MODE = process.env.MOD_WEB_MODE || "prod"; // "dev" | "prod"

module.exports = {
  apps: [
    {
      name: "mod-web-api",
      script: path.join(API_DIR, "target", "release", "mod-api"),
      cwd: API_DIR,
      autorestart: true,
      max_restarts: 10,
      restart_delay: 2000,
      env: {
        PORT: API_PORT,
        MOD_ORBIT_DIR: ORBIT_DIR,
      },
    },
    {
      name: "mod-web-app",
      script: "node_modules/.bin/next",
      args: MODE === "prod" ? `start -p ${APP_PORT}` : `dev -p ${APP_PORT}`,
      cwd: APP_DIR,
      autorestart: true,
      max_restarts: 10,
      restart_delay: 2000,
      env: {
        PORT: APP_PORT,
        NEXT_PUBLIC_BASE_PATH: "/web",
        MOD_API_URL: `http://localhost:${API_PORT}`,
      },
    },
  ],
};
