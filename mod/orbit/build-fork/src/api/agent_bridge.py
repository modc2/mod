#!/usr/bin/env python3
"""agent_bridge — mint this console's own mod protocol-auth token.

The ✦ backend submits jobs to the orbit/agent module over HTTP, and that
module authenticates callers with a *signed, time-bounded* envelope
({data, time, key, signature}, base64url) rather than a bearer string. The
core auth mod rejects one older than its max_age (24h by default), so a token
pasted once in the console's AGENT tab stops working a day later — the run
then fails with agent's own "Permission denied: 'run' requires admin access",
which reads like a grant problem and isn't one.

So the server mints a fresh one per run instead of holding a stale one. The
Rust API can't sign with the host key in-process, so it shells out here, the
same way job completion shells out to store_bridge.py. This is the SERVER's
identity — the fallback a trusted job uses when the submitter hasn't
connected a session of their own, exactly like claude jobs falling back to
root's subscription. Sandboxed (peer) jobs never get it.

    agent_bridge.py mint [scope]   → {"token": "...", "address": "0x…", "ttl": 86400}
    agent_bridge.py whoami         → {"address": "0x…"}

stdout is pure JSON; imported mods print banners, so their output is routed
to stderr until the result is emitted.
"""

import json
import os
import sys


def _emit_setup():
    """Swap stdout out so mod import banners can't corrupt the JSON result."""
    out = sys.stdout
    sys.stdout = sys.stderr

    def emit(obj) -> None:
        print(json.dumps(obj), file=out)

    return emit


def _load_auth(emit):
    sys.path.insert(0, os.path.expanduser("~/mod"))
    try:
        import mod as m
    except Exception as e:
        emit({"error": f"mod import failed: {e}"})
        return None
    try:
        return m.mod("auth")()
    except Exception as e:
        emit({"error": f"auth mod unavailable: {e}"})
        return None


def mint(scope: str) -> int:
    emit = _emit_setup()
    auth = _load_auth(emit)
    if auth is None:
        return 1
    try:
        token = auth.token({"scope": scope}, mod="str")
    except Exception as e:
        emit({"error": f"sign failed: {e}"})
        return 1
    emit({
        "token": token,
        "address": getattr(auth.key, "address", ""),
        # What the verifier will accept, so the caller can cache below it.
        "ttl": int(getattr(auth, "max_age", 86400)),
    })
    return 0


def whoami() -> int:
    emit = _emit_setup()
    auth = _load_auth(emit)
    if auth is None:
        return 1
    emit({"address": getattr(auth.key, "address", "")})
    return 0


def main() -> int:
    argv = sys.argv[1:]
    cmd = argv[0] if argv else "mint"
    if cmd == "mint":
        return mint(argv[1] if len(argv) > 1 else "agent")
    if cmd == "whoami":
        return whoami()
    print(json.dumps({"error": f"unknown command '{cmd}'"}))
    return 1


if __name__ == "__main__":
    sys.exit(main())
