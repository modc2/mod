import { NextRequest, NextResponse } from "next/server";

export const dynamic = "force-dynamic";

const API = process.env.COMPUTE_FORK_API_URL || "http://localhost:8000";

async function rpc(fn: string, params: Record<string, any> = {}) {
  const res = await fetch(`${API}/call`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ fn: `compute/${fn}`, params, wait: true }),
    signal: AbortSignal.timeout(30000),
  });
  if (!res.ok) throw new Error(`API ${res.status}`);
  const data = await res.json();
  return data?.result ?? data;
}

/** POST /api/billing — bill one or all instances */
export async function POST(req: NextRequest) {
  try {
    const body = await req.json();
    const { action = "bill_all", name } = body;

    if (action === "bill" && name) {
      const result = await rpc("bill", { name });
      return NextResponse.json(result);
    }

    const result = await rpc("bill_all");
    return NextResponse.json(result);
  } catch (e: any) {
    return NextResponse.json({ error: e.message }, { status: 500 });
  }
}
