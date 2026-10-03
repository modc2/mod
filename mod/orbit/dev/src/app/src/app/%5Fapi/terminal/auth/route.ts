import { NextRequest, NextResponse } from "next/server";
import { authorize } from "@/lib/terminalAuth";

// Verifies an owner wallet signature and issues a terminal session token.
// Public endpoint, but it only mints a token for a valid signature from the
// configured owner — non-owners get 401.
export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(req: NextRequest) {
  let body: any;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ ok: false, error: "invalid JSON body" }, { status: 400 });
  }
  const { address, ts, signature } = body || {};
  if (typeof address !== "string" || typeof signature !== "string" || ts == null) {
    return NextResponse.json(
      { ok: false, error: "address, ts, signature required" },
      { status: 400 }
    );
  }
  const res = authorize(address, Number(ts), signature);
  return NextResponse.json(res, { status: res.ok ? 200 : 401 });
}
