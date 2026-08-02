"use client";

/**
 * In-app documentation: what the store is, how to use it, the full HTTP API
 * surface, and how to plug an LLM agent in over MCP. Content lives here (not
 * fetched) so the page works even when the API is down.
 */

import Link from "next/link";
import { SubHeader } from "@/components/SubHeader";

const API_BASE = "/api/store";

type Ep = { method: string; path: string; auth: string; docs: string };

const ENDPOINTS: Ep[] = [
  { method: "GET", path: "/status", auth: "—", docs: "Module + backend status." },
  { method: "GET", path: "/me", auth: "token", docs: "Caller address, admin flag, quota, terms state." },
  { method: "POST", path: "/put", auth: "token", docs: "Upload file/text/image; form fields: backend, key, public, pool. Whitelisted + within quota + signed terms." },
  { method: "POST", path: "/register", auth: "token", docs: "Reference an external CID (arweave, ipfs elsewhere, s3, …) without uploading bytes." },
  { method: "GET", path: "/get", auth: "optional", docs: "Retrieve by CID. Private objects need ?token= or a Bearer header (owner / grant / pool)." },
  { method: "GET", path: "/preview", auth: "optional", docs: "Peek at content: up to max_bytes (text decoded when possible) + size + truncated flag." },
  { method: "GET", path: "/object", auth: "optional", docs: "Full object profile: stored when/by whom, backends, visibility, semhash, links graph, access roster." },
  { method: "GET", path: "/list", auth: "token", docs: "List your objects (+ visibility / scheme / external url)." },
  { method: "GET", path: "/search", auth: "token", docs: "Substring q or semantic_q search; scope mine | shared | all." },
  { method: "GET", path: "/shared", auth: "token", docs: "Objects shared with you via grants or pool membership." },
  { method: "POST", path: "/publish", auth: "token", docs: "Flip an object private ↔ public (owner only)." },
  { method: "DELETE", path: "/rm", auth: "token", docs: "Delete your object; the store owner may remove any content with ?reason= (audited)." },
  { method: "GET/POST/DELETE", path: "/grants", auth: "token", docs: "Timed access grants: {grantee, cid, scope, ttl_seconds}; list yours; revoke by id." },
  { method: "GET/POST", path: "/pools", auth: "token", docs: "Data pools (mutual access): create, list; /pools/{id} manages members + objects." },
  { method: "POST/GET", path: "/tickets", auth: "token", docs: "One-time short-TTL fetch links: mint, list; /ticket/{code} redeems exactly once." },
  { method: "POST/GET", path: "/handoff", auth: "token", docs: "QR auth transfer between devices: mint a one-time code; /handoff/{code} claims it." },
  { method: "GET/POST/DELETE", path: "/market", auth: "optional", docs: "Marketplace: public browse; /market/list, /market/acquire, /market/like, /market/mine." },
  { method: "POST/GET/DELETE", path: "/pin", auth: "token", docs: "Pin a CID; /pins lists yours; DELETE unpins." },
  { method: "GET/POST", path: "/backends", auth: "token", docs: "List backends; /backends/status readiness (?probe=1 validates keys); /backends/key saves credentials (owner)." },
  { method: "GET/POST", path: "/quota", auth: "token", docs: "Your usage + limit; owner sets per-user limits." },
  { method: "GET/POST", path: "/terms", auth: "optional", docs: "Current terms text + version; /terms/accept records your wallet-signed acceptance." },
  { method: "GET/POST", path: "/onchain", auth: "optional", docs: "Registry + BlocTime gate status; caller BlocTime balance; owner registers on-chain." },
  { method: "POST", path: "/mcp", auth: "optional", docs: "MCP tool server for LLM agents (see the MCP section below)." },
];

const MCP_TOOLS: [string, string, boolean][] = [
  ["store_status", "service + backends status", true],
  ["store_market_browse", "browse marketplace drops (q / tag / seller / sort / free)", true],
  ["store_terms", "current terms of service text + version", true],
  ["store_me", "caller identity, quota, authorization + terms state", false],
  ["store_list", "list your stored objects", false],
  ["store_search", "substring + semantic search over your objects", false],
  ["store_get", "preview an object's content by CID (public CIDs work unauthenticated)", true],
  ["store_object_info", "full object profile incl. the CID links graph", true],
  ["store_put_text", "store a text/JSON payload (name, backend, public, pool)", false],
  ["store_share", "create a timed read grant for an address", false],
  ["store_pin", "pin a CID", false],
  ["store_pins", "list your pins", false],
  ["store_pools", "list your data pools", false],
];

const SECTIONS = [
  ["what", "What is this?"],
  ["quickstart", "Quickstart"],
  ["sharing", "Sharing & access"],
  ["market", "The market"],
  ["backends", "Backends"],
  ["api", "HTTP API"],
  ["mcp", "MCP"],
] as const;

function MethodBadges({ m }: { m: string }) {
  return (
    <>
      {m.split("/").map((x) => (
        <span key={x} className={`docs-method ${x === "GET" ? "get" : x === "DELETE" ? "del" : "post"}`} style={{ marginRight: 4 }}>
          {x}
        </span>
      ))}
    </>
  );
}

export default function DocsPage() {
  return (
    <div className="docs-wrap">
      <SubHeader active="docs" />

      <div className="docs-hero">
        <h1>store docs</h1>
        <p>
          CID-agnostic decentralized storage with a marketplace on top: everything you put in is
          content-addressed, everything addressable gets its own page, and every capability here is also an
          HTTP endpoint and an MCP tool.
        </p>
        <nav className="docs-toc">
          {SECTIONS.map(([id, label]) => <a key={id} href={`#${id}`}>{label}</a>)}
        </nav>
      </div>

      <section id="what" className="docs-section panel">
        <h2>What is this?</h2>
        <p className="lede">One store, five storage systems, one address space.</p>
        <p>
          The store keeps <strong>objects</strong> — files, text, JSON, images — addressed by their{" "}
          <strong>CID</strong> (content identifier): a fingerprint of the bytes, so the same content always has
          the same address, no matter which backend holds it. Every object has a permanent page at{" "}
          <code>/store/o/&lt;cid&gt;</code> you can link, share, or print as a QR code.
        </p>
        <ul>
          <li><strong>Private by default</strong> — only you can read what you store, until you share, pool, publish, or list it.</li>
          <li><strong>Gated writes</strong> — storing needs the owner's whitelist or on-chain <strong>BlocTime</strong> holdings, a signed terms-of-service acceptance, and quota headroom.</li>
          <li><strong>CID-agnostic</strong> — data living elsewhere (Arweave, another IPFS pin, S3) can be registered by reference and becomes a first-class object.</li>
          <li><strong>JSON link graph</strong> — CID strings embedded in stored JSON are auto-detected, so objects form a navigable graph.</li>
        </ul>
      </section>

      <section id="quickstart" className="docs-section panel">
        <h2>Quickstart</h2>
        <ol>
          <li><strong>Sign in</strong> with MetaMask on the <Link href="/">main page</Link> — your wallet signature is the whole login; no password, no account creation.</li>
          <li><strong>No wallet extension?</strong> <em>Continue without a wallet</em> mints a keypair inside your browser and signs with that instead — same addresses, same API. Back it up from the 🔑 button: clearing site data erases the key, and with it access to anything stored under that address.</li>
          <li><strong>Read &amp; sign the terms</strong> (once per version) when prompted.</li>
          <li><strong>Add data</strong> — pick File / Text / JSON / Image, choose a backend (or <code>both</code> to fan out), optionally tick <em>public</em> or drop it straight into a pool.</li>
          <li><strong>Open its page</strong> — every object row has an <em>open</em> link; the page shows content, metadata, market state, and the link graph.</li>
          <li><strong>No wallet on your phone?</strong> Use the phone icon in the header to mint a QR sign-in code.</li>
        </ol>
      </section>

      <section id="sharing" className="docs-section panel">
        <h2>Sharing &amp; access</h2>
        <p className="lede">Four ways to let someone else read an object, from seconds to forever.</p>
        <ul>
          <li><strong>Grants</strong> — timed read/write access for a specific 0x address (15 minutes → forever, revocable).</li>
          <li><strong>Pools</strong> — permissioned buckets with mutual access: every member reads everything pooled in; roles are owner / editor / viewer, membership can expire.</li>
          <li><strong>Tickets</strong> — single-use, seconds-lived fetch links (rendered as QR codes) that can't be replayed.</li>
          <li><strong>Publish</strong> — flip an object public so anyone with the CID can read it.</li>
        </ul>
        <p>
          Quotas cap what each address can store; the owner can raise per-user limits. The store operator can
          remove illegal content — every takedown lands in an audit log.
        </p>
      </section>

      <section id="market" className="docs-section panel">
        <h2>The market</h2>
        <p className="lede">Content-addressed drops, priced in BlocTime you hold — not spend.</p>
        <ul>
          <li><strong>Free drops</strong> — anyone signed in can grab one; grabbing a private drop mints you a permanent read grant.</li>
          <li><strong>Priced drops</strong> — set a price in <strong>BLOC</strong>; buyers must <em>hold</em> at least that much BlocTime on-chain. Their stake is the ticket — no payment moves.</li>
          <li>Listings carry a title, description, tags, likes and download counts; sort by hot / new / top, filter by tag, seller, or free-only.</li>
          <li>Every drop's QR code and title link to its object page.</li>
        </ul>
      </section>

      <section id="backends" className="docs-section panel">
        <h2>Backends</h2>
        <table className="docs-table">
          <thead>
            <tr><th>Backend</th><th>What it is</th><th>Needs a key?</th></tr>
          </thead>
          <tbody>
            <tr><td><code>localfs</code></td><td>Content-addressed files on this server's disk — IPFS-compatible CIDs, zero dependencies.</td><td>no</td></tr>
            <tr><td><code>filecoin</code></td><td>Filecoin via a Lotus daemon + gateway (orbit/filecoin module).</td><td>no (needs the daemon)</td></tr>
            <tr><td><code>hippius</code></td><td>Bittensor substrate storage network with an S3-compatible gateway.</td><td>S3 key + secret</td></tr>
            <tr><td><code>lighthouse</code></td><td>lighthouse.storage — perpetual IPFS/Filecoin pinning behind an API key.</td><td>API key</td></tr>
            <tr><td><code>external</code></td><td>Reference-only: a CID living in any other system, resolvable via its gateway URL.</td><td>no</td></tr>
          </tbody>
        </table>
        <p>
          Keys are saved off-tree on the server (<code>~/.mod/&lt;backend&gt;/</code>), never in committed
          config. The Backends tab shows per-backend readiness and lets the owner validate keys remotely.
        </p>
      </section>

      <section id="api" className="docs-section panel">
        <h2>HTTP API</h2>
        <p className="lede">
          Base URL <code>{API_BASE}</code> · interactive OpenAPI docs at{" "}
          <a href={`${API_BASE}/docs`} target="_blank" rel="noreferrer">{API_BASE}/docs ↗</a>
        </p>
        <p>
          Authenticated endpoints take a mod-protocol token — a wallet-signed proof — as{" "}
          <code>Authorization: Bearer &lt;token&gt;</code>. <em>optional</em> means public objects/listings work
          without it and private ones need it.
        </p>
        <table className="docs-table">
          <thead>
            <tr><th>Endpoint</th><th>Methods</th><th>Auth</th><th>What it does</th></tr>
          </thead>
          <tbody>
            {ENDPOINTS.map((e) => (
              <tr key={e.path}>
                <td className="mono">{e.path}</td>
                <td><MethodBadges m={e.method} /></td>
                <td>{e.auth}</td>
                <td>{e.docs}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section id="mcp" className="docs-section panel">
        <h2>MCP — plug an agent in</h2>
        <p className="lede">
          The store is an MCP server: <code>{API_BASE}/mcp</code> (Streamable HTTP, JSON-RPC 2.0).
        </p>
        <p>
          Any MCP-capable client (Claude Code, Claude Desktop, or your own agent) can browse the market, store
          data, search semantically, and mint share grants as tools. Public tools work anonymously; the rest
          want the same Bearer token the app uses.
        </p>
        <div className="docs-code">{`# Claude Code
claude mcp add --transport http store \\
  ${typeof window !== "undefined" ? window.location.origin : ""}${API_BASE}/mcp \\
  --header "Authorization: Bearer <your mod token>"`}</div>
        <table className="docs-table">
          <thead>
            <tr><th>Tool</th><th>What it does</th><th>Auth</th></tr>
          </thead>
          <tbody>
            {MCP_TOOLS.map(([name, docs, pub]) => (
              <tr key={name}>
                <td className="mono">{name}</td>
                <td>{docs}</td>
                <td>{pub ? "public" : "token"}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="muted" style={{ fontSize: 12.5 }}>
          Handshake: <code>initialize</code> → <code>notifications/initialized</code> →{" "}
          <code>tools/list</code> / <code>tools/call</code>. Protocol versions 2024-11-05 through 2025-06-18.
        </p>
      </section>
    </div>
  );
}
