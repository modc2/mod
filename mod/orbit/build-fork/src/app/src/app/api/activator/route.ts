import { NextRequest, NextResponse } from "next/server";
import http from "http";

// Bridge to the fleet's activator (core/server/activator, :9000) — the
// scale-to-zero proxy that pm2-stops any managed module idle longer than its
// timeout and wakes it again on the next gateway request.
//
// Why the console needs this: a slept module is not a broken module. Without
// this the console can only see "nothing is listening on :3091" and shouts
// about an outage that is really the fleet working as designed. And because
// the console's own probes are port binds (not TCP connections), watching a
// module through the console looks *exactly* like nobody using it — so the
// sweep would put the very module you have open to sleep under you.
//
// Two things live here:
//   GET  → which modules are managed + the live idle timeout (read-only)
//   POST → touch one module {module, target?: "app" | "api"}: a request through
//          the activator, which stamps its last-access and wakes it if it was
//          asleep. `target` picks which port the wake waits for.
// The activator's control plane is localhost-only; this route runs on the host
// next to it, which is what makes the console able to ask at all.

const ACTIVATOR = { host: "127.0.0.1", port: parseInt(process.env.ACTIVATOR_PORT || "9000", 10) };
// A touch doesn't answer until the wake finishes, and the activator's own wake
// budget is 30s — anything shorter here reports a slow cold start as a dead
// activator. The state read gets a smaller budget, but not a tight one: the
// activator shells out to pm2 synchronously, so it goes unresponsive for a
// second or two whenever it's starting something.
const WAKE_TIMEOUT_MS = 45000;
const STATE_TIMEOUT_MS = 12000;

type Res = { status: number; body: string };

function request(path: string, method = "GET", timeout = STATE_TIMEOUT_MS): Promise<Res> {
  return new Promise((resolve, reject) => {
    const req = http.request({ ...ACTIVATOR, path, method, timeout }, (r) => {
      let body = "";
      r.on("data", (c) => (body += c));
      r.on("end", () => resolve({ status: r.statusCode || 0, body }));
    });
    req.on("timeout", () => req.destroy(new Error("activator timeout")));
    req.on("error", reject);
    req.end();
  });
}

// Module names are spliced into a proxy path — keep them to what a module dir
// can actually be called.
const safeName = (v: unknown) =>
  typeof v === "string" && /^[a-z0-9][a-z0-9_-]{0,63}$/i.test(v) ? v : null;

async function state(): Promise<any | null> {
  try {
    const r = await request("/_activator/state");
    return r.status === 200 ? JSON.parse(r.body) : null;
  } catch {
    return null; // no activator on this host — the console just treats every module as unmanaged
  }
}

export async function GET() {
  const s = await state();
  if (!s) return NextResponse.json({ ok: true, present: false, managed: [], idleSeconds: null });
  return NextResponse.json({
    ok: true,
    present: true,
    idleSeconds: s.idleSeconds ?? null,
    // Only modules that can actually be slept — pinned/disabled ones never move.
    managed: (s.modules || []).filter((m: any) => !m.pinned).map((m: any) => m.module),
    modules: (s.modules || []).map((m: any) => ({
      module: m.module, running: m.running, apiUp: m.apiUp, appUp: m.appUp,
      pinned: m.pinned, disabled: m.disabled, idleSeconds: m.idleSeconds,
    })),
  });
}

// Touch = one proxied request through the activator. That single hop is what
// stamps last-access (so the idle sweep leaves it alone) and wakes it if it
// was stopped. Deliberately not owner-gated: any visitor loading /{mod} on the
// gateway already does exactly this, so it grants nothing new.
// It is deliberately ONE hop. Bracketing it with state reads would double the
// cost and, worse, make a busy activator look like an absent one — it drives
// pm2 synchronously, so it stops answering while it starts something. The
// proxy's own reply already says everything we need: a 404 means it doesn't
// route that name, a 503 means the host disabled it or the wake failed, and
// anything else means the module is up and stamped.
export async function POST(req: NextRequest) {
  let body: any = {};
  try { body = await req.json(); } catch { /* empty body is fine */ }
  const mod = safeName(body.module);
  if (!mod) return NextResponse.json({ ok: false, error: "module required" }, { status: 400 });

  // WHICH path matters twice over: it has to be one the activator routes
  // (/api/{mod} needs an api port, which an app-only module lacks), and the
  // wake only waits on that path's port. The console asks for "app" from the
  // preview tab so this call doesn't return before the frame would load.
  const wantApp = body.target !== "api";
  let res: Res;
  try {
    res = await request(wantApp ? `/${mod}` : `/api/${mod}/health`, wantApp ? "HEAD" : "GET", WAKE_TIMEOUT_MS);
  } catch (e: any) {
    const gone = e.code === "ECONNREFUSED" || e.code === "EHOSTUNREACH";
    return NextResponse.json(
      { ok: false, error: gone ? "no activator on this host" : `activator: ${e.message}`, present: !gone },
      { status: 503 },
    );
  }
  // A 404 can just mean this module has no port on that side (app-only or
  // api-only) — the other path is still a valid stamp-and-wake.
  if (res.status === 404) {
    try {
      res = await request(wantApp ? `/api/${mod}/health` : `/${mod}`, wantApp ? "GET" : "HEAD", WAKE_TIMEOUT_MS);
    } catch { /* keep the 404 */ }
  }
  if (res.status === 404) return NextResponse.json({ ok: false, error: `${mod} has no route through the activator` }, { status: 404 });
  if (res.status === 503) return NextResponse.json({ ok: false, error: res.body.trim() || `${mod} would not wake` }, { status: 503 });
  return NextResponse.json({ ok: true, module: mod, status: res.status });
}
