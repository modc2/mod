import { NextRequest, NextResponse } from "next/server";
import { exec } from "child_process";
import { promisify } from "util";
import net from "net";

const execAsync = promisify(exec);

// Allow up to 30 seconds for service operations
export const maxDuration = 30;

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
    const { stdout } = await execAsync(`lsof -ti:${port} 2>/dev/null`);
    return stdout.trim().split("\n").filter(Boolean);
  } catch {
    return [];
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
    const body = await req.json();
    const { action, type, port, workDir, moduleName } = body;

    // type: "api" | "app"
    // action: "start" | "stop" | "restart" | "kill" | "status" | "find-port"

    if (action === "find-port") {
      const rangeStart = type === "api" ? 8800 : 8850;
      const rangeEnd = type === "api" ? 8849 : 8899;
      const freePort = await findFreePort(rangeStart, rangeEnd);
      return NextResponse.json({ ok: true, port: freePort });
    }

    if (action === "status") {
      if (!port) return NextResponse.json({ ok: false, error: "port required" }, { status: 400 });
      const inUse = await isPortInUse(port);
      const pids = inUse ? await getPidsOnPort(port) : [];
      return NextResponse.json({ ok: true, running: inUse, pids, port });
    }

    if (action === "kill" || action === "stop") {
      if (!port) return NextResponse.json({ ok: false, error: "port required" }, { status: 400 });
      const pids = await getPidsOnPort(port);
      if (pids.length === 0) {
        return NextResponse.json({ ok: true, message: `Nothing running on port ${port}`, killed: [] });
      }
      for (const pid of pids) {
        try {
          await execAsync(`kill -9 ${pid} 2>/dev/null`);
        } catch { /* already dead */ }
      }
      // Wait a moment for port to free
      await new Promise((r) => setTimeout(r, 500));
      const stillUp = await isPortInUse(port);
      return NextResponse.json({ ok: !stillUp, killed: pids, port, stillRunning: stillUp });
    }

    if (action === "start" || action === "restart") {
      if (!workDir) return NextResponse.json({ ok: false, error: "workDir required" }, { status: 400 });

      // If restart, kill existing first
      if (action === "restart" && port) {
        const pids = await getPidsOnPort(port);
        for (const pid of pids) {
          try { await execAsync(`kill -9 ${pid} 2>/dev/null`); } catch {}
        }
        await new Promise((r) => setTimeout(r, 800));
      }

      // Find a free port
      const rangeStart = type === "api" ? 8800 : 3000;
      const rangeEnd = type === "api" ? 8899 : 3099;
      let targetPort: number;
      if (port && !(await isPortInUse(port))) {
        targetPort = port;
      } else {
        targetPort = await findFreePort(rangeStart, rangeEnd);
      }

      let cmd: string;
      if (type === "api") {
        // Check if this is a Rust API (has start.sh or Cargo.toml)
        const isRust = await execAsync(`test -f "${workDir}/start.sh" && echo rust || echo python`).then(r => r.stdout.trim() === "rust").catch(() => false);
        if (isRust) {
          cmd = `cd "${workDir}" && nohup bash start.sh ${targetPort} > /tmp/mod-api-${moduleName || "service"}.log 2>&1 &`;
        } else {
          // Python FastAPI module
          cmd = `cd "${workDir}" && nohup python -m uvicorn mod:app --host 0.0.0.0 --port ${targetPort} > /tmp/mod-api-${moduleName || "service"}.log 2>&1 &`;
        }
      } else {
        // Next.js app
        cmd = `cd "${workDir}" && nohup npx next dev -p ${targetPort} > /tmp/mod-app-${moduleName || "service"}.log 2>&1 &`;
      }

      try {
        await execAsync(cmd);
      } catch (e: any) {
        return NextResponse.json({ ok: false, error: e.message });
      }

      // Wait briefly for it to come up (reduced from 2s to 1s for faster response)
      await new Promise((r) => setTimeout(r, 1000));
      const running = await isPortInUse(targetPort);

      return NextResponse.json({
        ok: true,
        port: targetPort,
        running,
        log: `/tmp/mod-${type}-${moduleName || "service"}.log`,
      });
    }

    return NextResponse.json({ ok: false, error: `Unknown action: ${action}` }, { status: 400 });
  } catch (e: any) {
    return NextResponse.json({ ok: false, error: e.message }, { status: 500 });
  }
}

export async function GET(req: NextRequest) {
  const port = req.nextUrl.searchParams.get("port");
  if (!port) return NextResponse.json({ ok: false, error: "port required" }, { status: 400 });
  const p = parseInt(port, 10);
  const inUse = await isPortInUse(p);
  const pids = inUse ? await getPidsOnPort(p) : [];
  return NextResponse.json({ ok: true, running: inUse, pids, port: p });
}
