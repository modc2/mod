#!/usr/bin/env python3
"""
dockerize.py — Generate standardized Docker files for mod orbit modules.

Reads each module's config.json, detects its archetype based on filesystem
structure, and generates Dockerfile, docker-compose.yml, and
docker-entrypoint.sh with a single Caddy gateway port.

Usage:
    python3 scripts/dockerize.py --all             # generate for all modules with ports
    python3 scripts/dockerize.py --module polymarket # generate for one module
    python3 scripts/dockerize.py --all --dry-run    # preview only
    python3 scripts/dockerize.py --all --force       # overwrite existing files
"""

import argparse
import json
import os
import re
import sys
import stat
from pathlib import Path

ORBIT_DIR = Path(__file__).resolve().parent.parent / "mod" / "orbit"

# ---------------------------------------------------------------------------
# Archetype detection
# ---------------------------------------------------------------------------

def detect_archetype(mod_dir: Path) -> str:
    """Detect module archetype based on filesystem structure.

    A: Rust API (src/api/Cargo.toml) + Next.js (src/app/)
    B: Python API + Next.js app
    C: Rust root (Cargo.toml) + Next.js (app/)
    D: Python-only / m-serve (no standalone frontend)
    """
    has_rust_src_api = (mod_dir / "src" / "api" / "Cargo.toml").exists()
    has_rust_root = (mod_dir / "Cargo.toml").exists() and not has_rust_src_api
    has_nextjs_src_app = (
        (mod_dir / "src" / "app" / "package.json").exists()
        or any((mod_dir / "src" / "app").glob("next.config.*")) if (mod_dir / "src" / "app").is_dir() else False
    )
    has_nextjs_app = (
        (mod_dir / "app" / "package.json").exists()
        or any((mod_dir / "app").glob("next.config.*")) if (mod_dir / "app").is_dir() else False
    )
    has_nextjs = has_nextjs_src_app or has_nextjs_app

    if has_rust_src_api and has_nextjs:
        return "A"
    if has_rust_root and has_nextjs:
        return "C"
    # Python + Next.js
    if has_nextjs:
        return "B"
    # Python-only / m serve
    return "D"


def find_app_dir(mod_dir: Path) -> str | None:
    """Return relative app directory ('src/app' or 'app') or None."""
    if (mod_dir / "src" / "app" / "package.json").exists():
        return "src/app"
    if (mod_dir / "app" / "package.json").exists():
        return "app"
    if (mod_dir / "src" / "app").is_dir() and any((mod_dir / "src" / "app").glob("next.config.*")):
        return "src/app"
    if (mod_dir / "app").is_dir() and any((mod_dir / "app").glob("next.config.*")):
        return "app"
    return None


def find_api_dir(mod_dir: Path) -> str | None:
    """Return relative API directory or None."""
    if (mod_dir / "src" / "api" / "Cargo.toml").exists():
        return "src/api"
    if (mod_dir / "src" / "api").is_dir():
        return "src/api"
    if (mod_dir / "api").is_dir():
        return "api"
    if (mod_dir / "server").is_dir():
        return "server"
    return None


def find_rust_binary_name(cargo_dir: Path) -> str:
    """Extract binary name from Cargo.toml."""
    cargo_path = cargo_dir / "Cargo.toml"
    if not cargo_path.exists():
        return "server"
    content = cargo_path.read_text()
    # Check [[bin]] section first
    bin_match = re.search(r'\[\[bin\]\]\s*name\s*=\s*"([^"]+)"', content)
    if bin_match:
        return bin_match.group(1)
    # Fall back to [package] name
    pkg_match = re.search(r'\[package\]\s*name\s*=\s*"([^"]+)"', content)
    if pkg_match:
        return pkg_match.group(1)
    return "server"


def find_python_entrypoint(api_dir: Path) -> str:
    """Find the Python entrypoint module (for uvicorn)."""
    for candidate in ["server.py", "api.py", "main.py", "app.py"]:
        if (api_dir / candidate).exists():
            return candidate.replace(".py", "")
    # Check for any .py with FastAPI/app
    for py_file in api_dir.glob("*.py"):
        content = py_file.read_text(errors="ignore")
        if "FastAPI" in content or "app = " in content:
            return py_file.stem
    return "server"


def has_python_requirements(api_dir: Path) -> bool:
    """Check if there's a requirements.txt in the API dir."""
    return (api_dir / "requirements.txt").exists()


# ---------------------------------------------------------------------------
# Port extraction from config.json
# ---------------------------------------------------------------------------

def _extract_port_from_value(val) -> int | None:
    """Try to extract a port number from various value types."""
    if isinstance(val, (int, float)):
        return int(val)
    if isinstance(val, str):
        if val.isdigit():
            return int(val)
        m = re.search(r":(\d{4,5})", val)
        if m:
            return int(m.group(1))
    return None


def _deep_find_ports(obj: dict, depth: int = 0) -> tuple[int | None, int | None, int | None]:
    """Recursively search a dict for port/app_port/gateway_port fields."""
    if depth > 3:
        return None, None, None

    api_port = None
    app_port = None
    gateway_port = None

    # Direct fields at this level
    for key in ["port"]:
        if key in obj:
            p = _extract_port_from_value(obj[key])
            if p and api_port is None:
                api_port = p

    for key in ["app_port"]:
        if key in obj:
            p = _extract_port_from_value(obj[key])
            if p and app_port is None:
                app_port = p

    if "gateway_port" in obj:
        p = _extract_port_from_value(obj["gateway_port"])
        if p:
            gateway_port = p

    # Nested ports object
    if "ports" in obj and isinstance(obj["ports"], dict):
        ports = obj["ports"]
        if "api" in ports:
            p = _extract_port_from_value(ports["api"])
            if p and api_port is None:
                api_port = p
        if "app" in ports:
            p = _extract_port_from_value(ports["app"])
            if p and app_port is None:
                app_port = p

    # URLs containing port numbers
    if "urls" in obj and isinstance(obj["urls"], dict):
        for key in ["api", "server"]:
            if key in obj["urls"] and api_port is None:
                p = _extract_port_from_value(obj["urls"][key])
                if p:
                    api_port = p
        for key in ["app", "frontend"]:
            if key in obj["urls"] and app_port is None:
                p = _extract_port_from_value(obj["urls"][key])
                if p:
                    app_port = p

    # Top-level URL string fields
    if api_port is None:
        for key in ["api", "api_url", "server"]:
            if key in obj and isinstance(obj[key], str):
                p = _extract_port_from_value(obj[key])
                if p:
                    api_port = p
                    break

    if app_port is None:
        for key in ["app", "app_url", "frontend"]:
            if key in obj and isinstance(obj[key], str):
                p = _extract_port_from_value(obj[key])
                if p:
                    app_port = p
                    break

    # Recurse into nested dicts (data, engine, etc.)
    if api_port is None or app_port is None:
        for key in ["data", "engine", "config", "settings"]:
            if key in obj and isinstance(obj[key], dict):
                sub_api, sub_app, sub_gw = _deep_find_ports(obj[key], depth + 1)
                if api_port is None and sub_api is not None:
                    api_port = sub_api
                if app_port is None and sub_app is not None:
                    app_port = sub_app
                if gateway_port is None and sub_gw is not None:
                    gateway_port = sub_gw

    return api_port, app_port, gateway_port


def read_ports(config: dict) -> tuple[int | None, int | None, int | None]:
    """Extract (api_port, app_port, gateway_port) from config.json."""
    api_port, app_port, gateway_port = _deep_find_ports(config)

    # Default gateway_port
    if gateway_port is None:
        gateway_port = app_port or api_port

    return api_port, app_port, gateway_port


# ---------------------------------------------------------------------------
# Template: Archetype A — Rust API (src/api) + Next.js
# ---------------------------------------------------------------------------

def dockerfile_A(module_name: str, api_port: int, app_port: int,
                 gateway_port: int, binary_name: str, app_dir: str) -> str:
    return f"""\
# ── Stage 1: Build Rust API ──────────────────────────────────────
FROM rust:slim AS api-builder
RUN apt-get update && apt-get install -y --no-install-recommends pkg-config libssl-dev && rm -rf /var/lib/apt/lists/*
WORKDIR /build
COPY src/api/ .
RUN cargo build --release

# ── Stage 2: Build Next.js app ──────────────────────────────────
FROM node:20-slim AS app-builder
WORKDIR /build
COPY {app_dir}/package.json {app_dir}/package-lock.json* ./
RUN npm ci --no-audit --no-fund
COPY {app_dir}/ ./
ENV NEXT_PUBLIC_API_URL=/api/{module_name}
ENV NEXT_PUBLIC_BASE_PATH=/{module_name}
RUN npm run build

# ── Stage 3: Runtime ────────────────────────────────────────────
FROM node:20-slim
RUN apt-get update && apt-get install -y --no-install-recommends \\
    jq curl ca-certificates \\
    && ARCH=$(dpkg --print-architecture) \\
    && curl -fsSL "https://caddyserver.com/api/download?os=linux&arch=${{ARCH}}" -o /usr/local/bin/caddy \\
    && chmod +x /usr/local/bin/caddy \\
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY config.json .

# Rust API binary
COPY --from=api-builder /build/target/release/{binary_name} /app/bin/{binary_name}

# Next.js production build + runtime deps
COPY --from=app-builder /build/.next /app/{app_dir}/.next
COPY --from=app-builder /build/node_modules /app/{app_dir}/node_modules
COPY --from=app-builder /build/package.json /app/{app_dir}/package.json
COPY --from=app-builder /build/next.config.* /app/{app_dir}/

COPY docker-entrypoint.sh /app/docker-entrypoint.sh
RUN chmod +x /app/docker-entrypoint.sh

ENV GATEWAY_PORT={gateway_port}
EXPOSE {gateway_port}

ENTRYPOINT ["/app/docker-entrypoint.sh"]
"""


def entrypoint_A(module_name: str, api_port: int, app_port: int,
                 gateway_port: int, binary_name: str, app_dir: str) -> str:
    return f"""\
#!/bin/bash
set -e

API_PORT={api_port}
APP_PORT={app_port}
GATEWAY_PORT="${{GATEWAY_PORT:-{gateway_port}}}"
MODULE="{module_name}"

echo "── $MODULE docker ──"

# ── Start Rust API ──
PORT=$API_PORT /app/bin/{binary_name} &
API_PID=$!
echo "API starting on :$API_PORT (pid $API_PID)"

for i in $(seq 1 30); do
    if curl -sf http://localhost:$API_PORT/health > /dev/null 2>&1; then
        echo "API ready"; break
    fi
    sleep 1
done

# ── Start Next.js ──
cd /app/{app_dir}
NEXT_PUBLIC_API_URL="/api/$MODULE" \\
NEXT_PUBLIC_BASE_PATH="/$MODULE" \\
PORT=$APP_PORT \\
npx next start -p $APP_PORT &
APP_PID=$!
echo "App starting on :$APP_PORT (pid $APP_PID)"
cd /app

for i in $(seq 1 30); do
    if curl -sf http://localhost:$APP_PORT > /dev/null 2>&1; then
        echo "App ready"; break
    fi
    sleep 1
done

# ── Caddyfile ──
cat > /app/Caddyfile <<EOF
{{
    admin off
}}

:${{GATEWAY_PORT}} {{
    @api path /api/$MODULE /api/$MODULE/*
    handle @api {{
        uri strip_prefix /api/$MODULE
        reverse_proxy localhost:${{API_PORT}}
    }}
    handle /* {{
        reverse_proxy localhost:${{APP_PORT}}
    }}
}}
EOF

caddy run --config /app/Caddyfile &
CADDY_PID=$!
echo "Gateway on :$GATEWAY_PORT (pid $CADDY_PID)"

echo ""
echo "  API:     http://localhost:$API_PORT/health"
echo "  App:     http://localhost:$APP_PORT"
echo "  Gateway: http://localhost:$GATEWAY_PORT"
echo ""

cleanup() {{
    echo "shutting down..."
    kill $CADDY_PID $APP_PID $API_PID 2>/dev/null
    wait $CADDY_PID $APP_PID $API_PID 2>/dev/null
}}
trap cleanup SIGTERM SIGINT

wait -n $API_PID $APP_PID $CADDY_PID
EXIT_CODE=$?
echo "process exited with code $EXIT_CODE — stopping all"
cleanup
exit $EXIT_CODE
"""


# ---------------------------------------------------------------------------
# Template: Archetype B — Python API + Next.js (or Python-only with app)
# ---------------------------------------------------------------------------

def dockerfile_B(module_name: str, api_port: int, app_port: int | None,
                 gateway_port: int, api_dir: str | None, app_dir: str | None) -> str:
    has_api = api_dir is not None
    has_app = app_dir is not None

    stages = []

    # Stage 1: Python deps
    if has_api:
        stages.append(f"""\
# ── Stage 1: Python API deps ────────────────────────────────────
FROM python:3.12-slim AS api-builder
WORKDIR /build
COPY {api_dir}/requirements.txt* ./
RUN pip install --no-cache-dir --target /build/deps -r requirements.txt 2>/dev/null || true""")

    # Stage 2: Next.js build
    if has_app:
        stages.append(f"""\
# ── Stage {2 if has_api else 1}: Build Next.js app ───────────────────────────────
FROM node:20-slim AS app-builder
WORKDIR /build
COPY {app_dir}/package.json {app_dir}/package-lock.json* ./
RUN npm ci --no-audit --no-fund
COPY {app_dir}/ ./
ENV NEXT_PUBLIC_API_URL=/api/{module_name}
ENV NEXT_PUBLIC_BASE_PATH=/{module_name}
RUN npm run build""")

    # Runtime stage
    rt = f"""\
# ── Runtime ─────────────────────────────────────────────────────
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends \\
    curl ca-certificates{"  nodejs npm" if has_app else ""} \\
    && ARCH=$(dpkg --print-architecture) \\
    && curl -fsSL "https://caddyserver.com/api/download?os=linux&arch=${{ARCH}}" -o /usr/local/bin/caddy \\
    && chmod +x /usr/local/bin/caddy \\
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY config.json ."""

    if has_api:
        rt += f"""

# Python API
COPY {api_dir}/ /app/api/
COPY --from=api-builder /build/deps /app/deps
ENV PYTHONPATH=/app/deps:/app"""

    if has_app:
        rt += f"""

# Next.js app
COPY --from=app-builder /build/.next /app/{app_dir}/.next
COPY --from=app-builder /build/node_modules /app/{app_dir}/node_modules
COPY --from=app-builder /build/package.json /app/{app_dir}/package.json
COPY --from=app-builder /build/next.config.* /app/{app_dir}/"""

    rt += f"""

COPY docker-entrypoint.sh /app/docker-entrypoint.sh
RUN chmod +x /app/docker-entrypoint.sh

ENV GATEWAY_PORT={gateway_port}
EXPOSE {gateway_port}

ENTRYPOINT ["/app/docker-entrypoint.sh"]
"""
    stages.append(rt)
    return "\n\n".join(stages) + "\n"


def entrypoint_B(module_name: str, api_port: int, app_port: int | None,
                 gateway_port: int, api_dir: str | None, app_dir: str | None,
                 python_entrypoint: str = "server") -> str:
    has_app = app_dir is not None and app_port is not None

    api_start = f'cd /app/api && uvicorn {python_entrypoint}:app --host 0.0.0.0 --port $API_PORT &'

    app_block = ""
    if has_app:
        app_block = f"""
# ── Start Next.js ──
cd /app/{app_dir}
NEXT_PUBLIC_API_URL="/api/$MODULE" \\
NEXT_PUBLIC_BASE_PATH="/$MODULE" \\
PORT=$APP_PORT \\
npx next start -p $APP_PORT &
APP_PID=$!
echo "App starting on :$APP_PORT (pid $APP_PID)"
cd /app

for i in $(seq 1 30); do
    if curl -sf http://localhost:$APP_PORT > /dev/null 2>&1; then
        echo "App ready"; break
    fi
    sleep 1
done
"""

    proxy_target = f"localhost:${{{('APP_PORT' if has_app else 'API_PORT')}}}"
    pids_cleanup = "$CADDY_PID $APP_PID $API_PID" if has_app else "$CADDY_PID $API_PID"
    pids_wait = "$API_PID $APP_PID $CADDY_PID" if has_app else "$API_PID $CADDY_PID"

    return f"""\
#!/bin/bash
set -e

API_PORT={api_port}
{f'APP_PORT={app_port}' if has_app else '# No separate app port'}
GATEWAY_PORT="${{GATEWAY_PORT:-{gateway_port}}}"
MODULE="{module_name}"

echo "── $MODULE docker ──"

# ── Start Python API ──
{api_start}
API_PID=$!
echo "API starting on :$API_PORT (pid $API_PID)"

for i in $(seq 1 30); do
    if curl -sf http://localhost:$API_PORT/health > /dev/null 2>&1; then
        echo "API ready"; break
    fi
    sleep 1
done
{app_block}
# ── Caddyfile ──
cat > /app/Caddyfile <<EOF
{{
    admin off
}}

:${{GATEWAY_PORT}} {{
    @api path /api/$MODULE /api/$MODULE/*
    handle @api {{
        uri strip_prefix /api/$MODULE
        reverse_proxy localhost:${{API_PORT}}
    }}
    handle /* {{
        reverse_proxy {proxy_target}
    }}
}}
EOF

caddy run --config /app/Caddyfile &
CADDY_PID=$!
echo "Gateway on :$GATEWAY_PORT (pid $CADDY_PID)"

echo ""
echo "  API:     http://localhost:$API_PORT/health"
{f'echo "  App:     http://localhost:$APP_PORT"' if has_app else ''}
echo "  Gateway: http://localhost:$GATEWAY_PORT"
echo ""

cleanup() {{
    echo "shutting down..."
    kill {pids_cleanup} 2>/dev/null
    wait {pids_cleanup} 2>/dev/null
}}
trap cleanup SIGTERM SIGINT

wait -n {pids_wait}
EXIT_CODE=$?
echo "process exited with code $EXIT_CODE — stopping all"
cleanup
exit $EXIT_CODE
"""


# ---------------------------------------------------------------------------
# Template: Archetype C — Rust root (Cargo.toml) + Next.js (app/)
# ---------------------------------------------------------------------------

def dockerfile_C(module_name: str, api_port: int, app_port: int,
                 gateway_port: int, binary_name: str, app_dir: str) -> str:
    return f"""\
# ── Stage 1: Build Rust API ──────────────────────────────────────
FROM rust:slim AS api-builder
RUN apt-get update && apt-get install -y --no-install-recommends pkg-config libssl-dev && rm -rf /var/lib/apt/lists/*
WORKDIR /build
COPY Cargo.toml Cargo.lock* ./
COPY src/ ./src/
RUN cargo build --release

# ── Stage 2: Build Next.js app ──────────────────────────────────
FROM node:20-slim AS app-builder
WORKDIR /build
COPY {app_dir}/package.json {app_dir}/package-lock.json* ./
RUN npm ci --no-audit --no-fund
COPY {app_dir}/ ./
ENV NEXT_PUBLIC_API_URL=/api/{module_name}
ENV NEXT_PUBLIC_BASE_PATH=/{module_name}
RUN npm run build

# ── Stage 3: Runtime ────────────────────────────────────────────
FROM node:20-slim
RUN apt-get update && apt-get install -y --no-install-recommends \\
    jq curl ca-certificates \\
    && ARCH=$(dpkg --print-architecture) \\
    && curl -fsSL "https://caddyserver.com/api/download?os=linux&arch=${{ARCH}}" -o /usr/local/bin/caddy \\
    && chmod +x /usr/local/bin/caddy \\
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY config.json .

# Rust API binary
COPY --from=api-builder /build/target/release/{binary_name} /app/bin/{binary_name}

# Next.js production build + runtime deps
COPY --from=app-builder /build/.next /app/{app_dir}/.next
COPY --from=app-builder /build/node_modules /app/{app_dir}/node_modules
COPY --from=app-builder /build/package.json /app/{app_dir}/package.json
COPY --from=app-builder /build/next.config.* /app/{app_dir}/

COPY docker-entrypoint.sh /app/docker-entrypoint.sh
RUN chmod +x /app/docker-entrypoint.sh

ENV GATEWAY_PORT={gateway_port}
EXPOSE {gateway_port}

ENTRYPOINT ["/app/docker-entrypoint.sh"]
"""


# Archetype C uses same entrypoint as A
entrypoint_C = entrypoint_A


# ---------------------------------------------------------------------------
# Template: Archetype D — Python-only / m serve
# ---------------------------------------------------------------------------

def entrypoint_D(module_name: str, api_port: int, gateway_port: int) -> str:
    return f"""\
#!/bin/bash
set -e

API_PORT={api_port}
GATEWAY_PORT="${{GATEWAY_PORT:-{gateway_port}}}"
MODULE="{module_name}"

echo "── $MODULE docker ──"

# ── Start m serve ──
m serve port=$API_PORT key=$MODULE remote=0 mod=$MODULE &
API_PID=$!
echo "API starting on :$API_PORT (pid $API_PID)"

for i in $(seq 1 30); do
    if curl -sf http://localhost:$API_PORT/health > /dev/null 2>&1; then
        echo "API ready"; break
    fi
    sleep 1
done

# ── Caddyfile ──
cat > /tmp/Caddyfile <<EOF
{{
    admin off
}}

:${{GATEWAY_PORT}} {{
    @api path /api/$MODULE /api/$MODULE/*
    handle @api {{
        uri strip_prefix /api/$MODULE
        reverse_proxy localhost:${{API_PORT}}
    }}
    handle /* {{
        reverse_proxy localhost:${{API_PORT}}
    }}
}}
EOF

caddy run --config /tmp/Caddyfile &
CADDY_PID=$!
echo "Gateway on :$GATEWAY_PORT (pid $CADDY_PID)"

echo ""
echo "  API:     http://localhost:$API_PORT/health"
echo "  Gateway: http://localhost:$GATEWAY_PORT"
echo ""

cleanup() {{
    echo "shutting down..."
    kill $CADDY_PID $API_PID 2>/dev/null
    wait $CADDY_PID $API_PID 2>/dev/null
}}
trap cleanup SIGTERM SIGINT

wait -n $API_PID $CADDY_PID
EXIT_CODE=$?
echo "process exited with code $EXIT_CODE — stopping all"
cleanup
exit $EXIT_CODE
"""


# ---------------------------------------------------------------------------
# docker-compose.yml templates
# ---------------------------------------------------------------------------

def compose_self_contained(module_name: str, gateway_port: int) -> str:
    return f"""\
networks:
  default:
    external: true
    name: modnet

services:
  {module_name}:
    build: .
    container_name: {module_name}
    ports:
      - "${{GATEWAY_PORT:-{gateway_port}}}:${{GATEWAY_PORT:-{gateway_port}}}"
    restart: unless-stopped
    environment:
      - GATEWAY_PORT=${{GATEWAY_PORT:-{gateway_port}}}
    volumes:
      - {module_name}-data:/app/data

volumes:
  {module_name}-data:
"""


def compose_mod_latest(module_name: str, api_port: int, gateway_port: int) -> str:
    return f"""\
networks:
  default:
    external: true
    name: modnet

services:
  {module_name}:
    container_name: {module_name}
    image: mod:latest
    ports:
      - "${{GATEWAY_PORT:-{gateway_port}}}:${{GATEWAY_PORT:-{gateway_port}}}"
    restart: unless-stopped
    environment:
      - GATEWAY_PORT=${{GATEWAY_PORT:-{gateway_port}}}
    volumes:
      - ~/mod:/root/mod:ro
      - ~/.mod:/root/.mod
    working_dir: /root/mod/mod/orbit/{module_name}
    entrypoint: ["bash", "/root/mod/mod/orbit/{module_name}/docker-entrypoint.sh"]
"""


# ---------------------------------------------------------------------------
# Main generation logic
# ---------------------------------------------------------------------------

def generate_module(mod_dir: Path, force: bool = False, dry_run: bool = False) -> dict:
    """Generate Docker files for a single module. Returns a summary dict."""
    module_name = mod_dir.name
    config_path = mod_dir / "config.json"

    if not config_path.exists():
        return {"module": module_name, "status": "skipped", "reason": "no config.json"}

    try:
        config = json.loads(config_path.read_text())
    except (json.JSONDecodeError, Exception) as e:
        return {"module": module_name, "status": "error", "reason": f"bad config.json: {e}"}

    api_port, app_port, gateway_port = read_ports(config)

    if api_port is None and app_port is None:
        return {"module": module_name, "status": "skipped", "reason": "no port assignments"}

    if api_port is None:
        api_port = app_port
    if gateway_port is None:
        gateway_port = app_port or api_port

    archetype = detect_archetype(mod_dir)
    app_dir = find_app_dir(mod_dir)
    api_dir = find_api_dir(mod_dir)

    files = {}  # filename -> content

    if archetype == "A":
        binary_name = find_rust_binary_name(mod_dir / "src" / "api")
        ad = app_dir or "src/app"
        files["Dockerfile"] = dockerfile_A(module_name, api_port, app_port or api_port, gateway_port, binary_name, ad)
        files["docker-entrypoint.sh"] = entrypoint_A(module_name, api_port, app_port or api_port, gateway_port, binary_name, ad)
        files["docker-compose.yml"] = compose_self_contained(module_name, gateway_port)

    elif archetype == "B":
        python_ep = "server"
        if api_dir:
            python_ep = find_python_entrypoint(mod_dir / api_dir)
        files["Dockerfile"] = dockerfile_B(module_name, api_port, app_port, gateway_port, api_dir, app_dir)
        files["docker-entrypoint.sh"] = entrypoint_B(module_name, api_port, app_port, gateway_port, api_dir, app_dir, python_ep)
        files["docker-compose.yml"] = compose_self_contained(module_name, gateway_port)

    elif archetype == "C":
        binary_name = find_rust_binary_name(mod_dir)
        ad = app_dir or "app"
        files["Dockerfile"] = dockerfile_C(module_name, api_port, app_port or api_port, gateway_port, binary_name, ad)
        files["docker-entrypoint.sh"] = entrypoint_C(module_name, api_port, app_port or api_port, gateway_port, binary_name, ad)
        files["docker-compose.yml"] = compose_self_contained(module_name, gateway_port)

    elif archetype == "D":
        files["docker-entrypoint.sh"] = entrypoint_D(module_name, api_port, gateway_port)
        files["docker-compose.yml"] = compose_mod_latest(module_name, api_port, gateway_port)

    # Write files
    written = []
    skipped = []
    for filename, content in files.items():
        filepath = mod_dir / filename
        if filepath.exists() and not force:
            skipped.append(filename)
            continue
        if dry_run:
            written.append(f"{filename} (dry-run)")
            continue
        filepath.write_text(content)
        if filename.endswith(".sh"):
            filepath.chmod(filepath.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        written.append(filename)

    return {
        "module": module_name,
        "status": "ok",
        "archetype": archetype,
        "api_port": api_port,
        "app_port": app_port,
        "gateway_port": gateway_port,
        "app_dir": app_dir,
        "api_dir": api_dir,
        "written": written,
        "skipped": skipped,
    }


def main():
    parser = argparse.ArgumentParser(description="Generate Docker files for mod orbit modules")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--all", action="store_true", help="Generate for all modules with ports")
    group.add_argument("--module", type=str, help="Generate for a single module")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    parser.add_argument("--force", action="store_true", help="Overwrite existing files")
    args = parser.parse_args()

    if not ORBIT_DIR.exists():
        print(f"Error: orbit directory not found at {ORBIT_DIR}", file=sys.stderr)
        sys.exit(1)

    if args.module:
        mod_dir = ORBIT_DIR / args.module
        if not mod_dir.exists():
            print(f"Error: module '{args.module}' not found at {mod_dir}", file=sys.stderr)
            sys.exit(1)
        results = [generate_module(mod_dir, force=args.force, dry_run=args.dry_run)]
    else:
        results = []
        for mod_dir in sorted(ORBIT_DIR.iterdir()):
            if not mod_dir.is_dir():
                continue
            if mod_dir.name.startswith(".") or mod_dir.name == "__pycache__":
                continue
            result = generate_module(mod_dir, force=args.force, dry_run=args.dry_run)
            results.append(result)

    # Print summary
    ok = [r for r in results if r["status"] == "ok"]
    skipped = [r for r in results if r["status"] == "skipped"]
    errors = [r for r in results if r["status"] == "error"]

    print(f"\n{'=' * 60}")
    print(f"dockerize summary {'(DRY RUN)' if args.dry_run else ''}")
    print(f"{'=' * 60}")

    if ok:
        print(f"\n  Generated: {len(ok)} modules\n")
        for r in ok:
            w = ", ".join(r["written"]) if r["written"] else "(all exist, use --force)"
            s = f" [skipped: {', '.join(r['skipped'])}]" if r["skipped"] else ""
            print(f"    [{r['archetype']}] {r['module']:20s} "
                  f"gateway={r['gateway_port']:5d}  "
                  f"api={r['api_port'] or '-':>5}  "
                  f"app={r['app_port'] or '-':>5}  "
                  f"→ {w}{s}")

    if skipped:
        print(f"\n  Skipped: {len(skipped)} modules (no ports)")

    if errors:
        print(f"\n  Errors: {len(errors)} modules")
        for r in errors:
            print(f"    {r['module']}: {r['reason']}")

    print()


if __name__ == "__main__":
    main()
