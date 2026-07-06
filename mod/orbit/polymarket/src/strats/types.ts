export interface StratContext {
  vaultAddress: string;
  vaultUsdc: bigint;
  vaultPositions: Map<string, unknown>;
  followedTraders: { address: string; weight: number }[];
  fetchTraderPositions: (addr: string) => Promise<unknown>;
  fetchMidPrice: () => Promise<number>;
}
