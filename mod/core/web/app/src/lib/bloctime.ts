// Client for the bloctime module's API — the BlocTime protocol itself:
// time-weighted staking on Base Sepolia (lock NAT for N blocks, mint BLOC at a
// multiplier that grows with the lock).
//
// Proxied through Next at {basePath}/api/bloctime (see next.config.mjs) for
// the same reason the chain hub is: the module isn't publicly routed, and a
// domain-root path would never reach this app through the Caddy gateway.
//
// Reads go through here (the module holds the RPC + ABI). WRITES do not —
// they're signed by the visitor's own wallet in lib/wallet.tsx, because the
// server has no key for them and shouldn't.

export const BLOCTIME_API = `${process.env.NEXT_PUBLIC_BASE_PATH ?? ""}/api/bloctime`;

export type ContractInfo = {
  name: string;
  address: string;
  abi: unknown[];
  source?: string;
};

export type Contracts = {
  network: string;
  chainId: string;
  rpc: string;
  signer: string;
  contracts: ContractInfo[];
};

export type Position = {
  stakeId: number;
  /** wei, as a string */
  amount: string;
  startBlock: number;
  lockBlocks: number;
  blocTimeBalance: string;
  blocksRemaining: number;
};

export type Overview = {
  address: string;
  stakeCount: number;
  totalStaked: string;
  totalBlocTime: string;
  delegate: string;
  pendingRewards: string;
  votingPower: string;
  blocBalance: string;
  positions: Position[];
};

export type ProtocolStats = {
  totalBlocTime: string;
  totalSupply: string;
  totalStakes: number;
  address: string;
  nativeToken: string;
  network: string;
  explorer: string;
  currentEpoch?: number;
  pot?: unknown;
};

export type Params = {
  maxLockBlocks: number;
  distributionPercentage: number;
};

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BLOCTIME_API}${path}`, { cache: "no-store" });
  const json = await res.json().catch(() => null);
  if (!res.ok) throw new Error(detail(json) || `${path} → ${res.status}`);
  return (json?.result ?? json) as T;
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${BLOCTIME_API}${path}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
    cache: "no-store",
  });
  const json = await res.json().catch(() => null);
  if (!res.ok) throw new Error(detail(json) || `${path} → ${res.status}`);
  return (json?.result ?? json) as T;
}

/** FastAPI puts errors in `detail`, sometimes as a validation array. */
function detail(json: unknown): string {
  const d = (json as { detail?: unknown })?.detail;
  if (typeof d === "string") return d;
  if (Array.isArray(d)) return d.map((e) => (e as { msg?: string })?.msg).filter(Boolean).join("; ");
  return "";
}

export const bloctime = {
  contracts: () => get<Contracts>("/contracts"),
  params: () => get<Params>("/params"),
  stats: () => get<ProtocolStats>("/stats"),
  overview: (address: string) => post<Overview>("/overview", { address }),
  /** Lock multiplier in basis points (10000 = 1×) for a lock of N blocks. */
  multiplier: (blockCount: number) =>
    post<{ blockCount: number; multiplier: number; multiplierX: number }>(
      "/get_multiplier",
      { block_count: Math.max(0, Math.floor(blockCount)) },
    ),
  /** Any view function on a deployed contract (used for NAT balance/allowance). */
  read: (contract: string, fn: string, args: unknown[] = []) =>
    post<{ fn: string; output: string; outputs: { name: string; type: string }[] }>(
      "/contract/read",
      { contract, fn, args },
    ),
};

/** Base blocks are 2s, so a lock in blocks reads as a duration. */
export function lockDuration(blocks: number): string {
  const secs = blocks * 2;
  if (secs < 3600) return `${Math.round(secs / 60)} min`;
  if (secs < 86400) return `${(secs / 3600).toFixed(1)} h`;
  if (secs < 86400 * 60) return `${(secs / 86400).toFixed(1)} days`;
  return `${(secs / (86400 * 30)).toFixed(1)} months`;
}

/** wei string → human number. Safe for values far beyond 2^53. */
export function fromWei(wei: string | number | undefined | null): number {
  if (wei === undefined || wei === null) return 0;
  const s = String(wei);
  if (!/^\d+$/.test(s)) return Number(s) / 1e18 || 0;
  const pad = s.padStart(19, "0");
  const int = pad.slice(0, -18);
  const frac = pad.slice(-18).slice(0, 6);
  return Number(`${int}.${frac}`);
}

/** Human BLOC/NAT for display: "1,625", "0.0042", "—" for nothing. */
export function fmt(wei: string | number | undefined | null, digits = 2): string {
  const v = typeof wei === "number" && Math.abs(wei) < 1e15 ? wei : fromWei(wei);
  if (!v) return "0";
  if (Math.abs(v) < 0.0001) return "<0.0001";
  if (Math.abs(v) < 1) return v.toFixed(4);
  return v.toLocaleString("en-US", { maximumFractionDigits: digits });
}
