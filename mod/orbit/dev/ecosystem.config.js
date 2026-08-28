// pm2 process definitions for the build module.
//   dev-api : Rust axum job server + privileged module editor on :8870
//   dev-app : Next.js terminal UI on :8871, served under /build
//
// Launch:  pm2 start ecosystem.config.js   (or ./start.sh, which builds first)
//
// ── Why pm2 as ROOT (not docker) ────────────────────────────────────────────
// The docker deployment ran the API as uid-1000 `node`, sandboxed inside the
// container, so it could never edit sibling modules on the host. Running under
// pm2 as root gives the API real filesystem access to ~/mod/mod/{core,orbit}/*
// so the owner can edit OTHER modules through it. Because that is dangerous, the
// API is NOT started in local mode here (DEV_JOBS_LOCAL is unset) — every
// cross-module write/delete/restore/kill additionally demands a fresh,
// replay-protected sudo signature from the owner key (see src/api/src/sudo.rs).
const path = require("path");

const DIR = __dirname;                          // .../mod/orbit/build
const API_DIR = path.join(DIR, "src", "api");
const APP_DIR = path.join(DIR, "src", "app");

const API_PORT = process.env.DEV_API_PORT || "8870";
const APP_PORT = process.env.DEV_APP_PORT || "8871";
const MODE = process.env.DEV_MODE || "prod"; // "dev" | "prod"
const BASE_PATH = process.env.NEXT_PUBLIC_BASE_PATH || "/dev";

module.exports = {
  apps: [
    {
      name: "dev-api",
      script: path.join(API_DIR, "target", "release", "dev-jobs"),
      args: API_PORT,
      cwd: API_DIR,
      autorestart: true,
      max_restarts: 10,
      restart_delay: 2000,
      env: {
        // Bind to all interfaces so the host Caddy gateway can reach it.
        BIND_HOST: process.env.BIND_HOST || "0.0.0.0",
        // IMPORTANT: do NOT set DEV_JOBS_LOCAL=1 here. Local mode disables
        // auth + the sudo gate; this process runs as root, so it must enforce
        // both. (Use start.sh for an unauthenticated host-only dev server.)
      },
    },
    {
      name: "dev-app",
      script: "node_modules/.bin/next",
      args:
        MODE === "prod"
          ? `start -p ${APP_PORT} -H 0.0.0.0`
          : `dev -p ${APP_PORT} -H 0.0.0.0`,
      cwd: APP_DIR,
      autorestart: true,
      max_restarts: 10,
      restart_delay: 2000,
      env: {
        NEXT_PUBLIC_BASE_PATH: BASE_PATH,
        NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL || `http://localhost:${API_PORT}`,
      },
    },
  ],
};
