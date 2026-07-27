// Typed client for the dev gateway. All calls go through /api/dev/*, proxied
// to the Rust backend (see next.config.mjs).

const BASE = process.env.NEXT_PUBLIC_API_URL || "/api/dev";

function authHeaders(token: string | null): Record<string, string> {
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const text = await res.text();
    let msg = text;
    try {
      msg = JSON.parse(text).error ?? text;
    } catch {
      /* keep raw */
    }
    throw new Error(`${res.status}: ${msg}`);
  }
  return res.json() as Promise<T>;
}

export interface Provider {
  name: string;
  label: string;
  base_url: string;
  default_model: string;
  icon: string;
  color: string;
  byok: boolean;
  builtin: boolean;
  has_backend: boolean;
}

export interface MeProvider {
  name: string;
  has_key: boolean;
  has_backend: boolean;
}

export interface MeResponse {
  address: string;
  is_owner: boolean;
  providers: MeProvider[];
}

export interface OpenAIModel {
  id: string;
  [k: string]: unknown;
}

export interface ChatMessage {
  role: "system" | "user" | "assistant";
  content: string;
}

export interface ChatHandlers {
  onToken?: (delta: string) => void;
  onDone?: () => void;
  onError?: (err: string) => void;
}

export const api = {
  async health() {
    return json<{ ok: boolean; service: string }>(await fetch(`${BASE}/health`));
  },

  async providers() {
    const r = await json<{ providers: Provider[] }>(await fetch(`${BASE}/providers`));
    return r.providers || [];
  },

  async addProvider(token: string, p: Partial<Provider> & { name: string; base_url: string }) {
    return json<{ ok: boolean; provider: Provider }>(
      await fetch(`${BASE}/providers`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...authHeaders(token) },
        body: JSON.stringify(p),
      })
    );
  },

  async removeProvider(token: string, name: string) {
    return json<{ ok: boolean; removed: string }>(
      await fetch(`${BASE}/providers/${encodeURIComponent(name)}`, {
        method: "DELETE",
        headers: authHeaders(token),
      })
    );
  },

  async models(token: string, provider: string) {
    const r = await json<{ data?: OpenAIModel[] }>(
      await fetch(`${BASE}/providers/${encodeURIComponent(provider)}/models`, {
        headers: authHeaders(token),
      })
    );
    return r.data || [];
  },

  async me(token: string) {
    return json<MeResponse>(await fetch(`${BASE}/me`, { headers: authHeaders(token) }));
  },

  async setKey(token: string, provider: string, key: string) {
    return json<{ ok: boolean; has_key: boolean }>(
      await fetch(`${BASE}/key/${encodeURIComponent(provider)}`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...authHeaders(token) },
        body: JSON.stringify({ key }),
      })
    );
  },

  async rmKey(token: string, provider: string) {
    return json<{ ok: boolean; has_key: boolean }>(
      await fetch(`${BASE}/key/${encodeURIComponent(provider)}`, {
        method: "DELETE",
        headers: authHeaders(token),
      })
    );
  },

  /**
   * Stream one chat completion. Sends `stream: true`, then consumes the
   * OpenAI-style SSE (`data: {choices:[{delta:{content}}]}` … `data: [DONE]`),
   * surfacing each content delta via `onToken`.
   */
  async chat(
    token: string,
    provider: string,
    body: { model?: string; messages: ChatMessage[]; temperature?: number },
    handlers: ChatHandlers
  ): Promise<void> {
    const res = await fetch(`${BASE}/chat/${encodeURIComponent(provider)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders(token) },
      body: JSON.stringify({ ...body, stream: true }),
    });
    if (!res.ok || !res.body) {
      let text = await res.text().catch(() => res.statusText);
      try {
        text = JSON.parse(text).error ?? text;
      } catch {
        /* keep raw */
      }
      handlers.onError?.(`${res.status}: ${text}`);
      return;
    }
    await consumeOpenAISSE(res.body, handlers);
  },
};

// Parse an OpenAI-compatible chat SSE stream off a ReadableStream.
async function consumeOpenAISSE(body: ReadableStream<Uint8Array>, handlers: ChatHandlers) {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let idx: number;
      // SSE events are separated by a blank line.
      while ((idx = buf.indexOf("\n\n")) !== -1) {
        const chunk = buf.slice(0, idx);
        buf = buf.slice(idx + 2);
        for (const line of chunk.split("\n")) {
          const trimmed = line.trim();
          if (!trimmed.startsWith("data:")) continue;
          const data = trimmed.slice(5).trim();
          if (data === "[DONE]") {
            handlers.onDone?.();
            return;
          }
          try {
            const parsed = JSON.parse(data);
            const delta: string | undefined = parsed?.choices?.[0]?.delta?.content;
            if (delta) handlers.onToken?.(delta);
            const err = parsed?.error;
            if (err) handlers.onError?.(typeof err === "string" ? err : err.message ?? "error");
          } catch {
            /* ignore keep-alive / non-JSON lines */
          }
        }
      }
    }
    handlers.onDone?.();
  } catch (e) {
    handlers.onError?.(e instanceof Error ? e.message : String(e));
  }
}
