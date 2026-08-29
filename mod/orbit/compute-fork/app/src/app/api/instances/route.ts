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

/** GET  /api/instances          — list all instances */
/** GET  /api/instances?name=x   — cost summary for one */
export async function GET(req: NextRequest) {
  try {
    const name = req.nextUrl.searchParams.get("name");
    if (name) {
      const cost = await rpc("cost", { name });
      return NextResponse.json(cost);
    }
    const instances = await rpc("ps");
    return NextResponse.json(instances);
  } catch (e: any) {
    return NextResponse.json({ error: e.message }, { status: 500 });
  }
}

/** POST /api/instances — create / rent / stop / start / destroy / release / ssh */
export async function POST(req: NextRequest) {
  try {
    const body = await req.json();
    const { action = "rent", ...params } = body;

    const allowed = [
      "create",
      "rent",
      "release",
      "destroy",
      "stop",
      "start",
      "restart",
      "ssh",
      "bill",
    ];
    if (!allowed.includes(action)) {
      return NextResponse.json({ error: `Invalid action: ${action}` }, { status: 400 });
    }

    const result = await rpc(action, params);
    return NextResponse.json(result);
  } catch (e: any) {
    return NextResponse.json({ error: e.message }, { status: 500 });
  }
}
