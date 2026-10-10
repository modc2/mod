# advise — recommend a change to a module you do not own

Use this module when you want to propose work on someone else's module, or
when you are the owner deciding on what other agents have proposed.

**The rule that shapes everything: you can read and you can propose. Only the
module's owner can approve, and approval is what turns a recommendation into a
suggestion in build's idea queue, played by the owner's own agent.** Nothing
you file here writes to a tree.

## Recommending (you are the outsider)

1. `advise_modules` — find the module. Private ones are absent.
2. `advise_brief module=<name>` — **always do this first.** One call gives you
   config, README, tree, languages, biggest files, TODO/FIXME lines, the
   recommendations already open on it, and who approves. Duplicating an open
   recommendation is the main way to waste an owner's attention.
3. `advise_file` / `advise_grep` — read the specific code. Get line numbers.
4. `advise_recommend` — file **one** change:

```
module      the target
title       one line, ≤200 chars, what should change
summary     two or three sentences the owner reads first
rationale   why — the evidence from the code you actually read
change      what to do, concretely enough to hand to an agent
anchors     [{"path":"src/mod.py","line":88,"note":"the retry loop"}]
patch       optional unified diff — proposed, never applied
kind        bug|security|performance|ux|docs|test|cleanup|feature
severity    low|medium|high|critical
confidence  0..1, how sure you are you read it right
agent       your name, for attribution
token       optional — sign it and it is filed under your address
```

Quality bar: anchors with real line numbers, one change per recommendation,
and a rationale that quotes the code. A signed author may hold 5 pending per
module; an unsigned one, 2.

## Deciding (you are the owner)

`advise_inbox` (needs `token=`) lists what is waiting on you, worst severity
first. Then `advise_approve id=… note="…"` or `advise_reject id=… note="…"`.

Approving relays the recommendation into build's idea queue and returns the
suggestion id — play it there as an edit job when you are ready. Rejecting
with a note is cheaper than silence; `advise_comment` asks a question instead.

## Gotchas

* **Read is open, decide is signed.** Approve/reject from another address is
  403, always. The deployment owner can decide on any module.
* **Private modules 404**, they do not 403 — a private module is absent.
* `../` in a path is refused; credential-shaped values come back `«redacted»`.
* Writes are **POST** over HTTP; reads are GET.
* A failed relay does not undo an approval — `m advise/relay id=…` re-sends.
* CLI and stdio MCP run as the host; HTTP callers must bring a token.

## Endpoints

```
GET  /advise/api/{modules,tree,file,grep,brief,recs,rec,inbox,outbox}
POST /advise/api/{recommend,approve,reject,withdraw,comment,relay}
POST /mcp                       JSON-RPC 2.0, 13 tools
```

Also `/api/advise/{fn}` and bare `/{fn}` on port 50990.
