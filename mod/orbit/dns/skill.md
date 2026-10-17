# dns

The mod protocol's name layer: the zone is derived from the module fleet,
served authoritatively on UDP and TCP, and carries the protocol's attribution
beside every name. 28 MCP tools, a REST API, a CLI and a console all call the
same functions, so an agent, a shell and a human never see different answers.

API `:5380` (`/api/dns`) · console `/dns` · MCP `POST /mcp` · listener
`udp+tcp :15353`

## When to reach for it

- "where is module X" — the app URL, the API URL, the MCP endpoint, the record
- "whose module is this" / "what code is actually deployed here"
- something is unreachable and you cannot tell whether it is DNS, the router,
  or a dead upstream port
- someone wants the protocol on their own domain
- adding MX, TXT, CAA or a CNAME to a host this box is authoritative for
- "what changed in the zone, and who changed it"
- somebody is stuck or confused about DNS itself and needs it explained
  against this deployment rather than in the abstract — `dns_ask`

Not for: HTTP routes and TLS (`caddy`), waking slept modules (`activator`),
the module registry itself (`registry`).

## The order that matters

1. **`dns_resolve`** — first, for anything shaped like an address. It takes a
   bare module name, a hostname, a gateway path or a full URL and returns all
   four addresses, whether the upstream ports are live, the DNS answer, and the
   attribution. `found: false` means no module declares `route: true` under
   that name — the name may still resolve via the wildcard, which is exactly
   the failure that looks like DNS but is not.
2. **`dns_check`** — when resolve looks right and the thing is still
   unreachable. It diffs the record held here against a public resolver and
   names the disagreement: `not published` (nothing has delegated the name
   here), `not held here`, `proxied` (a CDN is in front), `mismatch`. This is
   where "it works locally" usually dies.
3. **`dns_attribution`** — who a module belongs to and which code it is.
   `verify=true` also fetches the protocol's signed module card and checks the
   signature.
4. **`dns_ask`** — when the question is vaguer than a name. Plain words in
   ("why is my domain not working", "how do I use my own domain", "how long
   until my change takes effect"); it pulls any name out of the question and
   resolves it before answering, so the answer is about the live box. Pair it
   with `dns_guide` for the setup checklist scored against this deployment,
   and `dns_explain` for one word at a time.

   No model sits behind those three: the sentences were written in advance and
   are selected by matching, then filled in with live state. That makes them
   deterministic and offline-safe — and it means a question it cannot place
   comes back as `confidence: none` with `understood: null` rather than as an
   invention. Treat that as "not covered", not as "no answer exists".

## Attribution, precisely

Two claims, and they are not the same claim:

- `owner=` in the record is what the module **declares** in its own
  `config.json`. A module that declares none publishes none — do not report
  the host's address as a module's owner.
- `key=` is the address this **box** signs its module cards with. It backs a
  narrower statement: this deployment serves this CID. `dns_attribution` with
  `verify=true` is what checks it; a TXT record on its own proves nothing,
  since whoever holds the zone wrote it.
- `cid=` is the module's `schema` CID. If it is stale, the record is stale —
  it repeats config.json rather than hashing the tree at query time.

Read it from outside with `dig +short TXT _mod.<mod>.<host>`, and the
deployment as a whole with `dig +short TXT _mod.<host>`.

## Writes

Every write takes a mod-protocol token (`Authorization: Bearer`), and the
answer to "may I" is data: `dns_operations` lists every operation with the
standing it needs, `dns_whoami` says which ones the current token can run.

- any signed caller: `dns_zone_register` a host **they** control, then own
  every record in it (`dns_record_set`, `dns_zone_target`, `dns_zone_verify`).
  This needs no permission from the deployment owner — reach for
  `dns_plan` first, which prints the whole sequence including the one step
  (`caddy add_host`) only the box's owner can do.
- IP addresses are masked (`x.x.x.x`) in every response unless the caller is
  the deployment owner — `settings.private_ips = false` turns the mask off
- deployment owner only: `dns_host_set`, `dns_settings`, records in the system
  zone, `dns_serve`/`dns_kill`, `dns_router_sync`.

Derived records cannot be deleted — they are computed, not stored. Write a
record over one to shadow it, or turn the derivation off in settings.

## Traps

- **The listener is on 15353, not 53.** A `dig` without `-p 15353` asks the
  box's stub resolver instead and will disagree with this module for reasons
  that have nothing to do with it.
- **`dns_lookup` is this server; `dns_check` is the world.** Confusing them
  makes an undelegated zone look healthy.
- **A wildcard hides a missing module.** `*.host` resolves, so
  `newmod.host` answering does not mean `newmod` is routed. `dns_resolve`
  says `routed: false` — believe that, not the A record.
- **Changing the host is not changing the routes.** `dns_host_set` moves every
  derived name; the router only follows after `dns_router_sync` (or
  `m caddy/host <host>`).
- **A denial is an answer.** Refusals come back naming who may do the thing and
  what you can do instead; read the message rather than retrying.
