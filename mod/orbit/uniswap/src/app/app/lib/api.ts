import { Trader, ScrapeProgress } from "./types";

const API_BASE = "/api";

interface StreamResult {
  traders: Trader[];
  source: "cache" | "fresh";
}

/**
 * Fetch traders via NDJSON streaming endpoint.
 * Calls onProgress for each progress event, onPartial for intermediate results.
 */
export async function fetchTradersStream(
  params: { chain: string; days: number; pool?: number; min_swaps?: number },
  onProgress: (p: ScrapeProgress) => void,
  onPartial?: (traders: Trader[]) => void
): Promise<StreamResult> {
  const qs = new URLSearchParams({
    chain: params.chain,
    days: String(params.days),
    pool: String(params.pool || 2000),
    min_swaps: String(params.min_swaps || 5),
  });

  const res = await fetch(`${API_BASE}/traders/stream?${qs}`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  if (!res.body) throw new Error("No response body");

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  let finalResult: StreamResult = { traders: [], source: "fresh" };

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buf += decoder.decode(value, { stream: true });
    let nl: number;

    while ((nl = buf.indexOf("\n")) !== -1) {
      const line = buf.slice(0, nl).trim();
      buf = buf.slice(nl + 1);
      if (!line) continue;

      try {
        const evt = JSON.parse(line);

        if (evt.type === "progress") {
          onProgress(evt as ScrapeProgress);
        } else if (evt.type === "partial") {
          onPartial?.(evt.traders as Trader[]);
        } else if (evt.type === "result") {
          finalResult = { traders: evt.traders, source: evt.source };
        } else if (evt.type === "error") {
          throw new Error(evt.message);
        }
      } catch (e) {
        if (e instanceof SyntaxError) continue;
        throw e;
      }
    }
  }

  return finalResult;
}

/**
 * Fetch traders from cache (non-streaming).
 */
export async function fetchTraders(params: {
  chain: string;
  days: number;
  limit?: number;
  sort?: string;
}): Promise<{ traders: Trader[]; total: number }> {
  const qs = new URLSearchParams({
    chain: params.chain,
    days: String(params.days),
    limit: String(params.limit || 50),
    sort: params.sort || "score",
  });

  const res = await fetch(`${API_BASE}/traders?${qs}`);
  return res.json();
}

/**
 * Fetch single trader profile.
 */
export async function fetchTrader(
  address: string,
  chain: string,
  days: number = 30
): Promise<{ trader: Trader } | { error: string }> {
  const qs = new URLSearchParams({ chain, days: String(days) });
  const res = await fetch(`${API_BASE}/traders/${address}?${qs}`);
  return res.json();
}
