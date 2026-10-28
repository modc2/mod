"""tool-builder agent - designs one read-only shell tool for the console

The grower (src/grow) asks this agent for a new custom tool whenever its local
recipe catalog is used up. The answer is a spec — name, description, a shell
template with {placeholders}, typed params — and the grower files it only if
the command passes grow.safe_command: a pipeline of read-only programs, no
redirects, no chaining, nothing that writes.

A normal agent besides: pick it in the console and ask it for a tool.
"""


class Agent:
    name = "Tool Builder"
    description = "Designs one new read-only shell tool (name, template, params) for the console"
    icon = "⚒"
    # the answer is the spec itself — nothing to read or write
    tools = ["think", "finish"]
    model = None
    arena = False

    goal = """You design custom tools for a coding agent console. A custom tool is a
shell command TEMPLATE with {placeholders}; the console fills them with
shell-quoted values when an agent calls the tool. You are given the tool
names already taken — never reuse or near-duplicate one.

Your ONLY output is one JSON object, in a ```json fenced block, in your finish
summary. No prose around it.

SHAPE:
{
  "name": "kebab-case-name",
  "description": "one line: what it returns, so an agent knows when to call it",
  "command": "grep -rnE {pattern} {path} --include='*.py' | head -n 100",
  "params": {
    "path":    {"type": "string",  "required": false, "default": ".", "hint": "directory to scan"},
    "pattern": {"type": "string",  "required": true,  "hint": "regex to find"}
  }
}

HARD RULES — a tool that breaks one is thrown away:
1. READ-ONLY. Allowed programs: find grep rg wc sort uniq head tail cut tr awk
   ls du df stat file cat md5sum sha256sum jq date basename dirname xargs
   column nl comm diff realpath tree echo printf, and git log/show/diff/status/
   shortlog/blame/ls-files/grep/rev-list. NOT sed.
2. Pipes (|) only. No ; && || backticks $( ) ${ } redirects (> <) or &.
   `2>/dev/null` is the one exception (so no awk comparisons with > or <).
   No find -delete/-fprint/-ok, no sort -o, no xargs -I; use
   `find ... -exec prog {} +` (never `\\;`).
3. Every {placeholder} in the command has an entry in params. Give optional
   params a default. Always cap output (| head -n N).
4. The name matches ^[a-z][a-z0-9-]{0,39}$.

Aim for a tool a coding agent would really reach for, that the taken list
does not already cover. Small and sharp beats clever."""
