import { NextRequest, NextResponse } from "next/server";
import { execFile } from "child_process";
import { promisify } from "util";
import fs from "fs";
import path from "path";
import os from "os";
import { verifySession } from "@/lib/terminalAuth";

const execFileAsync = promisify(execFile);

// Per-command cap. The TERMINAL tab is one-shot exec, not a PTY, so
// long-running processes don't belong here — use the APP/API tabs or
// run them detached via `nohup ... &`. Entering a nix dev shell adds an
// evaluation step on the first run, so nix commands get a longer ceiling.
export const maxDuration = 120;
const TIMEOUT_MS = 30_000;
const NIX_TIMEOUT_MS = 90_000;
const MAX_BUFFER = 4 * 1024 * 1024; // 4MB

function expandHome(p: string): string {
  if (!p) return p;
  if (p === "~") return os.homedir();
  if (p.startsWith("~/")) return path.join(os.homedir(), p.slice(2));
  return p;
}

// Resolve a binary on PATH without spawning a subprocess (cheaper than `which`
// and avoids a shell). Returns true if found and executable.
function hasBin(name: string): boolean {
  const dirs = (process.env.PATH || "").split(path.delimiter);
  for (const d of dirs) {
    if (!d) continue;
    try {
      fs.accessSync(path.join(d, name), fs.constants.X_OK);
      return true;
    } catch {
      /* not here — keep looking */
    }
  }
  return false;
}

type NixKind = "flake" | "shell" | null;

// Which nix environment, if any, the cwd declares AND the host can enter.
// Mirrors the launcher in the Rust process backend (api/src/process.rs) so the
// terminal runs commands in the same environment a module's services launch in.
function detectNix(cwd: string): NixKind {
  try {
    if (fs.existsSync(path.join(cwd, "flake.nix")) && hasBin("nix")) return "flake";
  } catch {}
  try {
    if (fs.existsSync(path.join(cwd, "shell.nix")) && hasBin("nix-shell")) return "shell";
  } catch {}
  return null;
}

// Build the (binary, args) to run `cmd` in `cwd`, transparently entering the
// module's nix dev shell when it declares one. The user command always runs
// under `bash -lc` so shell syntax (pipes, redirects, &&) works as typed.
function buildExec(cmd: string, cwd: string, nix: NixKind): { file: string; args: string[] } {
  if (nix === "flake") {
    return { file: "nix", args: ["develop", "--command", "bash", "-lc", cmd] };
  }
  if (nix === "shell") {
    return { file: "nix-shell", args: ["--run", cmd] };
  }
  return { file: "bash", args: ["-lc", cmd] };
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

    const { cmd, cwd, nix: nixOverride } = await req.json();

    if (typeof cmd !== "string" || !cmd.trim()) {
      return NextResponse.json({ ok: false, error: "cmd required" }, { status: 400 });
    }

    const workCwd = expandHome(typeof cwd === "string" && cwd ? cwd : process.cwd());

    // Auto-enter the module's nix env when it declares one; callers can opt out
    // by passing { nix: false } (e.g. to debug the bare host shell).
    const nix: NixKind = nixOverride === false ? null : detectNix(workCwd);
    const { file, args } = buildExec(cmd, workCwd, nix);

    try {
      const { stdout, stderr } = await execFileAsync(file, args, {
        cwd: workCwd,
        timeout: nix ? NIX_TIMEOUT_MS : TIMEOUT_MS,
        maxBuffer: MAX_BUFFER,
      });
      return NextResponse.json({ ok: true, stdout, stderr, code: 0, cwd: workCwd, nix });
    } catch (e: any) {
      // execFileAsync rejects on non-zero exit; surface output + exit code rather
      // than treating it like a 500 — a failed command is a normal terminal
      // outcome, not an API error.
      return NextResponse.json({
        ok: true,
        stdout: e?.stdout || "",
        stderr: e?.stderr || e?.message || String(e),
        code: typeof e?.code === "number" ? e.code : (e?.signal ? 130 : 1),
        cwd: workCwd,
        nix,
        signal: e?.signal || null,
      });
    }
  } catch (e: any) {
    return NextResponse.json({ ok: false, error: e?.message || String(e) }, { status: 500 });
  }
}
