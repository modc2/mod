import { promises as fs } from "fs";
import path from "path";
import RootClient from "./RootClient";
import { renderPaper } from "./tex";

export const dynamic = "force-dynamic";

const API = process.env.WHITEPAPER_API_URL || "http://localhost:50106";

async function getJson<T>(p: string): Promise<T | null> {
  try {
    const res = await fetch(`${API}${p}`, { cache: "no-store" });
    return res.ok ? ((await res.json()) as T) : null;
  } catch {
    return null;
  }
}

// The paper is rendered from whitepaper.tex on every request — the API serves
// it live from disk; reading the file directly is the fallback when it's down.
async function getTex(): Promise<string> {
  try {
    const res = await fetch(`${API}/paper.tex`, { cache: "no-store" });
    if (res.ok) return await res.text();
  } catch {}
  for (const p of [path.join(process.cwd(), "../../whitepaper.tex"), "/app/whitepaper.tex"]) {
    try {
      return await fs.readFile(p, "utf8");
    } catch {}
  }
  return "";
}

export default async function Page() {
  const [tex, root] = await Promise.all([
    getTex(),
    getJson<{ root: string | null; epoch: number | null; count?: number }>("/tree/root"),
  ]);
  return <RootClient paper={renderPaper(tex)} initialRoot={root} />;
}
