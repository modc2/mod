"""
recipes - the local catalog grow draws from when no model is involved

A recipe is one (language, operation) pair, and it yields a matched set: a
custom shell tool and an agent built to wield it. `py × todos` is the tool
`py-todos` (grep TODO/FIXME across Python files) and the agent `py-todo-hunter`
(runs that tool, reads what it found, reports what to fix first).

Every command here is written by hand and read-only: find / grep / wc / awk /
git log over the tree it is pointed at. Nothing is generated, so the local
engine is safe by construction and costs nothing — no model, no key, no
network. That is what lets the grower run every minute on a box with no
provider at all.

The catalog is finite on purpose (~180 pairs). When it is used up the `auto`
engine moves on to model drafting; see grow/mod.py.

Usage:
    for r in catalog(): r['tool']['name'], r['agent']['name']
"""
from typing import Any, Dict, List

# directories nobody wants scanned — vendored, built or VCS state
SKIP_DIRS = ("node_modules", ".git", ".next", "target", "__pycache__", "dist",
             "build", ".venv", "venv")
PRUNE = " ".join(f"-not -path '*/{d}/*'" for d in SKIP_DIRS)
GREP_EXCL = " ".join(f"--exclude-dir={d}" for d in SKIP_DIRS)

# key -> (label, extensions, definitions regex, imports regex)
# Order is priority: the catalog is walked front to back.
LANGS: Dict[str, tuple] = {
    "py":   ("Python", ["py", "pyi"],
             r"^\s*(async\s+)?(def|class)\s+\w+", r"^\s*(import|from)\s+\S+"),
    "ts":   ("TypeScript", ["ts", "tsx"],
             r"^\s*(export\s+)?(default\s+)?(async\s+)?(function|class|interface|type|enum)\s+\w+",
             r"^\s*import\s|require\("),
    "rs":   ("Rust", ["rs"],
             r"^\s*(pub(\(\w+\))?\s+)?(async\s+)?(fn|struct|enum|trait|impl|mod)\b",
             r"^\s*(pub\s+)?use\s"),
    "js":   ("JavaScript", ["js", "jsx", "mjs", "cjs"],
             r"^\s*(export\s+)?(default\s+)?(async\s+)?(function|class)\s+\w+",
             r"^\s*import\s|require\("),
    "sh":   ("shell", ["sh", "bash"],
             r"^\s*(function\s+)?[A-Za-z_][A-Za-z0-9_]*\s*\(\)", r"^\s*(source|\.)\s"),
    "md":   ("Markdown", ["md", "mdx"], r"^#{1,6}\s", None),
    "go":   ("Go", ["go"], r"^(func|type)\s", r"^import\s|^\s+\"[a-z]"),
    "sol":  ("Solidity", ["sol"],
             r"^\s*(contract|interface|library|function|event|modifier|struct)\s",
             r"^\s*import\s"),
    "json": ("JSON", ["json"], None, None),
    "yaml": ("YAML", ["yml", "yaml"], None, None),
    "toml": ("TOML", ["toml"], r"^\s*\[[^]]+\]", None),
    "css":  ("CSS", ["css", "scss"], None, r"@import"),
    "html": ("HTML", ["html", "htm"], None, r"<script[^>]+src=|<link[^>]+href="),
    "sql":  ("SQL", ["sql"], r"^\s*(create|CREATE)\s+(table|view|index|function|TABLE|VIEW|INDEX|FUNCTION)", None),
    "rb":   ("Ruby", ["rb"], r"^\s*(def|class|module)\s", r"^\s*require"),
    "java": ("Java", ["java"], r"^\s*(public\s+|private\s+|protected\s+)?(abstract\s+)?(class|interface|enum|record)\s+\w+",
             r"^import\s"),
    "c":    ("C", ["c", "h"], None, r"^\s*#include"),
    "cpp":  ("C++", ["cc", "cpp", "hpp", "cxx"], None, r"^\s*#include"),
    "any":  ("any", [], None, None),
}

PATH = {"type": "string", "required": False, "default": ".",
        "hint": "directory to scan (default: the current one)"}


def _find(exts: List[str]) -> str:
    names = " -o ".join(f"-name '*.{e}'" for e in exts)
    sel = f"\\( {names} \\) " if exts else ""
    return f"find {{path}} -type f {sel}{PRUNE}"


def _include(exts: List[str]) -> str:
    return " ".join(f"--include='*.{e}'" for e in exts)


def _globs(exts: List[str]) -> str:
    return (" -- " + " ".join(f"'*.{e}'" for e in exts)) if exts else ""


def _grep(regex: str, exts: List[str], flags: str = "-rnE") -> str:
    return (f"grep {flags} '{regex}' {{path}} {_include(exts)} {GREP_EXCL}"
            .replace("  ", " "))


# op -> builder(key, label, exts, defs, imports) -> recipe parts, or None when
# the op makes no sense for that language. Each part is:
#   tool:  suffix, description, command, extra params
#   agent: suffix, icon, the job in one line, how to report
OPS = {
    "todos": lambda k, label, ex, d, i: {
        "tool": ("todos", f"List TODO / FIXME / XXX / HACK markers in {label} files, with file:line",
                 _grep("TODO|FIXME|XXX|HACK", ex) + " | head -n 200", {}),
        "agent": ("todo-hunter", "☐", f"find the open TODO and FIXME markers in {label} code and rank which to fix first",
                  "the five markers that matter most, each with file:line and the change it asks for")},
    "largest": lambda k, label, ex, d, i: {
        "tool": ("largest", f"The largest {label} files by line count",
                 _find(ex) + " -print0 | xargs -0 -r wc -l | grep -v ' total$' | sort -rn | head -n {top}",
                 {"top": {"type": "integer", "required": False, "default": 15,
                          "hint": "how many files to list"}}),
        "agent": ("slimmer", "▤", f"find the biggest {label} files and propose how to split them",
                  "the three files most worth splitting, with a concrete split for each (what moves where)")},
    "loc": lambda k, label, ex, d, i: {
        "tool": ("loc", f"Count {label} files and their total lines",
                 _find(ex) + " | wc -l | sed 's/^/files: /'; "
                 + _find(ex) + " -print0 | xargs -0 -r cat | wc -l | sed 's/^/lines: /'", {}),
        "agent": ("sizer", "#", f"measure how much {label} code a project has and where it lives",
                  "file and line totals, then the three directories holding most of it")},
    "recent": lambda k, label, ex, d, i: {
        "tool": ("recent", f"{label} files modified in the last N days, newest first",
                 _find(ex) + " -mtime -{days} -printf '%TY-%Tm-%Td %TH:%TM  %p\\n' | sort -r | head -n 60",
                 {"days": {"type": "integer", "required": False, "default": 7,
                           "hint": "look back this many days"}}),
        "agent": ("changelog", "◷", f"summarize what changed recently in the {label} files",
                  "a short changelog grouped by area, newest first, naming the files behind each line")},
    "longlines": lambda k, label, ex, d, i: {
        "tool": ("longlines", f"{label} lines longer than a width, with file:line and length",
                 _find(ex) + " -exec awk -v w={width} 'length > w {print FILENAME\":\"FNR\": \"length}' {} + | head -n 120",
                 {"width": {"type": "integer", "required": False, "default": 120,
                            "hint": "flag lines longer than this"}}),
        "agent": ("line-tamer", "↔", f"find the over-long lines in {label} files and say which ones hurt readability",
                  "the worst offenders with file:line and a rewrapped version of each")},
    "churn": lambda k, label, ex, d, i: {
        "tool": ("churn", f"{label} files changed most often in git over the last N days",
                 "git -C {path} log --since={days}.days.ago --name-only --pretty=format:"
                 + _globs(ex) + " | grep -v '^$' | sort | uniq -c | sort -rn | head -n 25",
                 {"days": {"type": "integer", "required": False, "default": 30,
                           "hint": "look back this many days"}}),
        "agent": ("hotspot", "▲", f"find the {label} files that change the most and say why that is risky",
                  "the top hotspots, what keeps changing in each, and one refactor that would calm it")},
    "search": lambda k, label, ex, d, i: {
        "tool": ("search", f"Search {label} files for a regular expression, with file:line",
                 f"grep -rnE {{pattern}} {{path}} {_include(ex)} {GREP_EXCL} | head -n 200".replace("  ", " "),
                 {"pattern": {"type": "string", "required": True,
                              "hint": "extended regular expression to look for"}}),
        "agent": ("finder", "⌕", f"answer where-is questions about {label} code by searching it",
                  "every place that answers the question, file:line, with one sentence on each")},
    "dupes": lambda k, label, ex, d, i: {
        "tool": ("dupes", f"{label} files with byte-identical content (grouped by md5)",
                 _find(ex) + " -size +0 -exec md5sum {} + | sort | uniq -w32 -dD | head -n 100", {}),
        "agent": ("dedup", "⧉", f"find duplicated {label} files and say which copy should win",
                  "each duplicate group, the copy to keep, and what imports the others")},
    "defs": lambda k, label, ex, d, i: d and {
        "tool": ("defs", f"List {label} definitions (functions, classes, types) with file:line",
                 _grep(d, ex) + " | head -n 300", {}),
        "agent": ("mapper", "◫", f"map the structure of {label} code from its definitions",
                  "a module-by-module outline of what is defined where, and the three entry points to read first")},
    "imports": lambda k, label, ex, d, i: i and {
        "tool": ("imports", f"List {label} import / include lines with file:line",
                 _grep(i, ex) + " | head -n 300", {}),
        "agent": ("dep-tracer", "⇄", f"trace what {label} code depends on from its imports",
                  "external dependencies, the most-imported internal modules, and any import that looks unused or circular")},
}

AGENT_TOOLS = ["read", "grep", "glob", "think", "finish"]


def _agent_goal(title: str, tool: str, tool_desc: str, job: str, report: str) -> str:
    return (f"You are {title}. Your job: {job}.\n\n"
            f"1. Call the `{tool}` tool first — {tool_desc[0].lower() + tool_desc[1:]}. "
            f"Point it at the path the user names (default: the current directory).\n"
            f"2. Read the files it surfaces that matter most, to confirm what it found.\n"
            f"3. Finish with a short report: {report}.\n\n"
            f"You are read-only. Never edit, write, move or delete files.")


def catalog() -> List[Dict[str, Any]]:
    """Every recipe, priority order. Pure — the same list every call."""
    out = []
    for k, (label, exts, defs, imports) in LANGS.items():
        for op, build in OPS.items():
            if k == "any" and op in ("defs", "imports"):
                continue
            parts = build(k, label if k != "any" else "all", exts, defs, imports)
            if not parts:
                continue
            t_suffix, t_desc, cmd, extra = parts["tool"]
            a_suffix, icon, job, report = parts["agent"]
            tool = f"{k}-{t_suffix}"
            agent = f"{k}-{a_suffix}"
            title = agent.replace("-", " ").title()
            out.append({
                "id": f"{k}:{op}",
                "tool": {"name": tool, "description": t_desc, "command": cmd,
                         "params": {"path": dict(PATH), **extra}},
                "agent": {"name": agent, "icon": icon,
                          "description": f"{job[0].upper() + job[1:]} — uses {tool}",
                          "goal": _agent_goal(title, tool, t_desc, job, report),
                          "tools": [tool, *AGENT_TOOLS]},
            })
    return out
