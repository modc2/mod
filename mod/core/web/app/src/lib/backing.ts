// Per-module BlocTime backing — "how much BLOC stands behind this module".
//
// Served by the Rust mod-api (api/src/staking.rs), which merges two ledgers:
// the chain module's key-signed one and the wallet-signed one it owns itself.
// Writes are a `personal_sign` over the exact message `backingMessage` builds
// — the same string the server rebuilds and recovers the signer from, so what
// the wallet shows the visitor is precisely what is enforced.

import { API } from "./api";

export type ModTotal = { total: number; stakers: number; wallet: string };

export type BookTotals = {
  network: string;
  mods: Record<string, ModTotal>;
  total: number;
  chain_available: boolean;
};

export type Staker = {
  address: string;
  amount: string;
  bloc: number;
  via: "wallet" | "key";
};

export type ModBacking = {
  name: string;
  network: string;
  total: string;
  total_bloc: number;
  stakers: Staker[];
  address?: string;
  my_stake?: string;
  my_stake_bloc?: number;
  bloc_balance?: string;
  bloc_balance_bloc?: number;
  available?: string;
  available_bloc?: number;
  balance_available: boolean;
};

export type BackResult = {
  name: string;
  address: string;
  action: string;
  my_stake: string;
  my_stake_bloc: number;
  total: string;
  total_bloc: number;
  bloc_balance: string;
  available: string;
  available_bloc: number;
};

/** Byte-for-byte identical to `backing_message` in api/src/staking.rs. */
export function backingMessage(
  action: "stake" | "unstake",
  module: string,
  amount: string,
  network: string,
  address: string,
  time: number,
): string {
  return [
    "mod protocol · back a module",
    `action: ${action}`,
    `module: ${module}`,
    `amount: ${amount} BLOC`,
    `network: ${network}`,
    `address: ${address.toLowerCase()}`,
    `time: ${time}`,
  ].join("\n");
}

async function json<T>(res: Response, what: string): Promise<T> {
  const body = await res.json().catch(() => null);
  if (!res.ok) {
    throw new Error((body as { error?: string })?.error || `${what} → ${res.status}`);
  }
  return body as T;
}

export const backing = {
  totals: async () =>
    json<BookTotals>(await fetch(`${API}/mods/stakes`, { cache: "no-store" }), "stakes"),

  module: async (name: string, address?: string) =>
    json<ModBacking>(
      await fetch(
        `${API}/mods/${encodeURIComponent(name)}/stakes${
          address ? `?address=${encodeURIComponent(address)}` : ""
        }`,
        { cache: "no-store" },
      ),
      "module stakes",
    ),

  /**
   * Apply a signed stake/unstake. `sign` is the wallet's personal_sign; the
   * timestamp is part of the signed payload and expires after 15 minutes.
   */
  submit: async (
    args: {
      name: string;
      action: "stake" | "unstake";
      /** Human BLOC as typed, or "all" to withdraw a whole position. */
      amount: string;
      address: string;
      network: string;
    },
    sign: (message: string) => Promise<string>,
  ): Promise<BackResult> => {
    const time = Math.floor(Date.now() / 1000);
    const message = backingMessage(
      args.action,
      args.name.toLowerCase(),
      args.amount,
      args.network,
      args.address,
      time,
    );
    const signature = await sign(message);
    const res = await fetch(`${API}/mods/stakes`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        name: args.name.toLowerCase(),
        action: args.action,
        amount: args.amount,
        address: args.address.toLowerCase(),
        network: args.network,
        time,
        signature,
      }),
      cache: "no-store",
    });
    return json<BackResult>(res, "back module");
  },
};
