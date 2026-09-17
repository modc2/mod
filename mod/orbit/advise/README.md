# advise ✎

**Someone else's agent reads your module and proposes a change. You decide.**

A merge request needs a fork and a contributor who can write the code. An
issue needs nothing and says nothing. `advise` is the shape in between, aimed
at agents: read a module you do not own, and file one specific proposed change
— with file and line anchors, the reasoning behind it, and optionally a patch
— into a queue addressed to the address in that module's `config.json`.

Filing binds nothing. It does not touch a tree, start a job, or expire into an
action. **The owner approves or rejects**, and an approval is the only thing
that moves: it relays the recommendation into [build](../build)'s idea queue
as a suggestion the owner plays as an edit job, on their account, with their
own agent writing every line that lands.

That asymmetry is the whole design. The door to *file* can stay open —
unsigned callers included — precisely because the door to *act* is a
signature.

```
  outsider's agent          advise                     owner
  ───────────────           ──────                     ─────
  brief / file / grep  →    read surface
  recommend            →    pending ─────────────────→ inbox
                                                       approve  ─→ build idea queue
                                                       reject   ─→ closed, with a note
```

## The read half

An outside agent cannot recommend anything useful about code it has not read,
and build's file API deliberately confines a peer to their own userspace — so
this module publishes its own read surface. It shows ordinary published
source, minus the things nobody meant to publish:

* a module marked private in build is **absent**, not refused;
* dependency and build output (`node_modules`, `vendor`, `target`, `.next`,
  `__pycache__`, …) is pruned;
* `.env`, `*.pem`, `*.key`, `secrets*` are never opened, and in anything that
  is, a credential-shaped value comes back as `«redacted»`;
* bytes per file, files per listing and hits per grep are all bounded.

```bash
m advise/modules                       # 320+ modules you may scan
m advise/brief module=dns              # one call: everything an agent needs
m advise/file module=dns path=mod.py start=1 lines=200
m advise/grep module=dns query="def resolve"
```

`brief` is the one to start from: config, README, file tree, languages,
biggest files, TODO/FIXME lines, **the recommendations already filed** (so you
do not repeat one) and the address that has to approve yours.

## The write half

```bash
m advise/recommend module=dns \
  title="cache the zone between resolves" \
  summary="resolve() rebuilds the zone on every query" \
  rationale="mod.py:212 walks the fleet for each lookup" \
  change="memoise the zone for 30s, invalidate on record write" \
  anchors='[{"path":"mod.py","line":212,"note":"resolve()"}]' \
  kind=performance severity=medium confidence=0.8
```

```bash
m advise/recs status=pending           # the queue, public
m advise/rec id=rc_1a2b3c4d            # one, whole
m advise/inbox                         # what is waiting on me
m advise/approve id=rc_1a2b3c4d note="keep the cache small"
m advise/reject  id=rc_1a2b3c4d note="the zone is already cached upstream"
```

A recommendation carries: title, kind (bug / security / performance / ux /
docs / test / cleanup / feature), severity, summary, rationale, the proposed
change, anchors, an optional unified diff, confidence, effort, evidence and a
thread. Anchors are **checked** at filing time — a path that is not in the
module is kept and flagged, because a stale anchor is worth the owner seeing.

Filing is open. Quotas, not accounts, keep it honest: five pending per module
for a signed author, two for an unsigned one, thirty pending in total.

## Approval, and what an approval does

`approve` is owner-only, proven by a mod-protocol token from the address in
that module's `config.json` (the deployment owner can always decide). It:

1. marks the recommendation approved, with the decider and a note;
2. renders it — attribution, reasoning, anchors, patch — and POSTs it to
   `build`'s `/modules/{name}/suggestions` as a suggestion;
3. records the suggestion id on the recommendation.

From there it is an ordinary build idea: play it as an edit job when you are
ready, and it snapshots and rolls back like any other edit. **advise never
writes to a tree.**

If build is asleep or refuses the relay, the approval still stands and the
reason is recorded; `m advise/relay id=…` re-sends it.

## Surfaces

| | |
|---|---|
| CLI | `m advise/<fn>` — the anchor class in `mod.py` |
| REST | `/advise/api/{fn}`, `/api/advise/{fn}`, or bare `/{fn}` on :50990 |
| Console | <https://modc2.com/advise/> — SCAN, QUEUE, INBOX |
| MCP | `POST /mcp` — 13 tools, or `python3 mcp.py` over stdio |

All four dispatch into the same `Mod` class, so they cannot drift. Reads are
public; `inbox`, `approve`, `reject` need a token; writes are POST only.

### Identity

A mod-protocol token in `x-mod-token` or `Authorization: Bearer`, verified by
the protocol's shared `auth`. The console mints one with a single
`personal_sign` — no gas, no challenge endpoint. Unsigned callers get a stable
`anon:` pen name: they may read, file and comment, and may not decide.

The token the console mints is deliberately shaped to satisfy **build** too
(`data` a string, `time` whole seconds, EIP-191 signature), so an approval can
be relayed as the owner rather than as this host.

> Note: build's currently deployed binary requires edit access to file a
> suggestion, so a relay signed by a viewer key is refused and falls back to an
> unsigned POST. An owner's own token passes today; the source of build's
> queue already accepts unsigned filings, so the fallback will too once that
> binary ships.

## Files

```
mod.py        the anchor — every function, one class
scan.py       the read surface: modules, tree, file, grep, stats, brief
recs.py       the queue: filing, quotas, rendering, the decision, the relay
identity.py   mod-protocol tokens, anon handles, the mod.py shadowing trap
serve.py      console + REST + MCP on one port (50990)
mcp.py        13 MCP tools, stdio or HTTP
web/          the console
tests/        22 tests, offline
```

`m advise/test` runs them. They guard the seam: an outsider can read and file,
an outsider cannot decide, and the relayed text keeps the attribution.
