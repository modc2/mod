# agenti

Money for your AI agents. This is a web app, API and MCP server built on [nirholas/agenti](https://github.com/nirholas/agenti).
Upstream is vendored unmodified in `src/` at commit `72cfb67a`.

**App:** `http://localhost:50129/agenti/`

| Tab | What it does |
|---|---|
| HOME | Total balance, spend today vs limits, getting-started steps, recent activity |
| WALLETS | Create or import wallets. One 12-word phrase gives an EVM key (`m/44'/60'/0'/0/i`) and a Solana key (`m/44'/501'/i'/0'`), the same paths as upstream, MetaMask and Phantom. Shows balances on Base, Ethereum, Arbitrum, Polygon, Solana and two testnets. Rename, set default, show secrets (click twice; hidden again after 60 s), delete (a copy is kept in `deleted/`), sign a message |
| PAY | Paste an x402 URL or pick one from orbit/x402's index (paid APIs this wallet can pay are listed first). **Check price** calls it without paying. **Pay & call** signs an EIP-3009 USDC authorization (x402 v1 and v2) and shows the response and the settlement tx. Spending limits: per call, per day, a mainnet switch, allowed and blocked sites |
| GET PAID | Payment request: amount, chain and wallet give a QR code and a pay link (EIP-681 / Solana Pay). It counts as paid once the wallet's USDC balance rises by the amount; checked every 15 s |
| ACTIVITY | Every payment and price check |
| TOOLKIT | Connect snippets (this node's MCP, upstream `@agenti/mcp`, the SDK), the 58 upstream MCP tools by group, packages, the 29 examples with source, READMEs |

**Owner gate:** loopback with no proxy headers, or a build-console sign-in whose `whoami` says `is_owner`. Everyone else can only look up an address, browse discovery and read the toolkit.

**MCP:** `POST /mcp` with these tools: `agenti_overview`, `agenti_wallets`, `agenti_create_wallet`, `agenti_balances`, `agenti_lookup`, `agenti_services`, `agenti_probe`, `agenti_pay`, `agenti_policy`, `agenti_create_invoice`, `agenti_check_invoice`, `agenti_ledger` and `agenti_sign_message`.
```
claude mcp add --transport http agenti http://127.0.0.1:50129/mcp
```

**State:** `~/.mod/agenti/` (0700) holds `wallets.json` (0600, keys), `policy.json`, `ledger.jsonl`, `invoices.json` and `deleted/`. None of it goes in the repo.

```
m agenti/serve                     # app + API + MCP (pm2 runs this via m.serve)
m agenti/wallets | create_wallet name=bot | balances
m agenti/probe url=https://...     # price
m agenti/pay url=https://...       # paid call, inside limits
m agenti/policy [max_per_call_usd=0.1]
m agenti/test                      # tests/ (mock paywall, no network)
m agenti/install | build | test_upstream | mcp   # upstream pnpm workspace
```

**Layout:** `agentiapp/` holds `wallets.py`, `chains.py` (public RPCs, 30 s cache), `pay.py` (x402, policy, ledger, discovery), `invoices.py`, `toolkit.py` and `server.py`. `web/index.html` is a single-file UI.

**Limits:**
- Only USDC `exact` offers on EVM chains are signed. For Solana x402, use the upstream SDK.
- Invoices match on balance changes, so two open requests on the same wallet and chain can't be told apart. The UI warns when this happens.
- The module is private, so there is no public `/agenti` route yet. Use the bare port, or add a caddy override.
