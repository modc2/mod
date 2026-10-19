// /polymarket/_api/bundler — the BUNDLER agent's switchboard.
//
//   GET                       settings + the run in flight + recent run summaries
//                             + the newest finished run in full (`latest`)
//   GET  ?id=bdl_…            one run in full
//   POST {enabled?, intervalSecs?, review?, forge?}   retune / flip the loop
//   POST ?run=1 {forge?, review?}   FORGE NOW — returns the run id at once;
//                             the run (minutes) continues in the background
//
// Owner-gated like /_api/autostrat. Nothing here touches money: bundles are
// proposals, and the copy desk is reached only through COPY ALL / pm_copy_allocate.

import { NextResponse } from "next/server";

import {
  bundlerActive, latestDone, listRuns, readSettings, triggerBundler, writeSettings,
  type BundlerSettings,
} from "../../lib/server/bundler";
import type { ForgeSettings } from "../../lib/bundleForge";
import { bearer, verifyOwnerToken } from "../../lib/server/ownerToken";
import { agentOff } from "../../lib/server/agentMods";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";
export const maxDuration = 60;

function deny() {
  return NextResponse.json({ error: "unauthorized", gate: "polymarket-access" }, { status: 401 });
}

function status() {
  const active = bundlerActive();
  return {
    settings: readSettings(),
    active: active ? { id: active.id, at: active.at, origin: active.origin, stage: active.stage, pool: active.pool } : null,
    runs: listRuns().map((r) => ({
      id: r.id, at: r.at, origin: r.origin, status: r.status, error: r.error, summary: r.summary,
      pool: r.pool, survivors: r.survivors, finishedAt: r.finishedAt,
      grades: (r.bundles ?? []).map((b) => b.grade).join(""),
    })),
    latest: latestDone(),
  };
}

export async function GET(req: Request) {
  if (!verifyOwnerToken(bearer(req))) return deny();
  const id = new URL(req.url).searchParams.get("id");
  if (id) {
    const run = listRuns().find((r) => r.id === id);
    return run ? NextResponse.json(run) : NextResponse.json({ error: "no such run" }, { status: 404 });
  }
  return NextResponse.json(status());
}

export async function POST(req: Request) {
  if (!verifyOwnerToken(bearer(req))) return deny();
  const url = new URL(req.url);
  let body: Record<string, unknown>;
  try {
    body = (await req.json()) as Record<string, unknown>;
  } catch {
    body = {};
  }
  const forge = body.forge && typeof body.forge === "object" && !Array.isArray(body.forge)
    ? (body.forge as Partial<ForgeSettings>) : undefined;

  if (url.searchParams.get("run") === "1") {
    const off = agentOff("bundler");
    if (off) return off;
    try {
      const run = triggerBundler("click", forge,
        typeof body.review === "boolean" ? body.review : undefined);
      return NextResponse.json({ started: true, id: run.id, forge: run.forge });
    } catch (e) {
      return NextResponse.json({ error: e instanceof Error ? e.message : String(e) }, { status: 409 });
    }
  }

  const patch: Partial<BundlerSettings> = {};
  if (typeof body.enabled === "boolean") patch.enabled = body.enabled;
  if (typeof body.review === "boolean") patch.review = body.review;
  if (Number.isFinite(Number(body.intervalSecs))) patch.intervalSecs = Number(body.intervalSecs);
  if (forge) patch.forge = forge as ForgeSettings;
  writeSettings(patch);
  return NextResponse.json(status());
}
