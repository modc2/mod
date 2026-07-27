// Client for the chain module's hub API, proxied through Next at
// {basePath}/api/chain (see next.config.mjs). Drives on-chain registration,
// the $1 MOD mint, the reward pool and per-mod BlocTime staking — all signed
// server-side by a named key, on the configured network. The chain hub isn't
// publicly routed, so every call goes via Next; the basePath prefix is what
// lets the Caddy gateway's /web/* route carry these calls in prod.

export const CHAIN_API = `${process.env.NEXT_PUBLIC_BASE_PATH ?? ""}/api/chain`;

export type Pool = {
  governance_token: string;
  owner_percentage_bps: number;
  distributable_bps: number;
  total_bloctime: number;
  pool_usd: number;
  tokens: { address: string; symbol: string; balance: number; decimals: number; human: number }[];
};

export type Claimable = {
  address: string;
  bloctime: number;
  claimable_usd: number;
  tokens: { address: string; symbol: string; amount: number; decimals: number; human: number }[];
};

export type RegisterResult =
  | {
      status: "payment_required";
      address: string;
      bloctime: number;
      price_usd: number;
      payment_token: string;
      reason: string;
    }
  | {
      status: "registered";
      name: string;
      address: string;
      bloctime: number;
      paid: boolean;
      mint: unknown;
      register: unknown;
    };

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${CHAIN_API}${path}`, { cache: "no-store" });
  if (!res.ok) throw new Error((await res.json().catch(() => null))?.detail || `${path} → ${res.status}`);
  return res.json() as Promise<T>;
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${CHAIN_API}${path}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
    cache: "no-store",
  });
  const json = await res.json().catch(() => null);
  if (!res.ok) throw new Error(json?.detail || `${path} → ${res.status}`);
  return json as T;
}

/** Per-mod BlocTime staking. Amounts are wei (18 decimals) — divide by 1e18. */
export type ModStakesAll = {
  network: string;
  mods: Record<string, { total: number; stakers: number }>;
  total: number;
};

export type ModStakeInfo = {
  name: string;
  network: string;
  total: number;
  stakers: { address: string; amount: number }[];
  /** Present when the query carried an address/key. */
  address?: string;
  my_stake?: number;
  bloctime?: number;
  available?: number;
};

export type ModStakeResult = {
  name: string;
  address: string;
  my_stake: number;
  total: number;
  bloctime: number;
  available: number;
};

/** Wei → human BLOC string, trimmed ("12.5", "0.01", "3,250"). */
export function bloc(wei: number | undefined | null, digits = 2): string {
  const v = (wei ?? 0) / 1e18;
  if (v !== 0 && Math.abs(v) < 0.01) return v.toFixed(4);
  return v.toLocaleString("en-US", { maximumFractionDigits: digits });
}

export const chain = {
  bloctime: (address?: string) =>
    get<{ address: string; bloctime: number; is_owner: boolean }>(
      `/bloctime/owner${address ? `?address=${encodeURIComponent(address)}` : ""}`,
    ),
  pool: () => get<Pool>("/pool"),
  claimable: (address: string) =>
    get<Claimable>(`/pool/claimable?address=${encodeURIComponent(address)}`),
  register: (body: {
    name: string;
    data: string;
    key?: string;
    pay?: boolean;
    payment_token?: string;
  }) => post<RegisterResult>("/register", body),
  claim: (body: { token?: string; key?: string }) =>
    post<{ address: string; claims: unknown[] }>("/pool/claim", body),

  /** BLOC staked per module across the whole catalog. */
  modStakes: () => get<ModStakesAll>("/mods/stakes"),
  /** One module's stake book; pass the viewer's key/address for their position. */
  modStakeInfo: (name: string, who?: string) =>
    get<ModStakeInfo>(
      `/mods/stakes/${encodeURIComponent(name)}${
        who
          ? `?${who.startsWith("0x") ? "address" : "key"}=${encodeURIComponent(who)}`
          : ""
      }`,
    ),
  stakeMod: (body: { name: string; amount: number; key?: string }) =>
    post<ModStakeResult>("/mods/stake", body),
  unstakeMod: (body: { name: string; amount?: number; key?: string }) =>
    post<ModStakeResult>("/mods/unstake", body),
};
