import { NextRequest, NextResponse } from "next/server";

export const dynamic = "force-dynamic";

const API = process.env.COMPUTE_FORK_API_URL || "http://localhost:8000";

async function rpc(fn: string, params: Record<string, any> = {}) {
  const res = await fetch(`${API}/call`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ fn: `compute/${fn}`, params, wait: true }),
    signal: AbortSignal.timeout(15000),
  });
  if (!res.ok) throw new Error(`API ${res.status}`);
  const data = await res.json();
  return data?.result ?? data;
}

/** GET /api/offers — list all offers */
export async function GET() {
  try {
    const offers = await rpc("offers");
    return NextResponse.json(offers);
  } catch (e: any) {
    return NextResponse.json({ error: e.message }, { status: 500 });
  }
}

/** POST /api/offers — register or remove an offer */
export async function POST(req: NextRequest) {
  try {
    const body = await req.json();
    const { action = "register", ...params } = body;

    if (action === "register") {
      const result = await rpc("register_offer", params);
      return NextResponse.json(result);
    }
    if (action === "remove") {
      const result = await rpc("remove_offer", { name: params.name });
      return NextResponse.json(result);
    }

    return NextResponse.json({ error: `Invalid action: ${action}` }, { status: 400 });
  } catch (e: any) {
    return NextResponse.json({ error: e.message }, { status: 500 });
  }
}
