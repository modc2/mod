import { NextRequest, NextResponse } from "next/server";
import { execFile } from "child_process";
import { promisify } from "util";
import net from "net";
import { isOwnerRequest, OWNER_ONLY } from "@/lib/ownerAuth";

const execFileAsync = promisify(execFile);

// Allow up to 30 seconds for service operations
export const maxDuration = 30;

// This route starts and stops processes as the host user (pm2 runs the app as
// root), so every mutating action is owner-gated and nothing user-supplied is
// ever interpolated into a shell string. `port` is parsed to an integer before
// it reaches lsof; `workDir` rides as execFile's cwd, never as shell text.

// Strict port parse — anything that isn't a plain 1..65535 integer is refused,
// so a value like "1; rm -rf /" can never reach a command line.
function parsePort(v: unknown): number | null {
  const n = typeof v === "number" ? v : typeof v === "string" ? Number(v.trim()) : NaN;
  return Number.isInteger(n) && n >= 1 && n <= 65535 ? n : null;
}

// Log filenames are ours to choose — keep the module label to safe characters.
function safeLabel(v: unknown): string {
  const s = typeof v === "string" ? v : "";
  const cleaned = s.replace(/[^A-Za-z0-9_-]/g, "").slice(0, 64);
  return cleaned || "service";
}

const badPort = () =>
  NextResponse.json({ ok: false, error: "port must be an integer 1-65535" }, { status: 400 });

function findFreePort(start: number, end: number): Promise<number> {
  return new Promise((resolve, reject) => {
    let port = start;
    const tryPort = () => {
      if (port > end) return reject(new Error("No free port found"));
      const server = net.createServer();
      server.listen(port, "127.0.0.1", () => {
        server.close(() => resolve(port));
      });
      server.on("error", () => {
        port++;
        tryPort();
      });
    };
    tryPort();
  });
}

async function getPidsOnPort(port: number): Promise<string[]> {
  try {
    const { stdout } = await execFileAsync("lsof", ["-ti", `:${port}`]);
    return stdout.trim().split("\n").filter(Boolean);
  } catch {
    return [];
  }
}

async function killPids(pids: string[]): Promise<void> {
  for (const pid of pids) {
    if (!/^\d+$/.test(pid)) continue;
    try {
      await execFileAsync("kill", ["-9", pid]);
    } catch { /* already dead */ }
  }
}

async function isPortInUse(port: number): Promise<boolean> {
  return new Promise((resolve) => {
    const server = net.createServer();
    server.listen(port, "127.0.0.1", () => {
      server.close(() => resolve(false));
    });
    server.on("error", () => resolve(true));
  });
}

export async function POST(req: NextRequest) {
  try {
    // Everything below starts, stops, or kills host processes.
    if (!isOwnerRequest(req)) {
      return NextResponse.json(OWNER_ONLY, { status: 401 });
    }

    const body = await req.json();
    const { action, type, workDir, moduleName } = body;

    // type: "api" | "app"
    // action: "start" | "stop" | "restart" | "kill" | "status" | "find-port"

    if (action === "find-port") {
      const rangeStart = type === "api" ? 8800 : 8850;
      const rangeEnd = type === "api" ? 8849 : 8899;
      const freePort = await findFreePort(rangeStart, rangeEnd);
      return NextResponse.json({ ok: true, port: freePort });
    }

    if (action === "status") {
      const port = parsePort(body.port);
      if (!port) return badPort();
      const inUse = await isPortInUse(port);
      const pids = inUse ? await getPidsOnPort(port) : [];
      return NextResponse.json({ ok: true, running: inUse, pids, port });
    }

    if (action === "kill" || action === "stop") {
      const port = parsePort(body.port);
      if (!port) return badPort();
      const pids = await getPidsOnPort(port);
      if (pids.length === 0) {
        return NextResponse.json({ ok: true, message: `Nothing running on port ${port}`, killed: [] });
      }
      await killPids(pids);
      // Wait a moment for port to free
      await new Promise((r) => setTimeout(r, 500));
      const stillUp = await isPortInUse(port);
      return NextResponse.json({ ok: !stillUp, killed: pids, port, stillRunning: stillUp });
    }

    if (action === "start" || action === "restart") {
      if (typeof workDir !== "string" || !workDir.trim()) {
        return NextResponse.json({ ok: false, error: "workDir required" }, { status: 400 });
      }
      const requested = body.port === undefined || body.port === null ? null : parsePort(body.port);
      if (body.port !== undefined && body.port !== null && !requested) return badPort();

      // If restart, kill existing first
      if (action === "restart" && requested) {
        await killPids(await getPidsOnPort(requested));
        await new Promise((r) => setTimeout(r, 800));
      }

      // Find a free port
      const rangeStart = type === "api" ? 8800 : 3000;
      const rangeEnd = type === "api" ? 8899 : 3099;
      let targetPort: number;
      if (requested && !(await isPortInUse(requested))) {
        targetPort = requested;
      } else {
        targetPort = await findFreePort(rangeStart, rangeEnd);
      }

      const label = safeLabel(moduleName);
      const log = `/tmp/mod-${type === "api" ? "api" : "app"}-${label}.log`;
      // The launcher runs with workDir as its cwd — no path is spliced into the
      // command text, so a crafted workDir can only ever be a directory name.
      let script: string;
      if (type === "api") {
        // Rust APIs ship a start.sh; Python modules run uvicorn.
        const isRust = await execFileAsync("test", ["-f", `${workDir}/start.sh`])
          .then(() => true)
          .catch(() => false);
        script = isRust
          ? `nohup bash start.sh ${targetPort} > ${log} 2>&1 &`
          : `nohup python -m uvicorn mod:app --host 0.0.0.0 --port ${targetPort} > ${log} 2>&1 &`;
      } else {
        script = `nohup npx next dev -p ${targetPort} > ${log} 2>&1 &`;
      }

      try {
        await execFileAsync("bash", ["-lc", script], { cwd: workDir });
      } catch (e: any) {
        return NextResponse.json({ ok: false, error: e.message });
      }

      // Wait briefly for it to come up (reduced from 2s to 1s for faster response)
      await new Promise((r) => setTimeout(r, 1000));
      const running = await isPortInUse(targetPort);

      return NextResponse.json({ ok: true, port: targetPort, running, log });
    }

    return NextResponse.json({ ok: false, error: `Unknown action: ${action}` }, { status: 400 });
  } catch (e: any) {
    return NextResponse.json({ ok: false, error: e.message }, { status: 500 });
  }
}

// Read-only port probe — the console shows a running/stopped dot for every
// module's app, including for signed-out visitors, so this stays open. It
// reveals only whether a local port answers; the pid list is owner-only.
export async function GET(req: NextRequest) {
  const port = parsePort(req.nextUrl.searchParams.get("port"));
  if (!port) return badPort();
  const inUse = await isPortInUse(port);
  const pids = inUse && isOwnerRequest(req) ? await getPidsOnPort(port) : [];
  return NextResponse.json({ ok: true, running: inUse, pids, port });
}
