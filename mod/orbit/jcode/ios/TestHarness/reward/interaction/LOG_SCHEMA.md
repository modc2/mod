# jcode TUI log schema (for grounding the user model in real usage)

Logs live in `~/.jcode/logs/jcode-YYYY-MM-DD.log`. Lines look like:

    [2026-06-28 00:15:41.814] [INFO] <message>

`<message>` is sometimes a structured event:

    EVENT event=<TYPE> key=value key=value ...

## Structured EVENT types observed (3-day sample, by volume)

| event= | vol | meaning / useful fields |
|--------|-----|-------------------------|
| AGENT_PROVIDER_STREAM_LIFECYCLE | 12303 | model streaming; not a user action |
| SESSION_PERSISTENCE | 11935 | session saved; `append_ms`, `chars` |
| TOOL_LIFECYCLE | 9907 | a tool ran. `resolved_tool_name=`, `phase=start|end`, `execution_mode=AgentTurn`, `cwd=` |
| model_routes_summary | 4235 | routing; not a user action |
| SERVER_REQUEST_LIFECYCLE | 2222 | a client request hit the server (proxy for a user message / command) |
| SESSION_LIFECYCLE | 1750 | `phase=`, `client_connection_id=`, `allow_takeover=`, `client_has_local_history=` |
| SWARM_LIFECYCLE | 963 | swarm member status; `phase=member_status_updated`, `new_status=` |

## User-action verbs (grep counts, 3-day sample)

These are the closest proxies to "what the user does", and the actions the iOS
app must also support, so they should weight the mobile user graph:

| verb | count | iOS equivalent action |
|------|-------|-----------------------|
| compact | 8399 | context compaction notice (passive) |
| diff_mode | 1951 | (TUI-only; n/a on mobile) |
| interrupt | 1845 | cancel / stop button |
| soft_interrupt | 1164 | queue-a-message-mid-run |
| cancel | 262 | cancel |
| scroll_up/down/page | ~ | transcript scrolling |
| resume | 251 (today) | resume_session / switch session |
| side_panel | 39 | (n/a on mobile) |

## How to mine it (for log_mining.py)

1. Read the last N daily logs (default 7) under `~/.jcode/logs/`.
2. Count: user messages (`SERVER_REQUEST_LIFECYCLE` start, or `Assistant:` turns
   as a proxy for turns), interrupts, soft_interrupts, cancels, resumes/session
   switches, model switches, scrolls, tool runs (`TOOL_LIFECYCLE phase=start`).
3. Emit a normalized frequency profile dict, e.g.:
   `{"send_message": 0.55, "scroll": 0.20, "soft_interrupt": 0.08,
     "interrupt": 0.06, "switch_session": 0.05, "change_model": 0.02, ...}`
   These become the relative edge `weight`s in the mobile ActionGraph.
4. Be robust: logs are huge (100k+ lines/day) and noisy; stream line-by-line,
   tolerate missing files, and DEGRADE GRACEFULLY to literature-default weights
   if no logs are found (so the engine still runs in CI / on a fresh machine).

## Caveats (be honest in evidence)

- TUI usage is a *proxy* for mobile usage, not identical (no diff_mode/side_panel
  on mobile; mobile likely has relatively MORE scroll + read, fewer power-tools).
  log_mining.py should expose the raw TUI counts AND the mobile-mapped weights so
  the mapping assumptions are auditable.
- These logs are this user's personal data; keep mining read-only and local.
