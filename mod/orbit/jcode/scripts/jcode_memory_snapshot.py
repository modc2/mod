#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

DEFAULT_SOCKET = f"/run/user/{os.getuid()}/jcode.sock"


@dataclass
class ProcMem:
    pid: int
    role: str
    cmd: str
    session_id: str | None
    socket_path: str | None
    rss_mb: float
    pss_mb: float
    anon_mb: float
    shared_clean_mb: float
    private_clean_mb: float
    private_dirty_mb: float
    swap_mb: float


@dataclass
class Totals:
    count: int
    rss_mb: float
    pss_mb: float
    anon_mb: float
    private_dirty_mb: float
    shared_clean_mb: float
    swap_mb: float


SMAPS_KEYS = {
    "Rss": "rss_mb",
    "Pss": "pss_mb",
    "Anonymous": "anon_mb",
    "Shared_Clean": "shared_clean_mb",
    "Private_Clean": "private_clean_mb",
    "Private_Dirty": "private_dirty_mb",
    "Swap": "swap_mb",
}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Summarize jcode server/client process memory using smaps_rollup")
    p.add_argument("--json", action="store_true", help="Print JSON instead of a human summary")
    p.add_argument(
        "--include-aux",
        action="store_true",
        help="Include non-default-socket helper/test jcode processes in the output",
    )
    p.add_argument(
        "--socket",
        default=DEFAULT_SOCKET,
        help=f"Main jcode socket to treat as the primary instance (default: {DEFAULT_SOCKET})",
    )
    return p.parse_args()


def read_text(path: Path, binary: bool = False) -> str | None:
    try:
        if binary:
            return path.read_bytes().replace(b"\x00", b" ").decode("utf-8", "ignore").strip()
        return path.read_text().strip()
    except Exception:
        return None


def read_argv(path: Path) -> list[str] | None:
    try:
        raw = path.read_bytes()
    except Exception:
        return None
    if not raw:
        return None
    return [part.decode("utf-8", "ignore") for part in raw.split(b"\x00") if part]


def parse_socket_path(cmd: str) -> str | None:
    m = re.search(r"(?:^| )--socket\s+(\S+)", cmd)
    return m.group(1) if m else None


def parse_session_id(cmd: str) -> str | None:
    m = re.search(r"--resume\s+(session_[^\s]+)", cmd)
    return m.group(1) if m else None


def first_non_option(argv: list[str]) -> str | None:
    skip_next = False
    for arg in argv[1:]:
        if skip_next:
            skip_next = False
            continue
        if arg in {"--socket", "--resume", "-C", "--chdir", "--model", "--provider"}:
            skip_next = True
            continue
        if arg.startswith("-"):
            continue
        return arg
    return None


def classify_process(argv: list[str], cmd: str, main_socket: str) -> tuple[str | None, bool]:
    argv0 = Path(argv[0]).name if argv else ""
    if not argv0.startswith("jcode"):
        return None, False

    if "jcode serve" in cmd:
        socket_path = parse_socket_path(cmd) or main_socket
        if socket_path == main_socket:
            return "server", True
        return "server_aux", False

    socket_path = parse_socket_path(cmd) or main_socket
    is_main = socket_path == main_socket

    if "cargo build" in cmd or "rustc --crate-name jcode" in cmd or "handle_resume_session" in cmd:
        return None, False

    subcommand = first_non_option(argv)
    if subcommand in {"auth", "login", "logout", "serve", "self-dev"} and "--resume session_" not in cmd:
        if subcommand == "self-dev" and "--resume session_" in cmd:
            pass
        else:
            return None, False

    if "--resume session_" in cmd or " --fresh-spawn " in cmd:
        return ("client_session" if is_main else "client_aux"), is_main

    return ("client_interactive" if is_main else "client_aux"), is_main


def parse_smaps_rollup(pid: int) -> dict[str, float] | None:
    path = Path(f"/proc/{pid}/smaps_rollup")
    txt = read_text(path)
    if not txt:
        return None
    out = {value: 0.0 for value in SMAPS_KEYS.values()}
    for line in txt.splitlines():
        for key, out_key in SMAPS_KEYS.items():
            if line.startswith(f"{key}:"):
                parts = line.split()
                if len(parts) >= 2:
                    out[out_key] = round(int(parts[1]) / 1024.0, 1)
    return out


def iter_jcode_processes(main_socket: str, include_aux: bool) -> Iterable[ProcMem]:
    for pid_dir in Path("/proc").iterdir():
        if not pid_dir.name.isdigit():
            continue
        pid = int(pid_dir.name)
        argv = read_argv(pid_dir / "cmdline")
        if not argv:
            continue
        cmd = " ".join(argv)
        if "jcode" not in cmd:
            continue
        role, is_main = classify_process(argv, cmd, main_socket)
        if role is None:
            continue
        if not include_aux and not is_main and role not in {"server"}:
            continue
        smaps = parse_smaps_rollup(pid)
        if not smaps:
            continue
        yield ProcMem(
            pid=pid,
            role=role,
            cmd=cmd,
            session_id=parse_session_id(cmd),
            socket_path=parse_socket_path(cmd),
            **smaps,
        )


def sum_totals(procs: list[ProcMem]) -> Totals:
    return Totals(
        count=len(procs),
        rss_mb=round(sum(p.rss_mb for p in procs), 1),
        pss_mb=round(sum(p.pss_mb for p in procs), 1),
        anon_mb=round(sum(p.anon_mb for p in procs), 1),
        private_dirty_mb=round(sum(p.private_dirty_mb for p in procs), 1),
        shared_clean_mb=round(sum(p.shared_clean_mb for p in procs), 1),
        swap_mb=round(sum(p.swap_mb for p in procs), 1),
    )


def print_human(server: list[ProcMem], clients: list[ProcMem], aux: list[ProcMem]) -> None:
    def print_group(name: str, procs: list[ProcMem]) -> None:
        totals = sum_totals(procs)
        print(f"\n{name} ({totals.count})")
        print("-" * len(f"{name} ({totals.count})"))
        print(
            f"total: RSS {totals.rss_mb:.1f} MB | PSS {totals.pss_mb:.1f} MB | "
            f"Anon {totals.anon_mb:.1f} MB | Private dirty {totals.private_dirty_mb:.1f} MB"
        )
        for p in sorted(procs, key=lambda x: (x.role, -x.pss_mb, x.pid)):
            label = p.session_id or p.role
            print(
                f"  pid {p.pid:<7} {label:<48} RSS {p.rss_mb:>6.1f} | PSS {p.pss_mb:>6.1f} | "
                f"Anon {p.anon_mb:>6.1f} | PrivDirty {p.private_dirty_mb:>6.1f}"
            )

    print_group("Server", server)
    print_group("Clients", clients)
    if aux:
        print_group("Auxiliary", aux)

    grand = sum_totals(server + clients)
    print("\nPrimary total (server + clients)")
    print("--------------------------------")
    print(
        f"RSS {grand.rss_mb:.1f} MB | PSS {grand.pss_mb:.1f} MB | "
        f"Anon {grand.anon_mb:.1f} MB | Private dirty {grand.private_dirty_mb:.1f} MB | "
        f"Shared clean {grand.shared_clean_mb:.1f} MB"
    )
    print(
        f"Overcount if you sum RSS instead of PSS: {round(grand.rss_mb - grand.pss_mb, 1):.1f} MB"
    )


def main() -> int:
    args = parse_args()
    procs = list(iter_jcode_processes(args.socket, args.include_aux))
    server = [p for p in procs if p.role == "server"]
    clients = [p for p in procs if p.role.startswith("client_") and p.role != "client_aux"]
    aux = [p for p in procs if p.role.endswith("aux")]

    if args.json:
        payload = {
            "socket": args.socket,
            "server": [asdict(p) for p in sorted(server, key=lambda x: x.pid)],
            "clients": [asdict(p) for p in sorted(clients, key=lambda x: x.pid)],
            "auxiliary": [asdict(p) for p in sorted(aux, key=lambda x: x.pid)],
            "totals": {
                "server": asdict(sum_totals(server)),
                "clients": asdict(sum_totals(clients)),
                "primary": asdict(sum_totals(server + clients)),
                "auxiliary": asdict(sum_totals(aux)),
            },
        }
        print(json.dumps(payload, indent=2))
    else:
        print_human(server, clients, aux)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
