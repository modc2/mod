# agenti

The money layer for AI agents, from [nirholas/agenti](https://github.com/nirholas/agenti).
Gives an agent its own wallet (EVM + Solana) so it can pay x402 APIs, receive
USDC and check balances, as an SDK (`@agenti/sdk`) and an MCP server (`@agenti/mcp`).

Upstream is vendored unmodified in `src/` at commit `72cfb67a` (2026-10-11).
`mod.py` is a thin wrapper.

```
m agenti/info                  # pin + packages
m agenti/packages              # workspace packages
m agenti/readme package=mcp    # repo or package README
m agenti/install               # pnpm install (npx pnpm@9 if pnpm is absent)
m agenti/build                 # pnpm -r build
m agenti/test                  # pnpm -r test
m agenti/mcp                   # @agenti/mcp on stdio (after build)
```

Notes
- Upstream needs Node >= 18 and pnpm >= 9. The host has Node 18.19 and no global pnpm.
- Wallet keys go in env (`src/.env.example`) and never into this repo.
- Not routed (`private: true`): there is no web app yet.
- License: see `src/LICENSE`. The packages are Apache-2.0.
