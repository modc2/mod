import { NextRequest, NextResponse } from "next/server";
import { exec } from "child_process";
import { promisify } from "util";
import path from "path";
import os from "os";
import { verifySession } from "@/lib/terminalAuth";

const execAsync = promisify(exec);

// Per-command cap. The TERMINAL tab is one-shot exec, not a PTY, so
// long-running processes don't belong here — use the APP/API tabs or
// run them detached via `nohup ... &`.
export const maxDuration = 60;
const TIMEOUT_MS = 30_000;
const MAX_BUFFER = 4 * 1024 * 1024; // 4MB

function expandHome(p: string): string {
  if (!p) return p;
  if (p === "~") return os.homedir();
  if (p.startsWith("~/")) return path.join(os.homedir(), p.slice(2));
  return p;
}

export async function POST(req: NextRequest) {
  try {
    // Owner-only: this executes arbitrary shell as the host user. Require a
    // valid session token minted by /api/terminal/auth (owner wallet signature).
    if (!verifySession(req.headers.get("x-terminal-token"))) {
      return NextResponse.json(
        { ok: false, error: "unauthorized — authorize the terminal with the owner wallet" },
        { status: 401 }
      );
    }

    const { cmd, cwd } = await req.json();

    if (typeof cmd !== "string" || !cmd.trim()) {
      return NextResponse.json({ ok: false, error: "cmd required" }, { status: 400 });
    }

    const workCwd = expandHome(typeof cwd === "string" && cwd ? cwd : process.cwd());

    try {
      const { stdout, stderr } = await execAsync(cmd, {
        cwd: workCwd,
        timeout: TIMEOUT_MS,
        maxBuffer: MAX_BUFFER,
        shell: "/bin/bash",
      });
      return NextResponse.json({ ok: true, stdout, stderr, code: 0, cwd: workCwd });
    } catch (e: any) {
      // execAsync rejects on non-zero exit; surface output + exit code rather
      // than treating it like a 500 — a failed command is a normal terminal
      // outcome, not an API error.
      return NextResponse.json({
        ok: true,
        stdout: e?.stdout || "",
        stderr: e?.stderr || e?.message || String(e),
        code: typeof e?.code === "number" ? e.code : (e?.signal ? 130 : 1),
        cwd: workCwd,
        signal: e?.signal || null,
      });
    }
  } catch (e: any) {
    return NextResponse.json({ ok: false, error: e?.message || String(e) }, { status: 500 });
  }
}
