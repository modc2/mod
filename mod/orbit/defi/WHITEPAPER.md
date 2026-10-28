# DeFi ✦ Modular Finance — Whitepaper

*Version 1.3 · the `defi` module of the mod protocol · this document is served at `GET /whitepaper`, content-addressed into the protocol object store, and readable by agents through the `defi_whitepaper` MCP tool.*

---

## 1. Thesis

Every place money can go that gives a return is the same shape: it has **returns** (what it pays and out of what), **liquidity** (how deep it is, how you get in, how fast you get out), **conditions** (what it is subject to), and an **execution path** (who signs what to enter it). DeFi protocols hide this shape behind ten different apps. This module makes the shape explicit: every yield venue — a DefiLlama-indexed pool on Ethereum, Base or Solana, a Bittensor subnet, a vault you composed yourself, the BlocTime treasury — is a **finance module** with those four faces, and one desk enters any of them.

A module here is not a wrapper token and not a fund. It is a *description with an adapter*: the description says what the venue pays and costs, the adapter says exactly which calls enter and leave it. Money never routes through this service.

## 2. Architecture

Five layers, each honest about what it is:

- **The composer** — reusable Solidity blocks (vault, AMM, lending market, governor, treasury…) with typed ports, validated as a graph, compiled with solc, and deployed as an ordered plan **signed by the user's browser wallet**. Each block ships an agent security audit; an audit reduces the unknowns and certifies nothing.
- **The HUB** — a hand-vetted shortlist of legitimate protocols for plain USD. The names, tiers and risk notes are written by hand; every number beside them is joined live from the yields index. *Curated is not certified.*
- **The modules index** — DefiLlama's ~17k pools, Bittensor subnets, composed vaults and the treasury, normalized into finance modules. Fees (`apy_base`) are never merged with emissions (`apy_reward`); the 30-day mean and 7-day change ride along so any quoted rate can be checked against how it behaved.
- **The desk** — quotes and executes entries, exits and DEX swaps. A quote always measures the round trip: what entering *and leaving today* would really cost.
- **The treasury** — weekly payouts on BlocTime's clock (Friday 17:00 UTC), split pro-rata by BLOC across registered holders. A choice is a PLAN until its status is `locked`; the ledger, the arithmetic and the contract are reported as three distinct layers.

## 3. Keys and wallets — who signs what

**This service holds no private key, ever.** Every operation names its signer, and the signer is chosen by the operation:

| Operation | Signer |
|---|---|
| Deploying a composed protocol | **your browser wallet** (MetaMask, Rabby) — the API hands over bytecode and a plan, nothing more |
| Entering/exiting an EVM module (ERC-4626 deposit, Aave/Comet supply, receipt-token swap) | **your browser wallet**, from the machine-readable `wallet` plan in every quote — or the `eth` module's keystore, your choice |
| Entering/exiting a Solana module | **your injected Solana wallet** (Phantom) via Jupiter — or the `solana` module's keystore |
| Bittensor subnet stakes | the `bt` module's coldkey only — no browser wallet speaks Bittensor, and pretending otherwise would be a lie |
| Treasury lock | the `eth` module, against the bound contract — or deploy your own treasury block, which your browser wallet signs |
| Sign-in | `personal_sign` of a five-minute challenge → a 24-hour stateless bearer token |

When a chain module executes, the caller's own bearer token is **forwarded verbatim and never stored** — a compromise of this desk cannot sign. When the browser wallet executes, the desk only *plans* (approve calldata, deposit arguments, router route) and *records* (the position, with the transaction hashes as evidence). Guards stack: mainnet operations return `needs_confirm` until confirmed here, and the chain module's own gate still applies underneath; a browser wallet's confirmation screen is its own gate.

## 4. Storage — everything under the protocol

Every durable artifact is **content-addressed under the protocol's object store** (`~/.mod/defi/`, served at `/objects/{cid}`): a CIDv1 (raw, sha2-256, base32) computed over canonical bytes, so the identifier is the content and sharing is copying a string.

- **Protocol designs** — `POST /protocols/{id}/publish` → CID; anyone imports the same diagram from the CID.
- **Module whitepapers** — every finance module generates one (`GET /modules/{id}/whitepaper`): its four faces, its risks, its execution path, as-of-dated, stored under the protocol at its CID.
- **This document** — `GET /whitepaper`, same store, same rule.
- **The book** — positions live in the local ledger with the rate at entry frozen beside the rate now; a row is written only when something was actually sent.

## 5. Agents

The module is an **MCP server** (`POST /mcp`, Streamable HTTP, JSON-RPC 2.0) exposing the full surface — catalog, audits, validation, planning, yields, hub, modules, quotes, entries, positions, treasury, whitepapers — as tools, with blocks readable as `defi://block/{id}` resources so an agent reads the Solidity it is about to deploy. Anonymous callers get the public tools; a bearer token carries through to the chain modules exactly as it would by calling them directly.

## 6. Honesty rules

These are load-bearing, not copy:

1. **Fees and emissions never merge.** A headline APY that is mostly token emissions says so.
2. **A quote measures the round trip.** `round_trip_cost_pct` is the liquidity restriction, measured today, not asserted.
3. **Curated is not certified.** Every HUB card carries what can go wrong beside why it is here.
4. **Projections are labelled.** The book's earnings line says it is `amount × APY-at-entry × days/365`, not a balance.
5. **A ledger row means something moved.** Dry runs, refused confirms and unpriced plans leave no trace in the book.
6. **Read-only is said out loud.** A module without an adapter is listed for its terms and marked not enterable.

## 7. Trust model

The catalog contracts are unaudited reference implementations with agent audits attached. The HUB is a hand-written vetting layer over market data this module does not control. The chain modules enforce their own confirmation and spend rules. The user's browser wallet is the root of authority for everything it signs. Deploy to a testnet first; read the source and the audit; treat mainnet use as your own risk.

---

*The rates are the market's, not ours.*
