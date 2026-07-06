// Typed client for the Venice gateway. All calls go through /api/venice/*,
// proxied to the Rust backend (see next.config.mjs).

const BASE = process.env.NEXT_PUBLIC_API_URL || "/api/venice";

function authHeaders(token: string | null): Record<string, string> {
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${res.status}: ${text}`);
  }
  return res.json() as Promise<T>;
}

export interface MeResponse {
  address: string;
  has_key: boolean;
  paid_available: boolean;
  price: string;
  currency: string;
  network: string;
}

export interface VeniceModel {
  id: string;
  type?: string;
  model_spec?: {
    capabilities?: { supportsFunctionCalling?: boolean; supportsVision?: boolean };
    availableContextTokens?: number;
  };
}

export interface MediaOut {
  media_id: string;
  kind: "image" | "video";
  mime: string;
  url: string; // "/media/<id>"
  tool?: string;
  prompt?: string;
}

export interface AgentHandlers {
  onStatus?: (text: string) => void;
  onMedia?: (m: MediaOut) => void;
  onMessage?: (text: string) => void;
  onError?: (err: string) => void;
  onDone?: (media: MediaOut[]) => void;
}

// Absolute URL for a stored media item (for <img>/<video> src).
export function mediaUrl(url: string): string {
  return `${BASE}${url}`;
}

export const api = {
  async health() {
    return json<{ ok: boolean; service: string }>(await fetch(`${BASE}/health`));
  },
  async models() {
    const r = await json<{ data: VeniceModel[] }>(await fetch(`${BASE}/models`));
    return r.data || [];
  },
  async me(token: string) {
    return json<MeResponse>(await fetch(`${BASE}/me`, { headers: authHeaders(token) }));
  },
  async setKey(token: string, key: string) {
    return json<{ ok: boolean; has_key: boolean }>(
      await fetch(`${BASE}/key`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...authHeaders(token) },
        body: JSON.stringify({ key }),
      })
    );
  },
  async rmKey(token: string) {
    return json<{ ok: boolean; has_key: boolean }>(
      await fetch(`${BASE}/key`, { method: "DELETE", headers: authHeaders(token) })
    );
  },
  // Upload an attachment (data URL or base64) → returns its media_id.
  async uploadMedia(token: string, dataUrl: string) {
    return json<MediaOut>(
      await fetch(`${BASE}/media`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...authHeaders(token) },
        body: JSON.stringify({ data: dataUrl }),
      })
    );
  },
  /**
   * Run one multimodal agent turn, consuming the SSE event stream.
   * `fetchImpl` lets the paid path inject an x402-wrapped fetch.
   */
  async agent(
    token: string,
    body: { model: string; messages: { role: string; content: string }[]; attachments?: string[] },
    handlers: AgentHandlers,
    fetchImpl: typeof fetch = fetch
  ): Promise<void> {
    const res = await fetchImpl(`${BASE}/agent`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders(token) },
      body: JSON.stringify(body),
    });
    if (!res.ok || !res.body) {
      const text = await res.text().catch(() => res.statusText);
      throw new Error(`${res.status}: ${text}`);
    }
    await consumeSSE(res.body, (event, data) => {
      let parsed: Record<string, unknown> = {};
      try {
        parsed = JSON.parse(data);
      } catch {
        /* ignore */
      }
      switch (event) {
        case "status":
          handlers.onStatus?.(String(parsed.text ?? ""));
          break;
        case "media":
          handlers.onMedia?.(parsed as unknown as MediaOut);
          break;
        case "message":
          handlers.onMessage?.(String(parsed.text ?? ""));
          break;
        case "error":
          handlers.onError?.(String(parsed.error ?? "error"));
          break;
        case "done":
          handlers.onDone?.((parsed.media as MediaOut[]) || []);
          break;
      }
    });
  },
};

// Minimal SSE parser over a ReadableStream of the response body.
async function consumeSSE(
  body: ReadableStream<Uint8Array>,
  onEvent: (event: string, data: string) => void
) {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    // Events are separated by a blank line.
    let idx;
    while ((idx = buf.indexOf("\n\n")) !== -1) {
      const chunk = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      let event = "message";
      const dataLines: string[] = [];
      for (const line of chunk.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
      }
      if (dataLines.length) onEvent(event, dataLines.join("\n"));
    }
  }
}
