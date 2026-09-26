---
name: skills
description: Find, install and hand out agent skills. Use this when an agent needs to learn how to do a job it has the tools for but not the method — searching the open web for a SKILL.md, reading one, filing it in this host's catalog, writing your own, or loading several in front of a model at the start of a run.
tools: [fetch, bash]
tags: [agents, marketplace, skills, mcp]
---

# skills

A **tool** is something an agent can call. A **skill** is something it can
learn: a `SKILL.md` — front matter plus instructions — that says when to reach
for the tools it already has and how to use them for one job.

## When to use this

- An agent has the tools but keeps doing a job badly → find a skill for it.
- You want to know what skills exist for X → `search`.
- You want a model to actually follow a method → `load` it into the run.
- You wrote the method down yourself → `write` it into the catalog.

## Finding one

```bash
m skills/search "pdf forms"                    # every source at once
m skills/search excel sources=anthropic,code   # narrow it
m skills/sources                               # what is reachable, what needs a token
```

Six sources: `anthropic` (the official catalog), `topics` (self-tagged repos),
`code` (GitHub code search for `SKILL.md` — needs a token), `github` (repo
search), `awesome` (curated lists), `registry` (every module on this box).

Results are cards. Ranking is source trust × relevance × a capped star bonus,
so popularity cannot beat relevance.

## Reading and installing

```bash
m skills/get gh:anthropics/skills:skills/pdf       # the document itself
m skills/install gh:anthropics/skills:skills/pdf   # into ~/.mod/skills/catalog
m skills/install gh:someone/skill-pack all=true    # every skill in the repo
m skills/installed                                 # the catalog
m skills/remove pdf
```

ids are readable and stable: `gh:owner/repo[:path]`, `mod:orbit/<module>`, or a
plain URL.

## Using one in a run

```bash
m skills/doc pdf                    # the markdown, for one skill
m skills/load names=pdf,deploy      # several, with bodies — start-of-run call
```

`load` is the one that matters: it returns the documents, ready to put in front
of a model. Name them — handing a model forty skills is worse than handing it
none.

## Writing one

```bash
m skills/write name=deploy-checklist description="Ship safely" body=@notes.md
```

Front matter is filled in from the arguments when the body has none. Same
format, same catalog, same parser as a scraped skill.

## What it will not do

Installing a skill writes markdown and nothing else — no script runs, no
package installs, no new executable tool appears. `tools:` names tools the
agent already has. A skill teaches the use of a capability; it cannot grant
one. Assume any skill body is untrusted text: read it before you point a
model at it.

## Where things are

- catalog: `~/.mod/skills/catalog/<name>/SKILL.md` (portable to/from `~/.claude/skills`)
- console: <http://127.0.0.1:50860/skills>
- MCP: `POST http://127.0.0.1:50860/mcp` — ten `skills_*` tools
- writes (install/write/remove/token) need a caller on this box or the bearer
  token in `~/.mod/skills/server.secret`
