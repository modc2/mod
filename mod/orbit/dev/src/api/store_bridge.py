#!/usr/bin/env python3
"""store_bridge — publish one build task into localfs + the store index.

The Rust API can't call python mod fns in-process, so job completion shells
out to this bridge. stdin: a JSON envelope {"task": {...}, "owner": "0x..",
"key": "build-task-xxxx.json"}. The task bundle is put into localfs (the
shared content-addressed store, IPFS-compatible CIDs) and registered in the
dstore index with backend=localfs, so it shows up as a first-class object in
the store module for its owner. An ACL row marks it private to that owner
(store objects with no ACL read as public). stdout: {"cid": "..."} or
{"error": "..."}. Idempotent: same bundle → same CID; an existing index row
for (cid, owner) is not duplicated.

Second mode — `store_bridge.py get <cid>`: fetch a task bundle back out of
localfs by CID and print it as JSON. Powers GET /tasks/:cid (the replay QR),
including for bundles minted by another console that shares the blob store.
"""

import json
import os
import sys


def get_by_cid(cid: str) -> int:
    """Print the localfs object for `cid` as one JSON line on stdout."""
    out = sys.stdout
    sys.stdout = sys.stderr  # keep stdout pure JSON (imported mods print banners)

    def emit(obj) -> None:
        print(json.dumps(obj), file=out)

    sys.path.insert(0, os.path.expanduser("~/mod"))
    try:
        import mod as m
    except Exception as e:
        emit({"error": f"mod import failed: {e}"})
        return 1
    try:
        localfs = m.mod("localfs")()
        data = localfs.get(cid)
    except Exception as e:
        emit({"error": f"localfs get failed: {e}"})
        return 1
    if data is None:
        emit({"error": "not found"})
        return 1
    if isinstance(data, (bytes, bytearray)):
        try:
            data = json.loads(data.decode("utf-8"))
        except Exception:
            emit({"error": "blob is not JSON"})
            return 1
    emit(data)
    return 0


def main() -> int:
    try:
        envelope = json.load(sys.stdin)
    except Exception as e:
        print(json.dumps({"error": f"bad envelope: {e}"}))
        return 1

    task = envelope.get("task")
    owner = (envelope.get("owner") or "").strip()
    key = envelope.get("key") or "build-task.json"
    if not isinstance(task, dict):
        print(json.dumps({"error": "envelope needs a 'task' object"}))
        return 1

    # Imported mods print banner noise to stdout ("[localfs] Rust bindings…");
    # our stdout must stay pure JSON for the Rust caller, so swap the real
    # stdout out and route stray prints to stderr until we emit the result.
    out = sys.stdout
    sys.stdout = sys.stderr

    def emit(obj) -> None:
        print(json.dumps(obj), file=out)

    sys.path.insert(0, os.path.expanduser("~/mod"))
    try:
        import mod as m
    except Exception as e:
        emit({"error": f"mod import failed: {e}"})
        return 1

    try:
        localfs = m.mod("localfs")()
        cid = localfs.put(task)
    except Exception as e:
        emit({"error": f"localfs put failed: {e}"})
        return 1

    size = len(json.dumps(task).encode("utf-8"))
    registered = False
    try:
        ds = m.mod("dstore")()
        conn = ds._db()
        row = conn.execute(
            "SELECT 1 FROM objects WHERE cid=? LIMIT 1", (cid,)
        ).fetchone()
        conn.close()
        if not row:
            ds.register(cid=cid, backend="localfs", owner=owner or None,
                        key=key, size=size, scheme="ipfs")
            registered = True
    except Exception as e:
        # The CID exists in localfs either way; index failure is degradation,
        # not loss — surface it but still return the CID.
        emit({"cid": cid, "warning": f"store index failed: {e}"})
        return 0

    # Private-by-default in the store: without an ACL row the object reads as
    # public. Owned tasks get an owner-scoped private ACL; local-mode tasks
    # (no owner) stay unrestricted.
    if owner:
        try:
            import importlib.util as ilu
            access_py = os.path.expanduser("~/mod/mod/core/store/api/access.py")
            spec = ilu.spec_from_file_location("_store_access", access_py)
            acc_mod = ilu.module_from_spec(spec)
            spec.loader.exec_module(acc_mod)
            acc = acc_mod.Access(os.path.expanduser("~/.mod/store/access.db"))
            acc.set_acl(cid, owner=owner, scheme="ipfs", backend="localfs",
                        visibility="private")
        except Exception:
            pass

    emit({"cid": cid, "registered": registered, "size": size})
    return 0


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "get":
        sys.exit(get_by_cid(sys.argv[2]))
    sys.exit(main())
