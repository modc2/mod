// pm2 process definitions for the claude module.
//   claude-api : Rust axum job server + privileged module editor on :8820
//   claude-app : Next.js terminal UI on :8823, served under /claude
//
// Launch:  pm2 start ecosystem.config.js   (or ./start.sh, which builds first)
//
// ── Why pm2 as ROOT (not docker) ────────────────────────────────────────────
// The docker deployment ran the API as uid-1000 `node`, sandboxed inside the
// container, so it could never edit sibling modules on the host. Running under
// pm2 as root gives the API real filesystem access to ~/mod/mod/{core,orbit}/*
// so the owner can edit OTHER modules through it. Because that is dangerous, the
// API is NOT started in local mode here (CLAUDE_JOBS_LOCAL is unset) — every
// cross-module write/delete/restore/kill additionally demands a fresh,
// replay-protected sudo signature from the owner key (see src/api/src/sudo.rs).
const path = require("path");

const DIR = __dirname;                          // .../mod/orbit/claude
const API_DIR = path.join(DIR, "src", "api");
const APP_DIR = path.join(DIR, "src", "app");

const API_PORT = process.env.CLAUDE_API_PORT || "8820";
const APP_PORT = process.env.CLAUDE_APP_PORT || "8823";
const MODE = process.env.CLAUDE_MODE || "prod"; // "dev" | "prod"
const BASE_PATH = process.env.NEXT_PUBLIC_BASE_PATH || "/claude";

module.exports = {
  apps: [
    {
      name: "claude-api",
      script: path.join(API_DIR, "target", "release", "claude-jobs"),
      args: API_PORT,
      cwd: API_DIR,
      autorestart: true,
      max_restarts: 10,
      restart_delay: 2000,
      env: {
        // Bind to all interfaces so the host Caddy gateway can reach it.
        BIND_HOST: process.env.BIND_HOST || "0.0.0.0",
        // IMPORTANT: do NOT set CLAUDE_JOBS_LOCAL=1 here. Local mode disables
        // auth + the sudo gate; this process runs as root, so it must enforce
        // both. (Use start.sh for an unauthenticated host-only dev server.)
      },
    },
    {
      name: "claude-app",
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
