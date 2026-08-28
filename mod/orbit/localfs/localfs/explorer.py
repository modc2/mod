"""
Explorer index for localfs.

LocalFS is a flat pile of content-addressed blocks — there is no listing, no
names, no ordering. To browse it you need to know what each block *is*, and
that only comes from looking inside. This module walks the block store once,
sniffs each block's content kind, extracts a human-readable label and the CIDs
it references, and keeps the result in SQLite so the UI can page, sort, filter
and search 40k+ blocks instantly.

The store also turns out to contain its own directory structure: modules are
snapshotted as *manifest* blocks — flat JSON maps of `"path/to/file": "Qm…"`.
Indexing those gives real filenames and a browsable tree on top of what is
otherwise an anonymous blob pile, so we pull them out too.

The index is a cache — it is always rebuildable from the store and never the
source of truth. It lives off-tree in ~/.mod/localfs/.
"""

import json
import os
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Bump when the indexed shape changes — the index is a cache, so a mismatch
# just means "rebuild it".
SCHEMA_VERSION = 3

# How much of a block we read to classify it and pull out its label.
SNIFF_BYTES = 4096
# How much we scan for referenced CIDs (link graph). Bounded so one huge block
# can't stall a rebuild.
REF_SCAN_BYTES = 512 * 1024
# Manifests carry the store's only filenames, so JSON gets read in full.
JSON_SCAN_BYTES = 16 * 1024 * 1024
# Stored per block for search + list previews.
PREVIEW_CHARS = 400

CID = r"Qm[1-9A-HJ-NP-Za-km-z]{44}|baf[a-z2-7]{50,}"
CID_RE = re.compile(rf"\b({CID})\b")
# "src/app/page.tsx": "Qm…"  — a manifest entry, i.e. a named block.
NAMED_CID_RE = re.compile(rf'"([^"\\\n]{{1,255}})"\s*:\s*"({CID})"')

_MAGIC: List[Tuple[bytes, str, str]] = [
    (b"\x89PNG\r\n\x1a\n", "image", "image/png"),
    (b"\xff\xd8\xff", "image", "image/jpeg"),
    (b"GIF87a", "image", "image/gif"),
    (b"GIF89a", "image", "image/gif"),
    (b"BM", "image", "image/bmp"),
    (b"%PDF-", "pdf", "application/pdf"),
    (b"PK\x03\x04", "archive", "application/zip"),
    (b"\x1f\x8b", "archive", "application/gzip"),
    (b"BZh", "archive", "application/x-bzip2"),
    (b"\xfd7zXZ\x00", "archive", "application/x-xz"),
    (b"\x7fELF", "binary", "application/x-elf"),
    (b"\x00asm", "wasm", "application/wasm"),
    (b"SQLite format 3\x00", "binary", "application/vnd.sqlite3"),
]

MIME_BY_KIND = {
    "json": "application/json",
    "markdown": "text/markdown",
    "html": "text/html",
    "svg": "image/svg+xml",
    "css": "text/css",
    "csv": "text/csv",
    "wasm": "application/wasm",
    "pdf": "application/pdf",
    "archive": "application/octet-stream",
    "binary": "application/octet-stream",
}


def sniff(head: bytes, size: int) -> Tuple[str, str]:
    """Classify a block from its first bytes → (kind, mime)."""
    for magic, kind, mime in _MAGIC:
        if head.startswith(magic):
            return kind, mime
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image", "image/webp"

    try:
        text = head.decode("utf-8")
    except UnicodeDecodeError:
        # A cut multi-byte char at the boundary is not a binary signal.
        try:
            text = head[:-4].decode("utf-8")
        except UnicodeDecodeError:
            return "binary", "application/octet-stream"
    if "\x00" in text:
        return "binary", "application/octet-stream"

    s = text.lstrip()
    low = s.lower()

    if s[:1] in "{[":
        return "json", "application/json"
    if low.startswith("<?xml") or low.startswith("<svg"):
        return ("svg", "image/svg+xml") if "<svg" in low[:400] else ("xml", "text/xml")
    if low.startswith("<!doctype html") or low.startswith("<html"):
        return "html", "text/html"
    if s.startswith("#!"):
        shebang = s.split("\n", 1)[0]
        if "python" in shebang:
            return "python", "text/x-python"
        if "node" in shebang:
            return "javascript", "text/javascript"
        return "shell", "text/x-shellscript"

    lines = [ln for ln in s.split("\n")[:60]]

    def has(*pats: str) -> bool:
        return any(any(p in ln for p in pats) for ln in lines)

    def starts(*pats: str) -> bool:
        return any(ln.lstrip().startswith(pats) for ln in lines)

    # C family before markdown — `#include` / `#pragma` also start with '#'.
    if starts("#include", "#pragma", "#ifndef", "#ifdef", "#if !defined"):
        return "c", "text/x-c"
    if s.startswith("---\n") or starts("# ") or has("\n## ", "\n### "):
        return "markdown", "text/markdown"
    if starts("pub fn", "pub struct", "pub type", "pub mod", "impl ", "fn ", "use ", "#[") \
            and has(";", "{"):
        return "rust", "text/x-rust"
    if starts("package ") and has("func "):
        return "go", "text/x-go"
    # TypeScript/JS before Python: `import x` opens both, but only JS imports
    # quote their module, and only TS annotates types.
    if has("import type ", "interface ", ": React.", "<reference types=", ") : ") \
            or (has("from '", 'from "') and has(": ", "=>")):
        if has("import type ", "interface ", "<reference types=", ": string", ": number",
               ": boolean", "<Props", "as const"):
            return "typescript", "text/typescript"
    if has("from '", 'from "', "=>", "module.exports", "require(", "export default",
           "export const", "export function", "'use client'", "console.log("):
        return "javascript", "text/javascript"
    if starts("def ", "class ", "import ", "from ", "@", "async def ", '"""') \
            and has("def ", "self.", "import ", "__name__", "print("):
        return "python", "text/x-python"
    if starts("SELECT", "CREATE TABLE", "INSERT INTO", "select ", "create table"):
        return "sql", "text/x-sql"
    if starts("[") and has(" = "):
        return "toml", "text/x-toml"
    if re.match(r"^[\w.-]+:\s*(\S|$)", s) and has(": ", "- "):
        return "yaml", "text/yaml"
    if starts(".", "#", "@media", ":root") and has("{", "}") and has(":", ";"):
        return "css", "text/css"
    return "text", "text/plain"


def label_for(kind: str, head: bytes, size: int) -> str:
    """A one-line human handle for a block — what the row says it is."""
    if kind in ("binary", "archive", "image", "pdf", "wasm"):
        return f"{kind} · {_human(size)}"
    try:
        text = head.decode("utf-8", errors="replace")
    except Exception:
        return f"{kind} · {_human(size)}"

    if kind == "json":
        try:
            obj = json.loads(text)
        except json.JSONDecodeError:
            obj = None  # head is a truncated prefix of a bigger document
        if isinstance(obj, dict):
            for key in ("name", "title", "id", "cid", "module", "description"):
                val = obj.get(key)
                if isinstance(val, str) and val.strip():
                    return f"{key}: {val.strip()[:120]}"
            return "{" + ", ".join(list(obj)[:6]) + "}"
        if isinstance(obj, list):
            return f"[{len(obj)} items]"

    for raw in text.split("\n"):
        line = raw.strip()
        if not line or line in ('"""', "'''", "{", "[", "(", "*/"):
            continue  # nothing to read in a bare delimiter
        if kind == "markdown" and line.startswith("#"):
            return line.lstrip("# ").strip()[:120] or line[:120]
        if line.startswith(("#!", "//", "#", "/*", "*", "--")) and kind != "markdown":
            continue  # skip comment preamble, we want the first real line
        return line[:120]
    return text.strip()[:120] or f"{kind} · {_human(size)}"


def _human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024.0
    return f"{n}B"


SCHEMA = """
CREATE TABLE IF NOT EXISTS blocks (
  cid        TEXT PRIMARY KEY,
  shard      TEXT NOT NULL,
  size       INTEGER NOT NULL,
  mtime      REAL NOT NULL,
  meta_mtime REAL NOT NULL,
  pinned     INTEGER NOT NULL DEFAULT 0,
  ptype      TEXT,
  kind       TEXT NOT NULL,
  mime       TEXT,
  label      TEXT,
  preview    TEXT,
  name       TEXT,
  entries    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS blocks_kind    ON blocks(kind);
CREATE INDEX IF NOT EXISTS blocks_size    ON blocks(size);
CREATE INDEX IF NOT EXISTS blocks_mtime   ON blocks(mtime);
CREATE INDEX IF NOT EXISTS blocks_pinned  ON blocks(pinned);
CREATE INDEX IF NOT EXISTS blocks_entries ON blocks(entries);
CREATE TABLE IF NOT EXISTS refs (
  src TEXT NOT NULL,
  dst TEXT NOT NULL,
  PRIMARY KEY (src, dst)
);
CREATE INDEX IF NOT EXISTS refs_dst ON refs(dst);
CREATE TABLE IF NOT EXISTS names (
  src  TEXT NOT NULL,
  path TEXT NOT NULL,
  dst  TEXT NOT NULL,
  PRIMARY KEY (src, path)
);
CREATE INDEX IF NOT EXISTS names_dst ON names(dst);
CREATE TABLE IF NOT EXISTS state (k TEXT PRIMARY KEY, v TEXT);
"""

SORTS = {
    "mtime": "mtime",
    "size": "size",
    "kind": "kind",
    "cid": "cid",
    "label": "label",
    "name": "name",
    "entries": "entries",
}

# Kinds whose bytes we never try to render as text.
OPAQUE = ("binary", "archive", "image", "pdf", "wasm")


class Explorer:
    """Browsable view over a LocalFS block store."""

    def __init__(self, fs, db_path: Optional[str] = None):
        self.fs = fs
        self.blocks_path = Path(fs.blocks_path)
        self.db_path = Path(db_path or os.path.expanduser("~/.mod/localfs/index.sqlite"))
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._local = threading.local()
        self._progress: Dict[str, Any] = {"running": False}
        self._thread: Optional[threading.Thread] = None
        self._ensure_schema()

    # ── db plumbing ───────────────────────────────────────────────────

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.db_path, timeout=30)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            self._local.conn = conn
        return conn

    def _ensure_schema(self):
        conn = self._conn()
        with conn:
            conn.execute("CREATE TABLE IF NOT EXISTS state (k TEXT PRIMARY KEY, v TEXT)")
        stale = self._get_state("schema_version") != str(SCHEMA_VERSION)
        if stale:
            # The index is a rebuildable cache — on a shape change, start over
            # rather than migrate.
            with conn:
                for table in ("blocks", "refs", "names"):
                    conn.execute(f"DROP TABLE IF EXISTS {table}")
        with conn:
            conn.executescript(SCHEMA)
        if stale:
            self._set_state("schema_version", SCHEMA_VERSION)
            self._set_state("last_scan", "")

    def _get_state(self, key: str, default=None):
        row = self._conn().execute("SELECT v FROM state WHERE k=?", (key,)).fetchone()
        return row["v"] if row else default

    def _set_state(self, key: str, value):
        with self._conn() as c:
            c.execute("INSERT OR REPLACE INTO state (k, v) VALUES (?, ?)", (key, str(value)))

    # ── indexing ──────────────────────────────────────────────────────

    def refresh(self, full: bool = False) -> Dict[str, Any]:
        """Walk the store and bring the index up to date.

        Cheap by default: every block is stat'd (~1s for 40k blocks) but only
        blocks whose data or metadata changed are re-read and re-classified.
        """
        if not self._lock.acquire(blocking=False):
            return {"skipped": "refresh already running", **self.status()}
        started = time.time()
        try:
            conn = self._conn()
            known: Dict[str, Tuple[float, float]] = {}
            if not full:
                for row in conn.execute("SELECT cid, mtime, meta_mtime FROM blocks"):
                    known[row["cid"]] = (row["mtime"], row["meta_mtime"])
            else:
                with conn:
                    conn.execute("DELETE FROM blocks")
                    conn.execute("DELETE FROM refs")
                    conn.execute("DELETE FROM names")

            self._progress = {"running": True, "scanned": 0, "updated": 0,
                              "started_at": started, "full": full}
            seen, upserts, all_refs, all_names = set(), [], [], []
            scanned = updated = 0

            for shard in self._scandir(self.blocks_path):
                if not shard.is_dir():
                    continue
                metas = {}
                blocks = []
                for entry in self._scandir(shard.path):
                    if entry.name.endswith(".json"):
                        metas[entry.name[:-5]] = entry
                    else:
                        blocks.append(entry)
                for entry in blocks:
                    cid = entry.name
                    seen.add(cid)
                    scanned += 1
                    try:
                        bmtime = entry.stat().st_mtime
                    except OSError:
                        continue
                    meta_entry = metas.get(cid)
                    mmtime = 0.0
                    if meta_entry is not None:
                        try:
                            mmtime = meta_entry.stat().st_mtime
                        except OSError:
                            mmtime = 0.0
                    if known.get(cid) == (bmtime, mmtime):
                        continue
                    row, refs, names = self._read_block(cid, shard.name, entry, meta_entry,
                                                       bmtime, mmtime)
                    if row is None:
                        continue
                    upserts.append(row)
                    all_refs.append((cid, refs))
                    all_names.append((cid, names))
                    updated += 1
                    if len(upserts) >= 500:
                        self._flush(upserts, all_refs, all_names)
                        upserts, all_refs, all_names = [], [], []
                    self._progress.update(scanned=scanned, updated=updated)
                self._progress.update(scanned=scanned, updated=updated)

            self._flush(upserts, all_refs, all_names)

            removed = 0
            if not full:
                gone = [cid for cid in known if cid not in seen]
                if gone:
                    with conn:
                        for i in range(0, len(gone), 500):
                            chunk = gone[i:i + 500]
                            marks = ",".join("?" * len(chunk))
                            conn.execute(f"DELETE FROM blocks WHERE cid IN ({marks})", chunk)
                            conn.execute(f"DELETE FROM refs WHERE src IN ({marks})", chunk)
                            conn.execute(f"DELETE FROM names WHERE src IN ({marks})", chunk)
                    removed = len(gone)

            if updated or removed or full:
                self._assign_names()

            elapsed = round(time.time() - started, 2)
            self._set_state("last_scan", int(time.time()))
            self._set_state("last_scan_secs", elapsed)
            result = {"scanned": scanned, "updated": updated, "removed": removed,
                      "elapsed": elapsed, "full": full}
            self._progress = {"running": False, **result}
            return result
        finally:
            self._lock.release()

    def refresh_async(self, full: bool = False) -> Dict[str, Any]:
        """Kick off a refresh in the background; return immediately."""
        if self._thread and self._thread.is_alive():
            return {"started": False, **self.status()}
        self._thread = threading.Thread(target=self.refresh, kwargs={"full": full},
                                        daemon=True, name="localfs-index")
        self._thread.start()
        return {"started": True, "full": full}

    @staticmethod
    def _scandir(path) -> List[os.DirEntry]:
        try:
            return list(os.scandir(path))
        except OSError:
            return []

    def _read_block(self, cid, shard, entry, meta_entry, bmtime, mmtime):
        try:
            size = entry.stat().st_size
            with open(entry.path, "rb") as f:
                head = f.read(SNIFF_BYTES)
                tail = b""
                if size > SNIFF_BYTES:
                    budget = JSON_SCAN_BYTES if head.lstrip()[:1] in b"{[" else REF_SCAN_BYTES
                    tail = f.read(max(0, budget - SNIFF_BYTES))
        except OSError:
            return None, [], []

        meta = {}
        if meta_entry is not None:
            try:
                with open(meta_entry.path, "rb") as f:
                    meta = json.loads(f.read() or b"{}")
            except (OSError, json.JSONDecodeError):
                meta = {}

        kind, mime = sniff(head, size)
        label = label_for(kind, head, size)
        preview = ""
        refs: List[str] = []
        names: List[Tuple[str, str]] = []
        if kind not in OPAQUE:
            text = head.decode("utf-8", errors="replace")
            preview = text[:PREVIEW_CHARS]
            scan = text + tail.decode("utf-8", errors="replace")
            refs = sorted({c for c in CID_RE.findall(scan) if c != cid})
            if kind == "json":
                # Manifest blocks name their children; that's where the store's
                # filenames come from.
                names = [(path, dst) for path, dst in NAMED_CID_RE.findall(scan) if dst != cid]

        row = (cid, shard, int(meta.get("size", size)), bmtime, mmtime,
               1 if meta.get("pinned") else 0, meta.get("type"), kind, mime,
               label, preview, len(names))
        return row, refs, names

    def _flush(self, rows, refs, names):
        if not rows:
            return
        with self._conn() as c:
            c.executemany(
                "INSERT OR REPLACE INTO blocks "
                "(cid, shard, size, mtime, meta_mtime, pinned, ptype, kind, mime, label, "
                " preview, entries) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)
            for src, dsts in refs:
                c.execute("DELETE FROM refs WHERE src=?", (src,))
                if dsts:
                    c.executemany("INSERT OR REPLACE INTO refs (src, dst) VALUES (?, ?)",
                                  [(src, d) for d in dsts])
            for src, entries in names:
                c.execute("DELETE FROM names WHERE src=?", (src,))
                if entries:
                    c.executemany("INSERT OR REPLACE INTO names (src, path, dst) VALUES (?,?,?)",
                                  [(src, p, d) for p, d in entries])

    def _assign_names(self):
        """Give every block the basename it is known by inside manifests.

        A block can appear under many paths; we take one, deterministically,
        so the listing reads like a file browser instead of a wall of CIDs.
        """
        with self._conn() as c:
            c.execute("""
                UPDATE blocks SET name = (
                  SELECT path FROM names WHERE names.dst = blocks.cid
                  ORDER BY LENGTH(path), path LIMIT 1
                )
                WHERE cid IN (SELECT dst FROM names)
            """)

    def status(self) -> Dict[str, Any]:
        conn = self._conn()
        row = conn.execute("SELECT COUNT(*) n, COALESCE(SUM(size),0) s, "
                           "COALESCE(SUM(pinned),0) p FROM blocks").fetchone()
        last = self._get_state("last_scan")
        return {
            "indexed": row["n"],
            "size": row["s"],
            "pinned": row["p"],
            "last_scan": int(last) if last else None,
            "last_scan_secs": float(self._get_state("last_scan_secs") or 0),
            "storage_path": str(self.fs.storage_path),
            "db": str(self.db_path),
            "progress": self._progress,
        }

    # ── queries ───────────────────────────────────────────────────────

    def ls(self, q: str = None, kind: str = None, pinned: bool = None,
           sort: str = "mtime", order: str = "desc",
           limit: int = 50, offset: int = 0,
           min_size: int = None, max_size: int = None) -> Dict[str, Any]:
        """Paginated block listing — the explorer's main view."""
        where, args = [], []
        if q:
            term = q.strip()
            if CID_RE.fullmatch(term):
                where.append("cid = ?")
                args.append(term)
            else:
                where.append("(name LIKE ? OR label LIKE ? OR preview LIKE ? OR cid LIKE ?)")
                args += [f"%{term}%", f"%{term}%", f"%{term}%", f"{term}%"]
        if kind:
            kinds = [k.strip() for k in kind.split(",") if k.strip()]
            where.append(f"kind IN ({','.join('?' * len(kinds))})")
            args += kinds
        if pinned is not None:
            where.append("pinned = ?")
            args.append(1 if pinned else 0)
        if min_size is not None:
            where.append("size >= ?")
            args.append(int(min_size))
        if max_size is not None:
            where.append("size <= ?")
            args.append(int(max_size))

        clause = ("WHERE " + " AND ".join(where)) if where else ""
        col = SORTS.get(sort, "mtime")
        direction = "ASC" if str(order).lower() == "asc" else "DESC"
        limit = max(1, min(int(limit), 500))

        conn = self._conn()
        total = conn.execute(f"SELECT COUNT(*) n FROM blocks {clause}", args).fetchone()["n"]
        rows = conn.execute(
            f"SELECT cid, size, mtime, pinned, ptype, kind, mime, label, preview, name, entries "
            f"FROM blocks {clause} ORDER BY {col} {direction}, cid ASC LIMIT ? OFFSET ?",
            args + [limit, max(0, int(offset))]).fetchall()
        return {
            "total": total,
            "limit": limit,
            "offset": int(offset),
            "blocks": [dict(r) for r in rows],
        }

    def kinds(self) -> List[Dict[str, Any]]:
        """Facet counts, for the filter rail."""
        rows = self._conn().execute(
            "SELECT kind, COUNT(*) n, COALESCE(SUM(size),0) size FROM blocks "
            "GROUP BY kind ORDER BY n DESC").fetchall()
        return [dict(r) for r in rows]

    def roots(self, limit: int = 100, offset: int = 0, q: str = None) -> Dict[str, Any]:
        """Manifest blocks — snapshot roots you can browse as directory trees."""
        where, args = ["entries > 0"], []
        if q:
            where.append("(label LIKE ? OR preview LIKE ? OR cid LIKE ?)")
            args += [f"%{q}%", f"%{q}%", f"{q}%"]
        clause = "WHERE " + " AND ".join(where)
        conn = self._conn()
        total = conn.execute(f"SELECT COUNT(*) n FROM blocks {clause}", args).fetchone()["n"]
        rows = conn.execute(
            f"SELECT cid, size, mtime, pinned, kind, label, entries FROM blocks {clause} "
            f"ORDER BY entries DESC, mtime DESC LIMIT ? OFFSET ?",
            args + [max(1, min(int(limit), 500)), max(0, int(offset))]).fetchall()
        return {"total": total, "roots": [dict(r) for r in rows]}

    def tree(self, cid: str, path: str = "") -> Dict[str, Any]:
        """One directory level inside a manifest: subdirs and files at `path`."""
        prefix = path.strip("/")
        prefix = prefix + "/" if prefix else ""
        rows = self._conn().execute(
            "SELECT path, dst FROM names WHERE src=? AND path LIKE ? ORDER BY path",
            (cid, f"{prefix}%")).fetchall()
        if not rows:
            raise FileNotFoundError(f"No manifest entries under {cid}/{path}")

        dirs: Dict[str, Dict[str, Any]] = {}
        files: List[Dict[str, Any]] = []
        for row in rows:
            rest = row["path"][len(prefix):]
            if not rest:
                continue
            head, sep, _ = rest.partition("/")
            if sep:
                d = dirs.setdefault(head, {"name": head, "type": "dir", "entries": 0, "size": 0})
                d["entries"] += 1
            else:
                files.append({"name": head, "type": "file", "cid": row["dst"],
                              "path": row["path"]})

        # Fill in what we know about the files from the block index.
        if files:
            cids = list({f["cid"] for f in files})
            known = {}
            for i in range(0, len(cids), 500):
                chunk = cids[i:i + 500]
                marks = ",".join("?" * len(chunk))
                for r in self._conn().execute(
                        f"SELECT cid, size, kind, pinned FROM blocks WHERE cid IN ({marks})",
                        chunk):
                    known[r["cid"]] = dict(r)
            for f in files:
                info = known.get(f["cid"])
                f["local"] = info is not None
                f["size"] = info["size"] if info else None
                f["kind"] = info["kind"] if info else None
                f["pinned"] = bool(info["pinned"]) if info else False
            for d in dirs.values():
                d["size"] = sum(f["size"] or 0 for f in files
                                if f["path"].startswith(prefix + d["name"] + "/"))

        # Directory sizes need the whole subtree, not just this level's files.
        if dirs:
            sizes = {name: 0 for name in dirs}
            all_files = self._conn().execute(
                "SELECT n.path, b.size FROM names n LEFT JOIN blocks b ON b.cid = n.dst "
                "WHERE n.src=? AND n.path LIKE ?", (cid, f"{prefix}%")).fetchall()
            for r in all_files:
                rest = r["path"][len(prefix):]
                head, sep, _ = rest.partition("/")
                if sep and head in sizes:
                    sizes[head] += r["size"] or 0
            for name, total in sizes.items():
                dirs[name]["size"] = total

        return {
            "cid": cid,
            "path": prefix.rstrip("/"),
            "dirs": sorted(dirs.values(), key=lambda d: d["name"].lower()),
            "files": sorted(files, key=lambda f: f["name"].lower()),
        }

    def paths(self, cid: str, limit: int = 50) -> List[Dict[str, str]]:
        """Every manifest path this block is known by."""
        rows = self._conn().execute(
            "SELECT src, path FROM names WHERE dst=? ORDER BY LENGTH(path), path LIMIT ?",
            (cid, limit)).fetchall()
        return [dict(r) for r in rows]

    def block(self, cid: str, content_chars: int = 200_000) -> Dict[str, Any]:
        """Full detail for one block: index row + content + link graph."""
        conn = self._conn()
        row = conn.execute("SELECT * FROM blocks WHERE cid=?", (cid,)).fetchone()
        info = dict(row) if row else {}

        path = self.fs._block_path(cid)
        exists = path.exists()
        if not exists and not info:
            raise FileNotFoundError(f"Block not found: {cid}")
        if exists and not info:
            # Present on disk but not yet indexed — classify it on the spot.
            entry_stat = path.stat()
            with open(path, "rb") as f:
                head = f.read(SNIFF_BYTES)
            kind, mime = sniff(head, entry_stat.st_size)
            info = {"cid": cid, "size": entry_stat.st_size, "mtime": entry_stat.st_mtime,
                    "pinned": int(self.fs.pinned(cid)), "kind": kind, "mime": mime,
                    "label": label_for(kind, head, entry_stat.st_size)}

        info["exists"] = exists
        info["cidv1"] = self.fs.to_cidv1(cid) if cid.startswith("Qm") else cid
        info["pinned"] = bool(info.get("pinned"))
        info["truncated"] = False
        info["content"] = None
        info["paths"] = self.paths(cid)

        if exists and info.get("kind") not in OPAQUE:
            with open(path, "rb") as f:
                raw = f.read(content_chars + 1)
            text = raw.decode("utf-8", errors="replace")
            if len(text) > content_chars:
                text = text[:content_chars]
                info["truncated"] = True
            info["content"] = text

        info["refs"] = [r["dst"] for r in conn.execute(
            "SELECT dst FROM refs WHERE src=? ORDER BY dst", (cid,))]
        info["referrers"] = [r["src"] for r in conn.execute(
            "SELECT src FROM refs WHERE dst=? ORDER BY src LIMIT 200", (cid,))]
        # Which of the referenced CIDs we actually hold locally.
        if info["refs"]:
            marks = ",".join("?" * len(info["refs"]))
            have = {r["cid"] for r in conn.execute(
                f"SELECT cid FROM blocks WHERE cid IN ({marks})", info["refs"])}
            info["refs"] = [{"cid": c, "local": c in have} for c in info["refs"]]
        return info

    def raw(self, cid: str) -> Tuple[bytes, str]:
        """Byte-exact block content plus a best-guess content type."""
        data = self.fs.get_file(cid)
        row = self._conn().execute("SELECT kind, mime FROM blocks WHERE cid=?", (cid,)).fetchone()
        if row and row["mime"]:
            return data, row["mime"]
        kind, mime = sniff(data[:SNIFF_BYTES], len(data))
        return data, MIME_BY_KIND.get(kind, mime)

    def set_pinned(self, cid: str, pinned: bool):
        """Keep the index honest after a pin toggle without a full rescan."""
        meta_mtime = 0.0
        meta_path = self.fs._meta_path(cid)
        if meta_path.exists():
            meta_mtime = meta_path.stat().st_mtime
        with self._conn() as c:
            c.execute("UPDATE blocks SET pinned=?, meta_mtime=? WHERE cid=?",
                      (1 if pinned else 0, meta_mtime, cid))

    def forget(self, cid: str):
        """Drop a removed block from the index."""
        with self._conn() as c:
            c.execute("DELETE FROM blocks WHERE cid=?", (cid,))
            c.execute("DELETE FROM refs WHERE src=? OR dst=?", (cid, cid))
            c.execute("DELETE FROM names WHERE src=?", (cid,))

    def grep(self, q: str, limit: int = 50, max_bytes: int = 1_000_000) -> Dict[str, Any]:
        """Deep search: scan block contents, not just the stored preview.

        Slower than `ls(q=…)` — it opens every text block — but it finds
        matches past the first few hundred bytes.
        """
        needle = q.encode("utf-8", errors="ignore").lower()
        if not needle:
            return {"query": q, "matches": [], "scanned": 0}
        conn = self._conn()
        matches, scanned = [], 0
        marks = ",".join("?" * len(OPAQUE))
        rows = conn.execute(
            f"SELECT cid, kind, size, label, name FROM blocks "
            f"WHERE kind NOT IN ({marks}) AND size <= ? "
            f"ORDER BY mtime DESC", (*OPAQUE, max_bytes))
        for row in rows:
            scanned += 1
            try:
                with open(self.fs._block_path(row["cid"]), "rb") as f:
                    data = f.read(max_bytes)
            except OSError:
                continue
            pos = data.lower().find(needle)
            if pos < 0:
                continue
            start = max(0, pos - 60)
            snippet = data[start:pos + len(needle) + 120].decode("utf-8", errors="replace")
            line = data[:pos].count(b"\n") + 1
            matches.append({**dict(row), "line": line, "snippet": snippet})
            if len(matches) >= limit:
                break
        return {"query": q, "matches": matches, "scanned": scanned,
                "truncated": len(matches) >= limit}
