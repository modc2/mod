# Upload your own

Everything in the library can come from a file you wrote: a **prompt**, a
**tool document**, a **memory note**, or a whole **agent**. Drop the file on the
market rail's upload panel (the `+` in its header → `upload`), or POST it.

Two carriers, both plain text:

- **Markdown** — optional YAML front matter, then the body.
- **JSON** — one object.

Uploading is a create like any other: sign in first, and what you upload is
filed under your address. Only you (and the host) can edit or delete it.

---

## Upload an agent

An agent is a persona: a name, a one-liner, and the system prompt it runs
under. The body of the file **is** the system prompt.

```markdown
---
type: agent
name: release-captain
description: Cuts releases and writes the changelog
icon: '>>'
model: anthropic/claude-sonnet-4.5
tools: [bash, read, edit, git, test]
---
You cut releases for this repo.

Check the tree is clean, run the tests, bump the version, write the changelog
from the commit log, tag it. Never push without a green test run.
```

| key | what it does |
|---|---|
| `name` | the agent's slug — lowercased, spaces become dashes |
| `description` | the one-liner shown in the market |
| `icon` | glyph beside the name (default `>_`) |
| `model` | model override, else the console's pick is used |
| `tools` | restrict it to these tools; omit for the full loadout. A fleet module counts — `mod.git` is a tool name like any other |
| `harness` | `claude` or `codex` — hand the run to that CLI instead of this module's loop (host owner only, and only if that CLI is installed here) |
| body | the goal / system prompt |

JSON works the same:

```json
{
  "type": "agent",
  "name": "release-captain",
  "description": "Cuts releases and writes the changelog",
  "tools": ["bash", "git", "test"],
  "goal": "You cut releases for this repo. Tests green before any tag."
}
```

Re-uploading an agent you already own updates it in place. If the name is
taken by someone else's agent, rename yours.

## Upload a prompt

```markdown
---
type: prompt
name: Bug hunt
description: Root-cause a failure and fix it
tags: [debug, fix]
---
Find the root cause of this bug. Reproduce it, read the relevant code paths,
explain the cause, then apply a minimal fix and verify it.
```

A bare `.md` or `.txt` file with no front matter uploads as a prompt named
after the file.

## Upload a tool document

A tool document is instructions, not code — an uploaded `SKILL.md` becomes a
document the agent is handed as context. Nothing in it is ever executed. (The
tools the agent actually *calls* live in the TOOLS tab: the ones shipped here,
the shell tools you define there, and the fleet.)

```markdown
---
type: tool
name: pdf
description: Fill, split and merge PDF files
tags: [docs]
license: MIT
---
# PDF handling

When asked to edit a PDF, use pdftk for page operations and qpdf to repair…
```

## Upload a memory note

```markdown
---
type: memory
name: api conventions
tags: [project]
---
Every endpoint returns `{error}` on failure and never raises past the router.
Ports live in config.json, secrets in ~/.mod/<module>/.
```

---

## How the kind is decided

First hit wins:

1. the kind you picked in the upload panel (or `kind=` on the API call)
2. `type:` in the front matter or the JSON object
3. the filename — `SKILL.md`, `*.tool.md`, `*.agent.md`, `*.memory.md`,
   `*.prompt.md`, or a `tools/ skills/ agents/ memory/ prompts/` path
4. the shape — `goal`/`harness`/`icon` ⇒ agent, `allowed-tools`/`license` ⇒
   tool document, `text` ⇒ prompt, `content` ⇒ memory
5. prompt

So `release-captain.agent.md` needs no `type:` line, and picking a kind in the
panel overrides everything.

`type: skill` still works everywhere `type: tool` does — that is what these
documents were called before, and files in the wild say it.

Limits: 200,000 characters per file; a tool document's body is clipped at
120,000.

---

## From the API

```bash
API=http://localhost:50117          # or https://<host>/agent/api

# upload a file (multipart) — kind is optional, 'auto' by default
curl -F file=@release-captain.agent.md -F key=$TOKEN $API/library/upload/file

# or send the text as JSON
curl -X POST $API/library/upload -H 'content-type: application/json' \
  -d "$(jq -n --rawfile t release-captain.agent.md \
        '{text: $t, filename: "release-captain.agent.md", kind: "auto"}')"

# install anything from a shared localfs CID — the bundle says what it is
curl -X POST $API/library/import -H 'content-type: application/json' \
  -d '{"cid": "Qm…"}'

# this page, as served to the console
curl $API/library/formats
```

`$TOKEN` is the signed session token the console gets when you sign in with
your wallet (`GET /whoami?key=…` resolves it to your address). Without one the
server refuses the create — there'd be no address to file it under.

Every uploaded item is pinned to localfs and comes back with a `cid`. Share
that CID (or its QR from the library) and anyone can install your agent with
one call.
