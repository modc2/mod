const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export async function api(
  fn: string,
  params: Record<string, any> = {},
  method: "GET" | "POST" = "POST"
): Promise<any> {
  const url = `${API_URL}/${fn}`;

  const res = await fetch(url, {
    method,
    headers: { "Content-Type": "application/json" },
    body: method === "POST" ? JSON.stringify(params) : undefined,
    cache: "no-store",
  });

  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`API ${res.status}: ${text}`);
  }
  const data = await res.json();
  return data?.result ?? data;
}

/** Call a mod function through the standard call proxy */
export async function call(
  fn: string,
  params: Record<string, any> = {}
): Promise<any> {
  return api("call", { fn, params, wait: true });
}
