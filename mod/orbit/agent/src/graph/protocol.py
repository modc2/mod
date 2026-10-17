"""
graph/protocol - the vocabulary a graph of agents is written in.

This is the whole protocol in one file: the node kinds, the ports each one
answers on, the shape of the message that travels an edge, and the fixed set
of predicates a gate may test. Nothing here runs anything — runner.py does
that, the console reads KINDS over GET /graph/kinds rather than hardcoding a
palette, and docs/graph.md is the prose version of this file.

The one rule that shapes everything else: **a node is an agent, not a part of
one.** An agent is built in the registry (src/agents/) — prompt, model,
toolbox, memory — and arrives here already whole. A graph says what talks to
what, in which order, and under what condition; it never says what an agent is
made of. So there is no `prompt` node, no `model` node and no `toolbox` node,
and wiring is composition between agents rather than assembly of one.

A message is what crosses an edge:

    {"text": str,      # what the sending node produced — the payload
     "data": {},       # structured extras a node chose to attach
     "from": node_id,  # who sent it
     "port": str,      # which of that node's outputs it left by
     "ok": bool,       # did the sender succeed
     "agent": str,     # the agent that produced it, when a node ran one
     "hops": int}      # edges crossed so far — the loop guard reads this
"""
from typing import Any, Dict, List

# ── node kinds ───────────────────────────────────────────────────────
#
# `out` is the list of ports a node can emit on; a node with no static list
# (router) names its own ports in its data. `fan` says what arriving messages
# do: 'each' fires the node once per message, 'gather' collects them until the
# node is ready (that is what makes join a join).

KINDS: Dict[str, Dict[str, Any]] = {
    "input": {
        "label": "Input",
        "icon": "▷",
        "accent": "sky",
        "inputs": 0,
        "out": ["out"],
        "fan": "each",
        "summary": "where the run comes in",
        "doc": "The graph's entry. It carries the caller's query, unchanged, "
               "to everything wired to it. A graph with several inputs starts "
               "several branches at once.",
        "fields": [
            {"key": "label", "type": "text", "label": "label",
             "hint": "what this entry is for — shown on the node"},
        ],
    },
    "agent": {
        "label": "Agent",
        "icon": "◆",
        "accent": "emerald",
        "inputs": "*",
        "out": ["out", "err"],
        "fan": "each",
        "summary": "run one agent from the registry",
        "doc": "Runs an agent on the incoming message and emits what it "
               "produced. The agent is picked whole from the registry — this "
               "node does not define one. `prompt` is prepended to the "
               "message as the instruction for this position in the graph; "
               "model, steps and toolbox override that agent's own settings "
               "for this node only. A failed run leaves by `err`, so a graph "
               "can route around a broken step instead of dying on it.",
        "fields": [
            {"key": "agent", "type": "agent", "label": "agent", "required": True},
            {"key": "prompt", "type": "textarea", "label": "instruction",
             "hint": "what this agent does at this point in the graph"},
            {"key": "model", "type": "model", "label": "model",
             "hint": "empty = the agent's own model"},
            {"key": "steps", "type": "number", "label": "steps", "min": 1, "max": 60,
             "hint": "step budget for this node"},
            {"key": "toolbox", "type": "toolbox", "label": "toolbox",
             "hint": "empty = the agent's own tools"},
            {"key": "free", "type": "bool", "label": "free models only"},
        ],
    },
    "gate": {
        "label": "Gate",
        "icon": "⊘",
        "accent": "amber",
        "inputs": "*",
        "out": ["pass", "fail"],
        "fan": "each",
        "summary": "a condition the message must meet to go on",
        "doc": "Tests the message against a fixed set of predicates — no code "
               "is evaluated — and sends it out of `pass` or `fail`. Leave "
               "`fail` unwired and a blocked message simply stops there, which "
               "is the usual way to end a branch that did not earn the next "
               "step.",
        "fields": [
            {"key": "mode", "type": "select", "label": "match",
             "options": ["all", "any"], "default": "all"},
            {"key": "rules", "type": "rules", "label": "rules"},
        ],
    },
    "judge": {
        "label": "Judge",
        "icon": "⚖",
        "accent": "amber",
        "inputs": "*",
        "out": ["pass", "fail"],
        "fan": "each",
        "summary": "an agent decides whether this passes",
        "doc": "The gate for things a predicate cannot check. An agent is "
               "asked one yes/no question about the message and its answer "
               "picks the port. Use it for judgement ('is this review "
               "actionable?'); use a Gate for anything a string test can "
               "settle, because a Gate costs nothing and cannot be talked "
               "round.",
        "fields": [
            {"key": "agent", "type": "agent", "label": "judge", "required": True},
            {"key": "question", "type": "textarea", "label": "question",
             "hint": "answered YES or NO about the incoming message"},
            {"key": "model", "type": "model", "label": "model"},
            {"key": "free", "type": "bool", "label": "free models only", "default": True},
        ],
    },
    "router": {
        "label": "Router",
        "icon": "⋔",
        "accent": "violet",
        "inputs": "*",
        "out": None,  # named by data.routes, plus the implicit 'else'
        "fan": "each",
        "summary": "send it down one of several branches",
        "doc": "A gate with more than two answers. Each route is a name and "
               "its rules; the first route that matches takes the message, and "
               "anything that matches nothing leaves by `else`.",
        "fields": [
            {"key": "routes", "type": "routes", "label": "routes"},
        ],
    },
    "join": {
        "label": "Join",
        "icon": "⊕",
        "accent": "sky",
        "inputs": "*",
        "out": ["out"],
        "fan": "gather",
        "summary": "wait for the branches and merge them",
        "doc": "The other half of a fan-out. It holds the messages arriving "
               "from each branch and emits one: `concat` stitches them in "
               "arrival order under a header per source, `first` takes the one "
               "that arrived first, `longest` the longest, `vote` the answer "
               "the most branches agreed on. `wait: all` waits for every "
               "inbound edge that can still deliver — a branch a gate blocked "
               "cannot, so a join never hangs on one — and `any` fires on the "
               "first message through.",
        "fields": [
            {"key": "mode", "type": "select", "label": "merge",
             "options": ["concat", "first", "longest", "vote"], "default": "concat"},
            {"key": "wait", "type": "select", "label": "wait for",
             "options": ["all", "any"], "default": "all"},
        ],
    },
    "tool": {
        "label": "Tool",
        "icon": "◇",
        "accent": "emerald",
        "inputs": "*",
        "out": ["out", "err"],
        "fan": "each",
        "summary": "one tool call, no model in the loop",
        "doc": "Calls a single tool directly — a built-in, a custom shell "
               "tool, or a fleet module as `mod.<name>` — with no LLM step. "
               "The glue between agents: fetch a page, read a file, call "
               "another module, and hand the result on. `{{text}}` in any "
               "parameter is replaced with the incoming message.",
        "fields": [
            {"key": "tool", "type": "tool", "label": "tool", "required": True},
            {"key": "params", "type": "json", "label": "params",
             "hint": "{{text}} = the incoming message"},
        ],
    },
    "loop": {
        "label": "Loop",
        "icon": "↻",
        "accent": "violet",
        "inputs": "*",
        "out": ["out", "done"],
        "fan": "each",
        "summary": "send it round again, up to a limit",
        "doc": "The only node a cycle may pass through, and the reason a "
               "cycle is allowed at all. Every message through it is counted; "
               "while the count is under `max` and the `until` rules do not "
               "match, it leaves by `out` (round again), and otherwise by "
               "`done`. Wire `out` back to the node that should repeat.",
        "fields": [
            {"key": "max", "type": "number", "label": "max passes",
             "default": 3, "min": 1, "max": 20},
            {"key": "until", "type": "rules", "label": "stop when"},
        ],
    },
    "human": {
        "label": "Human",
        "icon": "⚑",
        "accent": "amber",
        "inputs": "*",
        "out": ["out"],
        "fan": "each",
        "summary": "park here for a person",
        "doc": "Stops the branch and files what reached it for review. The "
               "run does not block a thread waiting: the message is recorded "
               "as parked and the graph finishes with that branch open, so "
               "approving it is a separate act rather than a timeout.",
        "fields": [
            {"key": "note", "type": "textarea", "label": "what to check"},
        ],
    },
    "output": {
        "label": "Output",
        "icon": "◎",
        "accent": "emerald",
        "inputs": "*",
        "out": [],
        "fan": "each",
        "summary": "the graph's answer",
        "doc": "A terminal. Whatever reaches an output is what the graph "
               "returns; several outputs means several answers, each named.",
        "fields": [
            {"key": "label", "type": "text", "label": "label"},
        ],
    },
}

# ── gate predicates ──────────────────────────────────────────────────
#
# A fixed vocabulary, on purpose. A gate that took an expression would be a
# code path a shared graph could smuggle anything through, and the point of a
# gate is that reading it tells you what it does.

OPS: Dict[str, Dict[str, str]] = {
    "contains":     {"label": "contains",        "arg": "text"},
    "not_contains": {"label": "does not contain", "arg": "text"},
    "matches":      {"label": "matches regex",   "arg": "text"},
    "equals":       {"label": "equals",          "arg": "text"},
    "starts_with":  {"label": "starts with",     "arg": "text"},
    "ends_with":    {"label": "ends with",       "arg": "text"},
    "longer_than":  {"label": "longer than (chars)", "arg": "number"},
    "shorter_than": {"label": "shorter than (chars)", "arg": "number"},
    "gt":           {"label": "greater than",    "arg": "number"},
    "lt":           {"label": "less than",       "arg": "number"},
    "empty":        {"label": "is empty",        "arg": "none"},
    "not_empty":    {"label": "is not empty",    "arg": "none"},
    "ok":           {"label": "the step succeeded", "arg": "none"},
    "failed":       {"label": "the step failed", "arg": "none"},
}

# what a rule may look at. Anything else is read out of the message's `data`
# by key (field "data.score" -> message["data"]["score"]).
FIELDS: List[Dict[str, str]] = [
    {"key": "text", "label": "the message"},
    {"key": "agent", "label": "the agent that produced it"},
    {"key": "port", "label": "the port it arrived on"},
    {"key": "from", "label": "the node it came from"},
]


def descriptor() -> Dict[str, Any]:
    """The whole protocol, as the console reads it."""
    return {
        "version": 1,
        "kinds": KINDS,
        "ops": OPS,
        "fields": FIELDS,
        "message": {
            "text": "what the sending node produced",
            "data": "structured extras",
            "from": "sending node id",
            "port": "the output it left by",
            "ok": "did the sender succeed",
            "agent": "the agent that produced it",
            "hops": "edges crossed so far",
        },
    }
