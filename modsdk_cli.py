"""Root CLI entry point for the ModNet SDK.

This script aggregates operational commands for chain and IPFS services,
providing a single interface for managing local infrastructure.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path
from typing import Iterable, Sequence
import subprocess

REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_ENV_FILE = REPO_ROOT / ".env"


class ModsdkCliError(Exception):
    """Raised when the CLI encounters an unrecoverable condition."""


def load_project_env(env_path: Path = DEFAULT_ENV_FILE) -> None:
    """Load key=value pairs from the project .env into os.environ if available."""

    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")

        if key and key not in os.environ:
            os.environ[key] = value


def ensure_script(script_path: Path) -> Path:
    """Validate that a script exists and is executable."""

    if not script_path.exists():
        raise ModsdkCliError(f"Script not found: {script_path}")
    if not os.access(script_path, os.X_OK):
        raise ModsdkCliError(f"Script is not executable: {script_path}")
    return script_path


def exec_script(script_path: Path, args: Iterable[str]) -> None:
    """Replace the current process with the given script and arguments."""

    script = ensure_script(script_path)
    argv = [str(script), *args]
    os.execv(str(script), argv)


def run_subprocess(command: Sequence[str]) -> None:
    """Run a subprocess command, surfacing errors clearly."""

    try:
        subprocess.run(command, check=True)
    except subprocess.CalledProcessError as exc:
        raise ModsdkCliError(
            f"Command {' '.join(command)} failed with exit code {exc.returncode}"
        ) from exc


def ensure_pm2_available() -> None:
    """Ensure pm2 is installed and accessible."""

    if shutil.which("pm2") is None:
        raise ModsdkCliError(
            "pm2 is required for service management. Install via `npm install -g pm2`."
        )


def forward_to_ipfs(argv: list[str]) -> None:
    """Forward CLI invocations to the IPFS service CLI."""

    if not argv:
        argv = ["--help"]

    ipfs_service_root = REPO_ROOT / "ipfs-service"
    if not ipfs_service_root.exists():
        raise ModsdkCliError("ipfs-service repository is not present. Initialise submodules first.")

    sys.path.insert(0, str(ipfs_service_root))
    try:
        from app.cli import main as ipfs_main  # type: ignore import-not-found

        ipfs_main(argv)
    finally:
        try:
            sys.path.remove(str(ipfs_service_root))
        except ValueError:
            pass


def handle_ipfs(args: argparse.Namespace) -> None:
    """Handle IPFS command forwarding."""

    load_project_env()
    forward_to_ipfs(args.ipfs_args)


def handle_chain_start_node(args: argparse.Namespace) -> None:
    """Start a ModNet node using the chain script."""

    load_project_env()
    script = REPO_ROOT / "chain" / "scripts" / "start_node.sh"
    exec_script(script, args.extra or [])


def handle_chain_start_validator(args: argparse.Namespace) -> None:
    """Start a ModNet validator node using the chain script."""

    load_project_env()
    script = REPO_ROOT / "chain" / "scripts" / "start_validator_node.sh"
    exec_script(script, args.extra or [])


def handle_services_start(_: argparse.Namespace) -> None:
    """Start background services using pm2."""

    load_project_env()
    ensure_pm2_available()

    ipfs_service_root = REPO_ROOT / "ipfs-service"
    if not ipfs_service_root.exists():
        raise ModsdkCliError(
            "ipfs-service repository is not present. Initialise submodules first."
        )

    chain_script = REPO_ROOT / "chain" / "scripts" / "start_node.sh"
    if not chain_script.exists():
        raise ModsdkCliError("chain/scripts/start_node.sh not found. Build the chain project first.")

    module_api_root = REPO_ROOT / "mcp-registrar"
    if not (module_api_root / "Cargo.toml").exists():
        raise ModsdkCliError("mcp-registrar/Cargo.toml not found. Ensure submodule is initialised.")

    print("[modsdk] Starting IPFS daemon via pm2 (process name: ipfs-daemon)")
    run_subprocess([
        "pm2",
        "start",
        "ipfs",
        "--name",
        "ipfs-daemon",
        "--",
        "daemon",
    ])

    print("[modsdk] Starting IPFS worker via pm2 (process name: ipfs-service)")
    run_subprocess([
        "pm2",
        "start",
        "uv",
        "--name",
        "ipfs-service",
        "--cwd",
        str(ipfs_service_root),
        "--",
        "run",
        "main.py",
    ])

    print("[modsdk] Starting chain node via pm2 (process name: chain-node)")
    run_subprocess([
        "pm2",
        "start",
        str(chain_script),
        "--name",
        "chain-node",
        "--interpreter",
        "bash",
        "--cwd",
        str(REPO_ROOT),
    ])

    print("[modsdk] Starting module API via pm2 (process name: module-api)")
    run_subprocess([
        "pm2",
        "start",
        "cargo",
        "--name",
        "module-api",
        "--cwd",
        str(module_api_root),
        "--",
        "run",
        "--bin",
        "module-api",
    ])


def handle_services_stop(_: argparse.Namespace) -> None:
    """Stop pm2 managed services."""

    load_project_env()
    ensure_pm2_available()

    print("[modsdk] Stopping pm2 process: module-api")
    try:
        run_subprocess(["pm2", "stop", "module-api"])
    except ModsdkCliError as exc:
        print(f"warning: {exc}")

    print("[modsdk] Stopping pm2 process: chain-node")
    try:
        run_subprocess(["pm2", "stop", "chain-node"])
    except ModsdkCliError as exc:
        print(f"warning: {exc}")

    print("[modsdk] Stopping pm2 process: ipfs-service")
    run_subprocess(["pm2", "stop", "ipfs-service"])

    print("[modsdk] Stopping pm2 process: ipfs-daemon")
    run_subprocess(["pm2", "stop", "ipfs-daemon"])


def handle_services_status(_: argparse.Namespace) -> None:
    """Show status of pm2 managed services."""

    load_project_env()
    ensure_pm2_available()

    run_subprocess(["pm2", "status", "ipfs-daemon", "ipfs-service", "chain-node", "module-api"])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="modsdk", description="ModNet SDK management CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    ipfs_parser = subparsers.add_parser("ipfs", help="Manage IPFS services")
    ipfs_parser.add_argument(
        "ipfs_args",
        nargs=argparse.REMAINDER,
        help="Arguments forwarded to the ipfs-service CLI",
    )
    ipfs_parser.set_defaults(func=handle_ipfs)

    chain_parser = subparsers.add_parser("chain", help="Manage ModNet chain services")
    chain_sub = chain_parser.add_subparsers(dest="chain_command", required=True)

    chain_start = chain_sub.add_parser("start-node", help="Start a local ModNet node")
    chain_start.add_argument(
        "extra",
        nargs=argparse.REMAINDER,
        help="Additional arguments forwarded to start_node.sh",
    )
    chain_start.set_defaults(func=handle_chain_start_node)

    chain_validator = chain_sub.add_parser(
        "start-validator", help="Start a local ModNet validator node"
    )
    chain_validator.add_argument(
        "extra",
        nargs=argparse.REMAINDER,
        help="Additional arguments forwarded to start_validator_node.sh",
    )
    chain_validator.set_defaults(func=handle_chain_start_validator)

    services_parser = subparsers.add_parser(
        "services", help="Manage persistent background services via pm2"
    )
    services_sub = services_parser.add_subparsers(dest="services_command", required=True)

    services_start = services_sub.add_parser("start", help="Start pm2 managed services")
    services_start.set_defaults(func=handle_services_start)

    services_stop = services_sub.add_parser("stop", help="Stop pm2 managed services")
    services_stop.set_defaults(func=handle_services_stop)

    services_status = services_sub.add_parser(
        "status", help="Show pm2 status for managed services"
    )
    services_status.set_defaults(func=handle_services_status)

    return parser


def main(argv: list[str] | None = None) -> None:
    argv = argv if argv is not None else sys.argv[1:]
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        args.func(args)
    except ModsdkCliError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
