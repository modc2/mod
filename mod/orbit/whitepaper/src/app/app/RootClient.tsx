"use client";

import { useEffect, useState } from "react";
import ThemeToggle from "./ThemeToggle";

type RootInfo = { root: string | null; epoch: number | null; count?: number } | null;

const API = process.env.NEXT_PUBLIC_API_URL || "/api/whitepaper";

export default function RootClient({ initialRoot }: { initialRoot: RootInfo }) {
  const [root, setRoot] = useState<RootInfo>(initialRoot);
  const [refreshing, setRefreshing] = useState(false);

  async function refresh() {
    setRefreshing(true);
    try {
      const r = await fetch(`${API}/tree/root`, { cache: "no-store" });
      if (r.ok) setRoot(await r.json());
    } finally {
      setRefreshing(false);
    }
  }

  useEffect(() => {
    const t = setInterval(refresh, 15_000);
    return () => clearInterval(t);
  }, []);

  return (
    <article className="prose-paper">
      <header className="border-b border-rule pb-6 mb-8">
        <div className="flex items-start justify-between gap-4">
          <p className="text-xs uppercase tracking-widest text-modblue/80">
            MOD Protocol · Whitepaper v0.1
          </p>
          <ThemeToggle />
        </div>
        <h1>Scaling Off-Chain Open-Source Management</h1>
        <p className="text-modblue/90 -mt-1">
          Storing the tree, not the leaves: a Merkle-root registry for the MOD
          orbit ecosystem, anchored by StakeTime priority.
        </p>
        <div className="mt-6 grid grid-cols-2 gap-4 text-sm">
          <div className="border border-rule rounded p-3 bg-panel">
            <div className="text-[10px] uppercase tracking-wider text-modblue/80">
              On-chain root
            </div>
            <code className="break-all text-[12px]">
              {root?.root ?? "— (no tree built)"}
            </code>
          </div>
          <div className="border border-rule rounded p-3 bg-panel">
            <div className="text-[10px] uppercase tracking-wider text-modblue/80">
              Epoch / records
            </div>
            <code className="text-[12px]">
              {root?.epoch ?? "—"} / {root?.count ?? "—"}
            </code>
            <button
              onClick={refresh}
              disabled={refreshing}
              className="ml-3 text-[10px] underline text-modcyan"
            >
              {refreshing ? "…" : "refresh"}
            </button>
          </div>
        </div>
      </header>

      <h2>Motivation</h2>
      <p>
        The current <code>Registry.sol</code> stores each module as an independent row keyed by
        an IPFS CID. With 200+ modules already deployed and agent-driven publishing on the
        horizon, the linear per-row gas cost has become the binding constraint on ecosystem
        scale.
      </p>
      <p>
        Re-registering a module costs <strong>80,000–110,000 gas</strong> on Base today. Every
        new version is a fresh write. Off-chain indexers that want to expose the registry over
        HTTP must replay every <code>ModRegistered</code> event since genesis — a quadratic
        cost in time and disk.
      </p>

      <h2>Construction</h2>
      <p>
        We replace the per-module hash table with a single 32-byte Merkle root anchored on
        chain. The full tree — module metadata, code CIDs, owner addresses, prices, version
        history — lives entirely off chain in an IPFS-pinned manifest. Authenticity is
        preserved by Merkle inclusion proofs; updates are batched into a single root-rotation
        transaction.
      </p>
      <p>Records are serialised as canonical JSON and hashed with keccak256:</p>
      <pre>{`record = {name, owner, cid, price, version, updatedAt}
leaf(r)  = keccak256(canonical_json(r))
tree     = sorted-pair Merkle tree (OpenZeppelin-compatible)
root     = tree.root()              // 1 SSTORE on chain`}</pre>

      <h3>On-chain anchor</h3>
      <pre>{`contract TreeRegistry {
    bytes32 public root;
    uint256 public epoch;
    mapping(bytes32 => bool) public revoked;

    function rotate(bytes32 newRoot, bytes32 manifestCid) external onlyPublisher {
        root = newRoot;
        epoch++;
        emit Rotated(epoch, newRoot, manifestCid);
    }

    function verify(bytes32 leaf, bytes32[] calldata proof) external view returns (bool) {
        if (revoked[leaf]) return false;
        return MerkleProof.verifyCalldata(proof, root, leaf);
    }
}`}</pre>

      <h2>Chain anchor: StakeTime as the publisher</h2>
      <p>
        A Merkle-tree registry shifts cost off chain, but it raises a new
        question: <em>who is trusted to rotate the root?</em> We answer this
        with <strong>StakeTime</strong> — the time-weighted staking and
        validator-consensus primitive already deployed on Base.
      </p>
      <p>
        StakeTime composes five independent on-chain primitives:
      </p>
      <ul>
        <li>
          <strong>Staking</strong> — delegated, lock-weighted positions that
          mint synthetic STT proportional to{" "}
          <code>amount × multiplier(lockBlocks)</code>.
        </li>
        <li>
          <strong>Consensus</strong> — pluggable scoring layer (Yuma decay,
          linear, stake-weighted, privacy-preserving).
        </li>
        <li>
          <strong>Inflation</strong> — pluggable emission curve (halving, flat,
          linear decay, sigmoid).
        </li>
        <li>
          <strong>Subnet</strong> — emission-token-bearing partitions of the
          validator set.
        </li>
        <li>
          <strong>Registry</strong> — competitive 420-slot directory with{" "}
          <em>weakest-replacement</em> when full.
        </li>
      </ul>
      <p>
        For our module tree, the publisher role is taken by{" "}
        <strong>any validator</strong> selected by the active consensus
        adapter. Their STT-weighted score determines:
      </p>
      <ol>
        <li>
          <strong>Right to rotate</strong> — only validators whose score
          exceeds <code>minPublisherScore</code> in the current epoch can call{" "}
          <code>rotate(newRoot, manifestCid)</code>.
        </li>
        <li>
          <strong>Slashing exposure</strong> — a rotation that doesn't match
          the published manifest is provably false and triggers{" "}
          <code>slash(validatorId, amount)</code> against the rotator's stake.
        </li>
        <li>
          <strong>Revenue share</strong> — Treasury rewards for module-call
          fees are distributed proportional to staker STT, so honest
          publishers earn alongside the modules they index.
        </li>
      </ol>

      <h2>Priority mechanism</h2>
      <p>
        Within a single rotation, the publisher batches many pending updates
        into one root. We use StakeTime priority to settle two questions:{" "}
        <em>which updates land in this epoch</em>, and{" "}
        <em>which modules occupy the 420-slot registry under contention</em>.
      </p>
      <h3>Per-update priority within an epoch</h3>
      <p>
        Each pending update is signed by the module's owner and gossiped to a
        mempool. The publisher orders the mempool by:
      </p>
      <pre>{`priority(update) = bond(update) × M(lock_update) + αᵢ × age(update)`}</pre>
      <p>
        where <code>bond</code> is STT the submitter has locked behind the
        update, <code>M(lock)</code> is the same piecewise-linear lock
        multiplier StakeTime uses for stake weight (monotonic, on-chain
        enforced), and <code>α</code> is a small fairness coefficient so old
        updates aren't permanently starved. The publisher MUST honour this
        ordering: any inversion is provable from the mempool log and slashable.
      </p>
      <h3>Slot priority for the competitive registry</h3>
      <p>
        The full module set is unbounded off-chain, but the on-chain{" "}
        <em>discoverable</em> set is capped at 420 slots — the same Darwinian
        registry StakeTime uses for subnets. When a 421st module would
        register, the slot held by the lowest-priority current occupant is
        forfeited:
      </p>
      <pre>{`slot_score(mod) = Σ_users (STT_user · usage_weight(mod, user))
              ─ inactivity_penalty(blocksSinceLastCall)`}</pre>
      <ul>
        <li>
          New entries are <strong>immune</strong> from replacement for{" "}
          <code>immunityPeriod</code> blocks (default ≈ 1 day at 12 s).
        </li>
        <li>
          Registration costs a <strong>locked bond</strong> of governance
          tokens (default 1{","}000) that is forfeited on competitive
          replacement and returned on voluntary deregistration.
        </li>
        <li>
          The weakest occupant is found by{" "}
          <code>Registry.getWeakestSubnet()</code> in O(1) via a heap maintained
          on each score update.
        </li>
      </ul>
      <p>
        Modules that fall out of the on-chain set remain fully accessible{" "}
        <em>off chain</em> via the manifest — they simply lose the gas-light
        on-chain discoverability hint until they earn it back.
      </p>

      <h2>Gas analysis</h2>
      <table>
        <thead>
          <tr>
            <th>Operation</th>
            <th>Current</th>
            <th>Tree</th>
          </tr>
        </thead>
        <tbody>
          <tr><td>Register</td><td>80k–110k</td><td>~30k / N</td></tr>
          <tr><td>Update CID</td><td>30k–50k</td><td>~30k / N</td></tr>
          <tr><td>Enumerate</td><td>O(N) event replay</td><td>1 IPFS fetch</td></tr>
          <tr><td>Storage growth</td><td>O(N)</td><td>O(1) + revocations</td></tr>
        </tbody>
      </table>
      <p>
        For a batch of <code>N = 100</code> updates, the amortised per-update cost drops to
        ~300 gas — two orders of magnitude below the current floor.
      </p>

      <h2>Verifiability</h2>
      <p>
        To verify that module <code>X</code> belongs to owner <code>O</code> with CID{" "}
        <code>C</code>, a client reads the on-chain root, pulls the manifest from any IPFS
        gateway, recomputes <code>leaf(r)</code>, and checks the Merkle proof. No trust is
        placed in any single gateway — a tampered manifest will fail to verify against the
        on-chain root.
      </p>

      <h2>Reference implementation</h2>
      <p>
        This page is served by the <code>whitepaper</code> orbit module. The Merkle tree
        endpoints are backed by a Rust API (<code>axum</code> + <code>tiny-keccak</code>)
        that shares state with the Python <code>Mod</code> class via{" "}
        <code>~/.mod/whitepaper/tree.json</code>.
      </p>
      <pre>{`POST /api/whitepaper/tree/build
POST /api/whitepaper/tree/proof   { "name": "agent" }
POST /api/whitepaper/tree/verify  { "leaf": "0x..", "proof": ["0x..", ...] }
GET  /api/whitepaper/tree/root
POST /api/whitepaper/mod/call     { "fn": "agent/info" }`}</pre>

      <h2>Migration</h2>
      <ol>
        <li>Shadow mode — <code>TreeRegistry</code> deployed alongside legacy <code>Registry</code>; both serve.</li>
        <li>Indexer flip — readers cut over to the tree-based view.</li>
        <li>Write deprecation — new registrations route through the tree pool.</li>
        <li>Sunset — legacy registry is frozen, <code>setOwnerless()</code>, mirrored into genesis manifest.</li>
      </ol>

      <footer className="border-t border-rule mt-12 pt-6 text-xs text-muted flex justify-between">
        <span>MOD Protocol — whitepaper module · v0.1</span>
        <a className="underline text-modcyan" href={`${API}/paper.tex`} target="_blank" rel="noreferrer">
          download .tex
        </a>
      </footer>
    </article>
  );
}
