"use client";

// ARENA — small models playing small games, scored by a rule.
//
// The whole board rests on one idea: a game is a list of rounds, and a round
// is a prompt plus a check the answer either passes or it doesn't. No judge
// model, no rubric, no opinion — which is what makes two runs comparable, and
// what makes it possible for anyone to write a game in the sheet on the right
// without asking permission from a scoring committee.

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  deleteGame, fetchCatalog, fetchGames, fetchLeaderboard, forkGame, runMatch,
  saveGame,
} from "../lib/api";
import { useAuth } from "../context/AuthContext";
import SignIn from "../components/SignIn";
import { shortAddress } from "../lib/wallets";
import type {
  Catalog, Check, Game, GameRound, Leaderboard, MatchResult,
} from "../lib/types";

const CHECKS: { id: Check; label: string; hint: string }[] = [
  { id: "contains", label: "CONTAINS", hint: "the answer has to contain this text" },
  { id: "equals", label: "EQUALS", hint: "case and punctuation ignored, otherwise exact" },
  { id: "number", label: "NUMBER", hint: "some number in the answer equals this" },
  { id: "regex", label: "REGEX", hint: "a pattern the answer has to match" },
  { id: "lines", label: "LINES", hint: "the answer has exactly this many non-empty lines" },
  { id: "absent", label: "ABSENT", hint: "the answer must NOT contain this" },
];

const BLANK_ROUND: GameRound = { prompt: "", check: "contains", expect: "" };

export default function ArenaPage() {
  const { session } = useAuth();
  const [games, setGames] = useState<Game[]>([]);
  const [board, setBoard] = useState<Leaderboard | null>(null);
  const [cat, setCat] = useState<Catalog | null>(null);
  const [gameId, setGameId] = useState("");
  const [entrants, setEntrants] = useState<string[]>([]);
  const [runtime, setRuntime] = useState("server");
  const [results, setResults] = useState<MatchResult[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [editing, setEditing] = useState<Game | "new" | null>(null);
  const [gate, setGate] = useState(false);

  const reload = useCallback(() => {
    fetchGames().then((g) => setGames(g.games)).catch((e) => setErr(String(e)));
    fetchLeaderboard().then(setBoard).catch(() => {});
  }, []);

  useEffect(() => {
    reload();
    fetchCatalog({ limit: "500" }).then(setCat).catch(() => {});
  }, [reload]);

  useEffect(() => {
    if (!gameId && games.length) setGameId(games[0].id);
  }, [games, gameId]);

  // Only models the chosen runtime can serve, and only ones that generate —
  // an embedding model has nothing to answer a round with.
  const roster = useMemo(() => {
    const field = runtime === "browser" ? "onnx_repo" : "torch_repo";
    return (cat?.models ?? [])
      .filter((m) => m[field as keyof typeof m] && (m.kind === "text" || m.kind === "vision"))
      // Smallest first. The catalog is ordered by downloads, which puts an 8B
      // MoE at the top of a list whose runtime is twelve CPU threads.
      .sort((a, b) => (a.params_b ?? 99) - (b.params_b ?? 99))
      .map((m) => ({ id: m.id, repo: m[field as keyof typeof m] as string,
                     label: `${m.id}${m.role ? ` · ${m.role}` : ""}` }));
  }, [cat, runtime]);

  useEffect(() => {
    // Default entrants: the two smallest things that can run — a first match
    // that takes ten minutes teaches nobody anything.
    if (entrants.length || !roster.length) return;
    setEntrants(roster.slice(0, 2).map((r) => r.repo));
  }, [roster, entrants.length]);

  const game = games.find((g) => g.id === gameId) ?? null;
  const mine = (g: Game) => !g.builtin && (!g.author || g.author === session?.address);

  const play = useCallback(async () => {
    if (!session) return setGate(true);
    if (!gameId || !entrants.length) return;
    setBusy(true); setErr(null); setResults(null);
    try {
      const out = await runMatch(gameId, entrants, runtime);
      setResults(out.results);
      fetchLeaderboard().then(setBoard).catch(() => {});
    } catch (e) {
      setErr(String(e instanceof Error ? e.message : e));
    } finally {
      setBusy(false);
    }
  }, [gameId, entrants, runtime, session]);

  const onFork = useCallback(async (id: string) => {
    if (!session) return setGate(true);
    try {
      const copy = await forkGame(id);
      reload();
      setGameId(copy.id);
      setEditing(copy);
    } catch (e) { setErr(String(e instanceof Error ? e.message : e)); }
  }, [session, reload]);

  const onDelete = useCallback(async (id: string) => {
    try {
      await deleteGame(id);
      setGameId("");
      reload();
    } catch (e) { setErr(String(e instanceof Error ? e.message : e)); }
  }, [reload]);

  return (
    <div className="flex flex-col gap-2 min-h-0">
      <div className="page-head">
        <div className="page-head-band !py-2 !px-3">
          <h1 className="font-display text-sm sm:text-base whitespace-nowrap">ARENA</h1>
          <span className="font-mono text-sm text-pixel-gray-light">
            {games.length} games · {board?.runs ?? 0} runs scored
          </span>
          <div className="flex items-center gap-1.5 ml-auto">
            <button
              onClick={() => (session ? setEditing("new") : setGate(true))}
              className="pixel-btn topbar-ctl px-2.5 nav-active"
              title="write your own game"
            >
              <span className="lq-ico" aria-hidden>✚</span>
              <span className="hidden sm:inline ml-1.5">NEW GAME</span>
            </button>
          </div>
        </div>
      </div>

      {err && (
        <div className="pixel-panel pixel-panel-red p-3 font-mono text-sm text-red-400 break-words">
          {err}
        </div>
      )}

      <div className="flex flex-col lg:flex-row gap-2 min-h-0">
        {/* ── the games ── */}
        <aside className="lg:w-[320px] shrink-0 flex flex-col gap-2">
          {games.map((g) => (
            <div
              key={g.id}
              className={`pixel-panel p-2 flex flex-col gap-1 ${
                g.id === gameId ? "pixel-panel-cyan" : ""}`}
            >
              <button onClick={() => setGameId(g.id)} className="text-left flex items-center gap-2">
                <span className="stat-tile-label !text-pixel-white">{g.name}</span>
                <span className="pixel-badge ml-auto text-pixel-gray-light border-pixel-border">
                  {g.rounds.length} rounds
                </span>
              </button>
              <p className="font-mono text-xs text-pixel-gray-light leading-snug">
                {g.blurb || "—"}
              </p>
              <div className="flex gap-1 flex-wrap">
                <span className="pixel-badge text-pixel-gray border-pixel-border">
                  {g.builtin ? "BUILT-IN" : g.author ? shortAddress(g.author, 4, 3) : "LOCAL"}
                </span>
                <span className="ml-auto flex gap-1">
                  <button onClick={() => onFork(g.id)} className="pixel-btn topbar-ctl !px-2"
                          title="fork — copy it into your own list to change it">
                    <span className="lq-ico" aria-hidden>⋔</span>
                  </button>
                  {mine(g) && (
                    <>
                      <button onClick={() => setEditing(g)} className="pixel-btn topbar-ctl !px-2"
                              title="edit this game">
                        <span className="lq-ico" aria-hidden>⚒</span>
                      </button>
                      <button onClick={() => onDelete(g.id)}
                              className="pixel-btn topbar-ctl !px-2 text-red-400"
                              title="delete this game">✕</button>
                    </>
                  )}
                </span>
              </div>
            </div>
          ))}
          {!games.length && (
            <div className="pixel-panel p-3 font-mono text-sm text-pixel-gray">loading games…</div>
          )}
        </aside>

        {/* ── the match ── */}
        <section className="flex-1 min-w-0 flex flex-col gap-2">
          <div className="pixel-panel p-2 flex flex-col gap-2">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="stat-tile-label">ENTRANTS</span>
              <select
                value={runtime}
                onChange={(e) => { setRuntime(e.target.value); setEntrants([]); }}
                className="pixel-input-sm font-mono"
                aria-label="runtime"
              >
                <option value="server">SERVER</option>
                <option value="cloud">CLOUD</option>
              </select>
              <button
                onClick={play}
                disabled={busy || !entrants.length}
                className="pixel-btn topbar-ctl px-4 ml-auto nav-active"
              >
                {busy ? "PLAYING…" : session ? "▶ PLAY" : "SIGN IN TO PLAY"}
              </button>
            </div>
            <p className="font-mono text-xs text-pixel-gray-light leading-snug">
              Up to four models, every round asked fresh, temperature 0 so the
              same entrant scores the same way twice. On CPU a four-round game
              against a 350M takes a few seconds; a 1.2B takes a minute.
            </p>
            {/* Capped and scrollable: fifty entrant caps push the leaderboard
                two screens down, and the board is the point of the page. */}
            <div className="grid sm:grid-cols-2 gap-1 max-h-[240px] overflow-y-auto">
              {roster.map((r) => {
                const on = entrants.includes(r.repo);
                return (
                  <button
                    key={r.repo}
                    onClick={() => setEntrants((prev) =>
                      on ? prev.filter((p) => p !== r.repo)
                        : prev.length >= 4 ? prev : [...prev, r.repo])}
                    aria-pressed={on}
                    className={`pixel-btn topbar-ctl !justify-start truncate ${on ? "nav-active" : ""}`}
                    title={r.repo}
                  >
                    {on ? "▣" : "▢"} <span className="ml-1.5 truncate">{r.label}</span>
                  </button>
                );
              })}
              {!roster.length && (
                <p className="font-mono text-sm text-pixel-gray">no models for this runtime</p>
              )}
            </div>
          </div>

          {results && <Results results={results} />}
          {game && !results && <GameCard game={game} />}
          <BoardTable board={board} />
        </section>
      </div>

      {editing && (
        <GameEditor
          game={editing === "new" ? null : editing}
          onClose={() => setEditing(null)}
          onSaved={(g) => { setEditing(null); reload(); setGameId(g.id); }}
        />
      )}
      {gate && <SignIn onClose={() => setGate(false)} />}
    </div>
  );
}

function GameCard({ game }: { game: Game }) {
  return (
    <div className="pixel-panel p-2 flex flex-col gap-1">
      <span className="stat-tile-label">{game.name} · WHAT IT ASKS</span>
      {game.system && (
        <p className="font-mono text-xs text-pixel-gray-light">system: {game.system}</p>
      )}
      <table className="pixel-table pixel-table-auto w-full">
        <thead>
          <tr>
            <th className="text-left">#</th>
            <th className="text-left">PROMPT</th>
            <th className="text-left">PASSES IF</th>
          </tr>
        </thead>
        <tbody>
          {game.rounds.map((r, i) => (
            <tr key={i}>
              <td className="font-mono text-pixel-gray">{i + 1}</td>
              <td className="font-mono text-xs">{r.prompt}</td>
              <td className="font-mono text-xs text-pixel-gray-light whitespace-nowrap">
                {r.check} “{r.expect}”
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Results({ results }: { results: MatchResult[] }) {
  const [open, setOpen] = useState<string | null>(results[0]?.id ?? null);
  return (
    <div className="pixel-panel p-2 flex flex-col gap-2">
      <span className="stat-tile-label">RESULT · {results[0]?.game_name}</span>
      {results.map((r, place) => (
        <div key={r.id} className="flex flex-col gap-1">
          <button
            onClick={() => setOpen(open === r.id ? null : r.id)}
            className="flex items-center gap-2 text-left w-full"
          >
            <span className={`font-display text-sm ${place === 0 ? "text-amber-400" : "text-pixel-gray"}`}>
              {place + 1}
            </span>
            <span className="font-mono truncate min-w-0">{r.label}</span>
            <span className="lq-pips ml-2 shrink-0">
              {(r.rounds ?? []).map((rr, i) => (
                <span key={i} className={`lq-pip ${rr.ok ? "lq-pip-ok" : ""}`}
                      title={rr.ok ? "passed" : `expected ${rr.check} “${rr.expect}”`} />
              ))}
            </span>
            <span className="lq-score ml-auto shrink-0">
              <span className="lq-score-fill" style={{ width: `${r.score}%` }} />
              <span className="lq-score-text">{r.passed}/{r.total} · {r.score}%</span>
            </span>
            <span className="font-mono text-xs text-pixel-gray-light hidden sm:inline shrink-0">
              {r.sec_per_round}s/round
            </span>
          </button>

          {open === r.id && (
            <div className="flex flex-col gap-1 pl-4 border-l-2 border-pixel-border">
              {(r.rounds ?? []).map((rr, i) => (
                <div key={i} className="font-mono text-xs">
                  <span className={rr.ok ? "text-green-400" : "text-red-400"}>
                    {rr.ok ? "✓" : "✗"}
                  </span>{" "}
                  <span className="text-pixel-gray-light">{rr.prompt}</span>
                  <div className="pl-4 whitespace-pre-wrap break-words">
                    {rr.error ? <span className="text-red-400">{rr.error}</span>
                      : rr.answer || <span className="text-pixel-gray">(nothing)</span>}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

function BoardTable({ board }: { board: Leaderboard | null }) {
  return (
    <div className="pixel-panel overflow-x-auto">
      <table className="pixel-table pixel-table-auto w-full">
        <thead>
          <tr>
            <th className="text-left">#</th>
            <th className="text-left">MODEL</th>
            <th className="text-left">GAME</th>
            <th className="text-right">SCORE</th>
            <th className="text-right hidden sm:table-cell">S/ROUND</th>
            <th className="text-left hidden md:table-cell">WHERE</th>
          </tr>
        </thead>
        <tbody>
          {(board?.rows ?? []).map((r, i) => (
            <tr key={`${r.model}-${r.game}`}>
              <td className={`font-mono ${i === 0 ? "text-amber-400" : "text-pixel-gray"}`}>{i + 1}</td>
              <td className="font-mono truncate" title={r.model}>{r.label}</td>
              <td className="font-mono text-xs text-pixel-gray-light">{r.game_name}</td>
              <td className="text-right font-mono">{r.passed}/{r.total}</td>
              <td className="text-right font-mono hidden sm:table-cell">{r.sec_per_round}</td>
              <td className="font-mono text-xs text-pixel-gray-light hidden md:table-cell">
                {r.runtime ?? "server"}
              </td>
            </tr>
          ))}
          {!board?.rows.length && (
            <tr>
              <td colSpan={6} className="text-center text-pixel-gray py-6 font-mono">
                nothing has been played yet — pick a game and press PLAY
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

// ── the editor ──────────────────────────────────────────────────────

function GameEditor({ game, onClose, onSaved }: {
  game: Game | null;
  onClose: () => void;
  onSaved: (g: Game) => void;
}) {
  const [name, setName] = useState(game?.name ?? "");
  const [blurb, setBlurb] = useState(game?.blurb ?? "");
  const [system, setSystem] = useState(game?.system ?? "");
  const [maxTokens, setMaxTokens] = useState(game?.max_tokens ?? 96);
  const [rounds, setRounds] = useState<GameRound[]>(game?.rounds ?? [{ ...BLANK_ROUND }]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const patch = (i: number, next: Partial<GameRound>) =>
    setRounds((rs) => rs.map((r, j) => (j === i ? { ...r, ...next } : r)));

  const save = async () => {
    setBusy(true); setErr(null);
    try {
      onSaved(await saveGame({
        id: game?.id, name, blurb, system, max_tokens: maxTokens, rounds,
      }));
    } catch (e) {
      setErr(String(e instanceof Error ? e.message : e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="lq-scrim" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="lq-sheet lq-sheet-wide" role="dialog" aria-modal="true">
        <div className="lq-sheet-head">
          <h2 className="font-display text-sm">{game ? "EDIT GAME" : "NEW GAME"}</h2>
          <button onClick={onClose} className="pixel-btn topbar-ctl px-2.5 ml-auto">✕</button>
        </div>

        <div className="lq-sheet-body">
          <p className="font-mono text-sm text-pixel-gray-light leading-snug">
            Every round is asked on its own, with no history — so a round can&apos;t
            ride on the last one&apos;s luck. Write the prompt, then say what a
            passing answer looks like.
          </p>

          <div className="grid sm:grid-cols-2 gap-2">
            <label className="flex flex-col gap-1">
              <span className="stat-tile-label">NAME</span>
              <input value={name} onChange={(e) => setName(e.target.value)}
                     className="pixel-input-sm font-mono w-full" placeholder="SPELLING BEE" />
            </label>
            <label className="flex flex-col gap-1">
              <span className="stat-tile-label">MAX TOKENS PER ROUND</span>
              <input type="number" min={8} max={512} step={8} value={maxTokens}
                     onChange={(e) => setMaxTokens(Number(e.target.value))}
                     className="pixel-input-sm font-mono w-full" />
            </label>
          </div>

          <label className="flex flex-col gap-1">
            <span className="stat-tile-label">ONE LINE ON WHAT IT TESTS</span>
            <input value={blurb} onChange={(e) => setBlurb(e.target.value)}
                   className="pixel-input-sm font-mono w-full"
                   placeholder="can it hold a format under pressure?" />
          </label>

          <label className="flex flex-col gap-1">
            <span className="stat-tile-label">SYSTEM PROMPT (OPTIONAL)</span>
            <textarea value={system} onChange={(e) => setSystem(e.target.value)} rows={2}
                      className="pixel-input-sm font-mono w-full resize-none"
                      placeholder="Answer with the value only." />
          </label>

          <span className="stat-tile-label">ROUNDS</span>
          {rounds.map((r, i) => (
            <div key={i} className="pixel-panel p-2 flex flex-col gap-1.5">
              <div className="flex items-center gap-2">
                <span className="font-mono text-pixel-gray">{i + 1}</span>
                <button
                  onClick={() => setRounds((rs) => rs.filter((_, j) => j !== i))}
                  disabled={rounds.length === 1}
                  className="pixel-btn topbar-ctl !px-2 ml-auto text-red-400"
                  title="drop this round"
                >
                  ✕
                </button>
              </div>
              <textarea
                value={r.prompt}
                onChange={(e) => patch(i, { prompt: e.target.value })}
                rows={2}
                placeholder="what the model is asked"
                className="pixel-input-sm font-mono w-full resize-none"
              />
              <div className="flex gap-1.5">
                <select
                  value={r.check}
                  onChange={(e) => patch(i, { check: e.target.value as Check })}
                  className="pixel-input-sm font-mono"
                  title={CHECKS.find((c) => c.id === r.check)?.hint}
                >
                  {CHECKS.map((c) => <option key={c.id} value={c.id}>{c.label}</option>)}
                </select>
                <input
                  value={r.expect}
                  onChange={(e) => patch(i, { expect: e.target.value })}
                  placeholder={CHECKS.find((c) => c.id === r.check)?.hint}
                  className="pixel-input-sm font-mono flex-1 min-w-0"
                />
              </div>
            </div>
          ))}

          <button
            onClick={() => setRounds((rs) => [...rs, { ...BLANK_ROUND }])}
            className="pixel-btn topbar-ctl w-full"
            disabled={rounds.length >= 12}
          >
            <span className="lq-ico" aria-hidden>✚</span> ADD ROUND
          </button>

          {err && <p className="font-mono text-sm text-red-400 break-words">{err}</p>}

          <div className="grid grid-cols-2 gap-1.5">
            <button onClick={onClose} className="pixel-btn topbar-ctl w-full">CANCEL</button>
            <button onClick={save} disabled={busy || !name.trim()}
                    className="pixel-btn topbar-ctl w-full nav-active">
              {busy ? "…" : "SAVE GAME"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
