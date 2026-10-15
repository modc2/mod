# grantfinder

A Claude agent that sweeps the web for grant programs and tags every one.
It starts with **open source** and **crypto**.

```
lanes ──> Claude Code (WebSearch + WebFetch only) ──> {grants:[...]} ──> tagger ──> SQLite (merge by URL)
```

- **Lanes** (`grantscan/lanes.py`) split the landscape into 12 slices. One lane is one agent run.
  - Open source: foundations, corporate funds, security, public/research money, AI, fellowships.
  - Crypto: Ethereum & L2s, other L1s, Bitcoin & privacy, public goods, DeFi, ZK research.
  - To add a category or lane, add a row.
- **Agent** (`grantscan/agent.py`):
  - Spawned from `orbit/claudecode`'s `harness` spec (bin, base args, model and system-prompt flags), so a CLI flag change made there applies here too.
  - Fenced on top of that spec:
    - `--tools WebSearch,WebFetch`: no shell, no file access.
    - `--strict-mcp-config`: none of the host's MCP servers.
    - `--json-schema`: the answer must be validated records.
    - `--max-budget-usd`: a cap per lane.
  - It is told to read each program's **official** page and to record the page it used as `evidence_url`.
- **Tags** (`grantscan/tags.py`): the agent's tags are normalised, and a keyword scan adds the tags the record supports. Tags are grouped as:
  - `category`: open-source, crypto
  - `ecosystem`: ethereum, solana, bitcoin, ...
  - `topic`: zk, security, public-goods, ai, ...
  - `mechanism`: retroactive, quadratic, bounty, fellowship, ...
  - `status-*`
  - `amount-*` buckets
- **Store** (`grantscan/store.py`): `~/.mod/grantfinder/grants.db`.
  - When the same program is found again, its record merges: newer fields win, and tags and lanes are combined.
  - Every agent run is logged with its cost and the searches and fetches it made.

## Use

```
m grantfinder/sweep                                  # all lanes, blocking (spends the host's Claude account)
m grantfinder/sweep category=crypto concurrency=3
m grantfinder/sweep lanes=crypto-bitcoin budget=1    # budget = USD cap per lane
m grantfinder/grants tags=crypto,zk status=open
m grantfinder/tags · m grantfinder/lanes · m grantfinder/runs
m grantfinder/verify <id>                            # re-check one grant on its official page
m grantfinder/serve                                  # pm2 `grantfinder`, http://127.0.0.1:51380/app
m grantfinder/test
```

## HTTP (:51380, also under `/grantfinder` and `/grantfinder/api`)

| route | |
|---|---|
| `GET /app` | web app: search, grouped tag filters, sweep progress, CSV |
| `GET /grants?q=&tags=a,b&status=&sort=deadline\|amount\|new\|name` | filtered list (all listed tags must match) |
| `GET /grants/<id>` · `GET /tags` · `GET /lanes` · `GET /runs` · `GET /export.csv` | reads, no auth |
| `POST /sweep {lanes?,category?,model?,budget?,concurrency?}` · `POST /verify {id}` · `DELETE /grants/<id>` | **loopback only**: refused if the request came through a proxy, because these spend the host's Claude account |

## Cost

A lane costs roughly $0.3–1.5 with `sonnet`. A full 12-lane sweep is capped at 12 × `budget`.
`/health` shows the total spent. Pass `model=haiku` for cheaper, shallower sweeps.
