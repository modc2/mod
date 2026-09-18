"use client";

// The ASK row of the SCORE editor — describe the trader you want on top in
// words, and an agent writes the score for you. The answer lands in the same
// editor box you could have typed into: it compiles in the browser through
// the normal path, so a bad formula shows ERR right there and never ranks
// anything. The agent proposes text; the editor is still the judge.

import { useCallback, useState } from "react";

import { getAccessToken } from "../lib/access";

const ASK_API = "/polymarket/api/score-agent";

interface Props {
  formula: string;
  setFormula: (f: string) => void;
  days: number;
}

export default function ScoreAsk({ formula, setFormula, days }: Props) {
  const [ask, setAsk] = useState("");
  const [busy, setBusy] = useState(false);
  const [reply, setReply] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const send = useCallback(async () => {
    const text = ask.trim();
    if (!text || busy) return;
    setBusy(true);
    setError(null);
    setReply(null);
    try {
      const token = getAccessToken();
      const res = await fetch(ASK_API, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({ ask: text, formula, days }),
      });
      const data = await res.json() as { reply?: string; formula?: string; error?: string };
      if (!res.ok) {
        setError(data.error || `the agent didn't answer (${res.status})`);
        return;
      }
      if (data.formula) setFormula(data.formula);
      setReply(data.reply || (data.formula ? "Done — the score is in the box." : "No score came back."));
      setAsk("");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [ask, busy, formula, days, setFormula]);

  return (
    <div className="space-y-1.5">
      <div className="flex items-center gap-2">
        <span className="text-[11px] text-pixel-gray tracking-wider shrink-0 min-w-[72px]" title="Describe the score in words — an agent writes the formula or function and drops it in the box above.">
          &#10022; ASK
        </span>
        <input
          type="text"
          value={ask}
          onChange={(e) => setAsk(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") void send(); }}
          disabled={busy}
          spellCheck={false}
          placeholder='describe it — "consistent winners with real volume, hide anyone under $500 traded"'
          className="pixel-input-sm flex-1 min-w-[160px] disabled:opacity-50"
        />
        <button
          onClick={() => void send()}
          disabled={busy || !ask.trim()}
          title="Have the agent write this score — it lands in the editor, nothing ranks until it compiles"
          className={`pixel-btn text-[11px] px-2.5 py-1 shrink-0 transition-colors ${
            busy
              ? "border-amber-400/60 text-amber-400 animate-pulse"
              : ask.trim()
              ? "border-green-400/70 text-green-400 hover:bg-green-400/10"
              : "border-pixel-border text-pixel-gray"
          }`}
        >
          {busy ? "WRITING…" : "WRITE IT"}
        </button>
      </div>
      {error && (
        <div className="text-[11px] text-red-400 leading-snug">{error}</div>
      )}
      {reply && !error && (
        <div className="text-[11px] text-pixel-gray-light leading-snug">
          <span className="text-green-400/80">&#10022;</span> {reply}
        </div>
      )}
    </div>
  );
}
