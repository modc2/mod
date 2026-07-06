const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:50140";

export async function api(fn: string, params: Record<string, any> = {}) {
  try {
    const res = await fetch(`${API_URL}/${fn}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(params),
    });
    if (!res.ok) throw new Error(`API error: ${res.status}`);
    return await res.json();
  } catch (e: any) {
    console.error(`API call ${fn} failed:`, e);
    return { error: e.message };
  }
}

export function formatNear(yocto: string): string {
  if (!yocto) return "0";
  const n = BigInt(yocto);
  const whole = n / BigInt(10) ** BigInt(24);
  const frac = n % (BigInt(10) ** BigInt(24));
  const fracStr = frac.toString().padStart(24, "0").slice(0, 4);
  return `${whole}.${fracStr}`;
}

export function formatToken(amount: string, decimals = 18): string {
  if (!amount) return "0";
  const n = BigInt(amount);
  const divisor = BigInt(10) ** BigInt(decimals);
  const whole = n / divisor;
  const frac = n % divisor;
  const fracStr = frac.toString().padStart(decimals, "0").slice(0, 4);
  return `${whole}.${fracStr}`;
}

export function shortenKey(key: string, len = 8): string {
  if (key.length <= len * 2) return key;
  return `${key.slice(0, len)}...${key.slice(-len)}`;
}
