"""Turn a directory of code into a training corpus (JSONL).

Approach: domain-adaptation / continued-pretraining. Each source file becomes one
record `{"path", "text", "lang", "chars"}` where `text` is a short path header +
the file body. train.py concatenates these and chops them into fixed-length token
blocks for causal-LM LoRA. This teaches the model the codebase's APIs, naming, and
idioms without needing hand-written instruction pairs.

Usable standalone:
    python3 -m trainer.dataset --src /path/to/code --out <dataset.jsonl>
    python3 -m trainer.dataset --src /path/to/code --stats   # just report, no write
"""
import argparse
import json
import os

# Extensions we treat as code/text worth training on → a coarse language tag.
CODE_EXT = {
    ".py": "python", ".rs": "rust", ".ts": "typescript", ".tsx": "tsx",
    ".js": "javascript", ".jsx": "jsx", ".go": "go", ".java": "java",
    ".c": "c", ".h": "c", ".cpp": "cpp", ".hpp": "cpp", ".cc": "cpp",
    ".rb": "ruby", ".php": "php", ".cs": "csharp", ".swift": "swift",
    ".kt": "kotlin", ".scala": "scala", ".sh": "bash", ".sql": "sql",
    ".md": "markdown", ".toml": "toml", ".yaml": "yaml", ".yml": "yaml",
    ".json": "json", ".css": "css", ".html": "html", ".lua": "lua",
    ".sol": "solidity", ".vue": "vue", ".ml": "ocaml", ".ex": "elixir",
}

# Directories we never descend into — build output, deps, vcs, caches.
SKIP_DIRS = {
    "node_modules", ".git", "target", ".next", "dist", "build", "__pycache__",
    ".venv", "venv", ".mypy_cache", ".pytest_cache", "vendor", ".cargo",
    "site-packages", ".idea", ".vscode", "coverage", ".turbo", "out",
}

MAX_FILE_BYTES = 200_000  # skip anything bigger — likely generated/minified


def is_probably_text(sample: bytes) -> bool:
    if b"\x00" in sample:
        return False
    # Heuristic: mostly-printable bytes.
    if not sample:
        return True
    printable = sum(1 for b in sample if b == 9 or b == 10 or b == 13 or 32 <= b < 127)
    return printable / len(sample) > 0.85


def build(src: str, out: str | None = None):
    src = os.path.abspath(os.path.expanduser(src))
    if not os.path.isdir(src):
        raise SystemExit(json.dumps({"error": f"not a directory: {src}"}))

    records = []
    by_lang: dict[str, int] = {}
    total_chars = 0
    skipped = 0
    f_out = open(out, "w") if out else None

    for root, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        for name in files:
            ext = os.path.splitext(name)[1].lower()
            lang = CODE_EXT.get(ext)
            if not lang:
                continue
            fp = os.path.join(root, name)
            try:
                if os.path.getsize(fp) > MAX_FILE_BYTES:
                    skipped += 1
                    continue
                with open(fp, "rb") as fh:
                    raw = fh.read()
                if not is_probably_text(raw[:4096]):
                    skipped += 1
                    continue
                body = raw.decode("utf-8", errors="replace").strip()
            except OSError:
                skipped += 1
                continue
            if not body:
                continue
            rel = os.path.relpath(fp, src)
            text = f"// file: {rel}\n{body}\n"
            rec = {"path": rel, "lang": lang, "chars": len(body), "text": text}
            total_chars += len(body)
            by_lang[lang] = by_lang.get(lang, 0) + 1
            records.append(rec)
            if f_out:
                f_out.write(json.dumps(rec) + "\n")

    if f_out:
        f_out.close()

    stats = {
        "src": src,
        "files": sum(by_lang.values()),
        "skipped": skipped,
        "total_chars": total_chars,
        "approx_tokens": total_chars // 4,  # ~4 chars/token rule of thumb
        "by_lang": dict(sorted(by_lang.items(), key=lambda kv: -kv[1])),
        "out": out,
    }
    return stats, records


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--out", default=None)
    ap.add_argument("--stats", action="store_true", help="report only, don't write")
    args = ap.parse_args()
    out = None if args.stats else args.out
    stats, _ = build(args.src, out)
    print(json.dumps(stats))


if __name__ == "__main__":
    main()
