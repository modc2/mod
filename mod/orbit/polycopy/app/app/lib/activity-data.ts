export interface DailyDataPoint {
  date: string;
  timestamp: number;
  uniswapVolume: number;
  uniswapTrades: number;
  polymarketVolume: number;
  polymarketTrades: number;
}

export async function fetchActivityData(chainId: number): Promise<DailyDataPoint[]> {
  const res = await fetch(`/api/activity?chainId=${chainId}`);
  if (!res.ok) throw new Error(`Activity API: ${res.status}`);
  const data: DailyDataPoint[] = await res.json();
  return data;
}
