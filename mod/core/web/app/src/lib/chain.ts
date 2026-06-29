// Client for the chain module's hub API, proxied through Next at /api/chain
// (see next.config.mjs). Drives on-chain registration, the $1 MOD mint, and the
// reward pool — all signed server-side by a named key, on the configured
// network. The chain hub isn't publicly routed, so every call goes via Next.

export const CHAIN_API = "/api/chain";

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
};
