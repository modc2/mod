"use client";

// ↻ REPLAY — re-run a finished task, with every knob it ran under exposed
// before it goes.
//
// Replay used to be a one-click "run this exact thing again", which is the
// wrong shape for how it actually gets used: a task fails on one agent and you
// want it on another, or the same ask against a different module, or the same
// prompt on a bigger model. All of that meant retyping the prompt into the
// composer by hand.
//
// So the button opens this instead — the original prompt, agent, model, module
// and system-prompt preset pre-filled, all editable, one RUN away. (Shift-click
// the button to skip the panel and replay untouched, as before.)
//
// And it lands on the SAME card. A replay used to file a second task, which
// meant the rail filled with near-identical rows and the card you actually
// pressed ↻ on sat there still showing the failed run it was replacing. Now
// the run reuses the task's id: that card goes back to RUNNING in front of
// you and carries the new result. `↻ files a NEW card` opts back out when the
// old result is worth keeping beside the new one — and a task that isn't
// yours to overwrite is pinned there, since the API refuses it anyway.
//
// What a job row can actually tell us is only prompt / model / work_dir /
// agent — agent_type and the ✦ agent's params were never persisted per job. So
// those two start from whatever the console is set to right now, which is
// exactly what instant replay silently inherited. The header says so rather
// than pretending the panel restored them.

import React, { useEffect, useMemo, useRef, useState } from "react";
import { PillSelect } from "./PillSelect";

export type ReplayDraft = {
  /** The machine preamble of an image task ("[Attached images: …]"), kept
      apart from the human ask so editing the ask doesn't drop the images. */
  promptPrefix: string;
  prompt: string;
  agent: string;
  /** claude roster id — also what the API records as the job's model. */
  model: string;
  /** codex roster id ("" = whatever ~/.codex/config.toml says). */
  codexModel: string;
  /** system-prompt preset (personality id) */
  agentType: string;
  workDir: string;
  /** ✦ orbit/agent params (provider, steps, toolbox, model, …) */
  params: Record<string, any>;
  /** Redo THIS card instead of filing a new one: the run reuses the task's
      id, so the row you pressed ↻ on is the row that goes back to RUNNING and
      then carries the new result. Off = the old behaviour, a second task. */
  inPlace: boolean;
};

type Opt = { value: string; label: string; color?: string; hint?: string; disabled?: boolean };

type Props = {
  job: { id: string; prompt: string; model: string; work_dir: string; agent?: string };
  /** Backends: claude / codex / ✦ agent. */
  agents: Array<{ value: string; label: string; icon: string; color: string; hint: string; available: boolean }>;
  models: Opt[];
  codexModels: Opt[];
  personalities: Array<{ value: string; label: string; icon?: string }>;
  /** Modules this signer may work in — name + absolute path. */
  modules: Array<{ name: string; path: string }>;
  onSearchModules?: (q: string) => void;
  /** orbit/agent's own /params schema, when it's reachable. */
  agentSchema: any | null;
  /** Load that schema. The composer only fetches it while the ✦ agent is the
      selected backend, so a replay that MOVES to the agent arrives with no
      schema — and then has no model list and no params to change, which is
      the whole reason someone opened this panel. Ask for it on the switch. */
  onNeedAgentSchema?: () => void;
  agentColor: string;
  /** Where the un-persisted fields start from: the console's live settings. */
  initial: ReplayDraft;
  /** Whether this console may overwrite this card: it has to be a real local
      row and the caller has to own it (the API refuses anything else). False
      pins the panel to "new task" and says why. */
  canRedo: boolean;
  submitting: boolean;
  subtleBorder: string;
  isLight: boolean;
  onRun: (draft: ReplayDraft) => void;
  onClose: () => void;
};

// The panel floats at 9998, so its pickers have to portal ABOVE that or their
// menus open behind the backdrop (PillSelect's default layer clears console
// chrome, not a modal).
const MENU_Z = 9999;

const LABEL = "text-[10px] font-bold uppercase shrink-0";
const LABEL_STYLE: React.CSSProperties = { color: "var(--text-tertiary)", letterSpacing: "0.08em" };

export function ReplayPanel({
  job,
  agents,
  models,
  codexModels,
  personalities,
  modules,
  onSearchModules,
  agentSchema,
  onNeedAgentSchema,
  agentColor,
  initial,
  canRedo,
  submitting,
  subtleBorder,
  isLight,
  onRun,
  onClose,
}: Props) {
  const [draft, setDraft] = useState<ReplayDraft>({ ...initial, inPlace: initial.inPlace && canRedo });
  const [keepImages, setKeepImages] = useState(true);
  const promptRef = useRef<HTMLTextAreaElement | null>(null);
  const set = <K extends keyof ReplayDraft>(k: K, v: ReplayDraft[K]) => setDraft(d => ({ ...d, [k]: v }));
  const setParam = (name: string, v: any) =>
    setDraft(d => ({ ...d, params: { ...d.params, [name]: v } }));

  useEffect(() => {
    const t = setTimeout(() => promptRef.current?.focus(), 40);
    return () => clearTimeout(t);
  }, []);

  useEffect(() => {
    if (draft.agent === "agent" && !agentSchema) onNeedAgentSchema?.();
  }, [draft.agent, agentSchema, onNeedAgentSchema]);

  // ✦ agent model lists are keyed by provider in the module's own schema, so
  // the MODEL row swaps rosters when the backend (or the provider) changes.
  const modelField = agentSchema?.fields?.find((f: any) => f.name === "model");
  const providerField = agentSchema?.fields?.find((f: any) => f.name === "provider");
  const provider: string = draft.params.provider || providerField?.default || "openrouter";
  const agentModelOptions: string[] = modelField?.options_by?.[provider] || [];
  const agentModelValue: string = draft.params.model || modelField?.default_by?.[provider] || "";

  const activeAgent = agents.find(a => a.value === draft.agent) || agents[0];
  const accent = activeAgent?.color || agentColor;

  // The one-line "what you changed" ledger under the fields. Replay's whole
  // job is running something ELSE — the diff against the original is the part
  // worth reading before you spend a run on it.
  const changes = useMemo(() => {
    const out: string[] = [];
    const wasAgent = job.agent || "claude";
    const label = (v: string) => agents.find(a => a.value === v)?.label || v;
    const modelName = (v: string, list: Opt[]) => list.find(m => m.value === v)?.label || v || "default";
    // "agent agent → claude" reads like a typo — name the destination first.
    if (draft.agent !== wasAgent) out.push(`on ${label(draft.agent)} (was ${label(wasAgent)})`);
    if (draft.agent === "claude" && draft.model !== job.model) {
      out.push(`model ${modelName(job.model, models)} → ${modelName(draft.model, models)}`);
    }
    if (draft.agent === "codex" && wasAgent === "codex" && draft.codexModel !== job.model) {
      out.push(`model ${modelName(job.model, codexModels)} → ${modelName(draft.codexModel, codexModels)}`);
    }
    if (draft.agent === "agent") {
      // The ✦ agent's model and params were never recorded on the job, so
      // there's nothing to diff them against — report only what was touched
      // HERE, and name the effective model when it was one of them.
      if ((draft.params.model ?? null) !== (initial.params.model ?? null)) {
        out.push(`model → ${agentModelValue || "module default"} (${provider})`);
      }
      const stripModel = (p: Record<string, any>) => {
        const { model: _m, ...rest } = p;
        return JSON.stringify(rest);
      };
      if (stripModel(draft.params) !== stripModel(initial.params)) out.push("agent params edited");
    }
    if ((draft.workDir || "") !== (job.work_dir || "")) {
      const name = modules.find(m => m.path === draft.workDir)?.name;
      out.push(`mod → ${name || draft.workDir || "none"}`);
    }
    const cleanOriginal = job.prompt.slice(job.prompt.length - (initial.prompt.length || 0));
    if (draft.prompt.trim() !== cleanOriginal.trim()) out.push("prompt edited");
    return out;
  }, [draft, job, agents, models, codexModels, modules, agentModelValue, provider, initial]);

  const run = () => {
    if (submitting) return;
    onRun({ ...draft, promptPrefix: keepImages ? draft.promptPrefix : "" });
  };

  const field = (label: string, node: React.ReactNode) => (
    <div className="inline-flex items-center gap-2">
      <span className={LABEL} style={LABEL_STYLE}>{label}</span>
      {node}
    </div>
  );

  return (
    <div
      style={{
        position: "fixed", inset: 0, zIndex: 9998,
        background: "rgba(0,0,0,0.55)", display: "flex",
        alignItems: "flex-start", justifyContent: "center", paddingTop: "12vh",
      }}
      onClick={onClose}
      onKeyDown={(e) => { if (e.key === "Escape") onClose(); }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        onKeyDown={(e) => {
          if (e.key === "Escape") { e.stopPropagation(); onClose(); }
          if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); run(); }
        }}
        role="dialog"
        aria-label="Replay task"
        className="flex flex-col gap-3 rounded-2xl"
        style={{
          width: 620, maxWidth: "94vw", maxHeight: "76vh", overflowY: "auto",
          padding: 18,
          background: isLight ? "#fff" : "var(--bg-secondary, #14141f)",
          border: `1px solid ${accent}40`,
          boxShadow: "0 18px 60px rgba(0,0,0,0.5)",
          fontFamily: "var(--font-mono)",
        }}
      >
        {/* Header — what is being replayed, and how it ran the first time. */}
        <div className="flex items-start justify-between gap-3">
          <div className="flex flex-col gap-1 min-w-0">
            <span className="text-[13px] font-bold uppercase" style={{ color: accent, letterSpacing: "0.08em" }}>
              ↻ Replay
            </span>
            <span className="text-[10.5px]" style={{ color: "var(--text-tertiary)" }}>
              #{job.id.slice(0, 8)} ran on {agents.find(a => a.value === (job.agent || "claude"))?.label || job.agent} ·
              {" "}{models.find(m => m.value === job.model)?.label || job.model || "—"} ·
              {" "}{modules.find(m => m.path === job.work_dir)?.name || job.work_dir || "no module"}
            </span>
          </div>
          <button
            onClick={onClose}
            className="task-action focus-ring"
            title="Close (Esc)"
            aria-label="Close replay"
          >
            ✕
          </button>
        </div>

        {/* The ask. Editable — a replay that can't be re-worded is just a
            rerun, and the composer already owns that. */}
        <div className="flex flex-col gap-1.5">
          <span className={LABEL} style={LABEL_STYLE}>PROMPT</span>
          <textarea
            ref={promptRef}
            value={draft.prompt}
            onChange={(e) => set("prompt", e.target.value)}
            rows={5}
            className="w-full text-[12.5px] font-mono px-3 py-2 rounded-xl focus-ring"
            style={{
              color: "var(--text-primary)",
              background: isLight ? "rgba(0,0,0,0.03)" : "rgba(255,255,255,0.04)",
              border: `1px solid ${subtleBorder}`,
              outline: "none", resize: "vertical", lineHeight: 1.5,
            }}
            aria-label="Replay prompt"
          />
          <div className="flex items-center gap-2 flex-wrap">
            {/* Where the result lands. A replay is nearly always "this card,
                again" — the run that already has your attention, the id in
                your URL, the row in the rail — so it redoes the card in place
                and its status goes back to RUNNING in front of you. Filing a
                SECOND card is still one click away for when the old result is
                worth keeping next to the new one. */}
            <button
              onClick={() => canRedo && set("inPlace", !draft.inPlace)}
              disabled={!canRedo}
              className="text-[10px] font-mono px-2 py-1 rounded-full focus-ring disabled:opacity-50"
              style={draft.inPlace
                ? { color: accent, border: `1px solid ${accent}66`, background: `${accent}18` }
                : { color: "var(--text-tertiary)", border: `1px solid ${subtleBorder}` }}
              title={canRedo
                ? (draft.inPlace
                    ? `Redo #${job.id.slice(0, 8)} in place — this card goes back to RUNNING and its old output is replaced`
                    : "File the replay as a second task and leave this card's result untouched")
                : "This task isn't yours to overwrite — the replay files a new card"}
              aria-pressed={draft.inPlace}
            >
              ↻ {draft.inPlace ? `redoes #${job.id.slice(0, 8)}` : "files a NEW card"}
            </button>
            {!!draft.promptPrefix && (
            <button
              onClick={() => setKeepImages(v => !v)}
              className="self-start text-[10px] font-mono px-2 py-1 rounded-full focus-ring"
              style={keepImages
                ? { color: accent, border: `1px solid ${accent}66`, background: `${accent}18` }
                : { color: "var(--text-tertiary)", border: `1px solid ${subtleBorder}` }}
              title="The original task carried screenshots — keep them attached to the replay, or run the prompt on its own"
              aria-pressed={keepImages}
            >
              ▣ images {keepImages ? "KEPT" : "DROPPED"}
            </button>
            )}
          </div>
        </div>

        {/* The knobs. Same names and same pickers as the composer's PARAMS
            row, so what you learn there applies here. */}
        <div className="flex items-center flex-wrap gap-x-5 gap-y-2.5">
          {field("AGENT", (
            <PillSelect
              menuZ={MENU_Z}
              value={draft.agent}
              options={agents.map(a => ({
                value: a.value, label: a.label, color: a.color, hint: a.hint, disabled: !a.available,
              }))}
              onChange={(v) => set("agent", v)}
              accent={accent}
              title={`Which agent runs the replay — ${activeAgent?.hint || ""}`}
              aria-label="Agent"
            />
          ))}
          {field("MODEL", draft.agent === "agent" ? (
            <PillSelect
              menuZ={MENU_Z}
              value={agentModelValue}
              options={agentModelOptions.map(m => ({ value: m, label: m, color: agentColor, hint: `${provider} model` }))}
              onChange={(v) => setParam("model", v)}
              accent={agentColor}
              placeholder={agentModelValue || "module default"}
              maxWidth={230}
              menuWidth={250}
              title={`Model: ${agentModelValue || "module default"} (${provider} via orbit/agent)`}
              aria-label="Model"
            />
          ) : draft.agent === "codex" ? (
            <PillSelect
              menuZ={MENU_Z}
              value={draft.codexModel}
              options={codexModels}
              onChange={(v) => set("codexModel", v)}
              accent={activeAgent?.color || accent}
              placeholder="codex default"
              title="Model the codex CLI runs"
              aria-label="Model"
            />
          ) : (
            <PillSelect
              menuZ={MENU_Z}
              value={draft.model}
              options={models}
              onChange={(v) => set("model", v)}
              accent={models.find(m => m.value === draft.model)?.color || accent}
              title="Model the claude harness runs"
              aria-label="Model"
            />
          ))}
          {field("MOD", (
            <PillSelect
              menuZ={MENU_Z}
              value={draft.workDir}
              options={[
                { value: "", label: "no module", hint: "run without a working directory" },
                ...modules.map(m => ({ value: m.path, label: m.name, color: "#fbbf24", hint: m.path })),
                // A task can name a directory that isn't in the registry (a
                // peers/ sandbox, a module since renamed) — keep it pickable
                // instead of silently rewriting where the replay runs.
                ...(draft.workDir && !modules.some(m => m.path === draft.workDir)
                  ? [{ value: draft.workDir, label: draft.workDir.split("/").slice(-1)[0], hint: draft.workDir }]
                  : []),
              ]}
              onChange={(v) => set("workDir", v)}
              accent="#fbbf24"
              placeholder="no module"
              maxWidth={200}
              menuWidth={280}
              searchable
              onSearch={onSearchModules}
              searchPlaceholder="search modules…"
              title={`Working directory: ${draft.workDir || "none"}`}
              aria-label="Module"
            />
          ))}
          {field("PROMPT", (
            <PillSelect
              menuZ={MENU_Z}
              value={draft.agentType}
              options={personalities.map(p => ({
                value: p.value,
                label: p.value === "default" ? "select system prompt" : `${p.icon || ""} ${p.label}`.trim(),
              }))}
              onChange={(v) => set("agentType", v)}
              accent="var(--text-secondary)"
              maxWidth={180}
              menuWidth={220}
              title="System-prompt preset sent with the replay (jobs don't record theirs — this starts from the console's current pick)"
              aria-label="System prompt"
            />
          ))}
        </div>

        {/* ✦ params, rendered from orbit/agent's own schema — the same panel
            the composer shows, so a replay can change provider/steps/toolbox
            and not just which agent runs. */}
        {draft.agent === "agent" && (
          <div className="flex flex-col gap-2 rounded-xl px-3 py-2.5" style={{ border: `1px solid ${agentColor}33`, background: `${agentColor}0d` }}>
            <span className="text-[10px] font-bold uppercase" style={{ color: agentColor, letterSpacing: "0.08em" }}>
              ✦ {agentSchema?.title || "AGENT PARAMS"}
            </span>
            {!agentSchema ? (
              <span className="text-[11px]" style={{ color: "var(--text-tertiary)" }}>
                agent module unreachable — the replay will run on its defaults
              </span>
            ) : (
              <div className="flex items-center flex-wrap gap-x-5 gap-y-2">
                {agentSchema.fields
                  .filter((f: any) => f.name !== "model")
                  .map((f: any) => {
                    const val = draft.params[f.name] ?? f.default;
                    if (f.type === "select") {
                      const opts = ((f.options || []) as any[]).map((o) =>
                        typeof o === "string"
                          ? { value: o, label: o }
                          : {
                              value: o.value === null || o.value === undefined ? "" : String(o.value),
                              label: o.icon ? `${o.icon} ${o.label}` : o.label,
                              hint: o.hint,
                            }
                      );
                      return (
                        <div key={f.name} className="inline-flex items-center gap-2">
                          <span className={LABEL} style={LABEL_STYLE}>{f.label}</span>
                          <PillSelect
              menuZ={MENU_Z}
                            value={val === null || val === undefined ? "" : String(val)}
                            options={opts}
                            onChange={(v) => {
                              setParam(f.name, v);
                              // a provider switch invalidates the picked model
                              if (f.name === "provider") setParam("model", null);
                            }}
                            accent={agentColor}
                            maxWidth={190}
                            menuWidth={240}
                            title={f.hint || f.label}
                            aria-label={f.label}
                          />
                        </div>
                      );
                    }
                    if (f.type === "number") {
                      return (
                        <div key={f.name} className="inline-flex items-center gap-2">
                          <span className={LABEL} style={LABEL_STYLE}>{f.label}</span>
                          <input
                            type="number"
                            value={val ?? ""}
                            min={f.min}
                            max={f.max}
                            step={f.step}
                            onChange={(e) => setParam(f.name, e.target.value === "" ? null : Number(e.target.value))}
                            className="w-[76px] text-[12px] font-mono px-2 py-1 rounded-full focus-ring"
                            style={{ color: agentColor, border: `1px solid ${agentColor}40`, background: "transparent" }}
                            title={f.hint || f.label}
                            aria-label={f.label}
                          />
                        </div>
                      );
                    }
                    if (f.type === "toggle") {
                      const on = !!val;
                      return (
                        <button
                          key={f.name}
                          onClick={() => setParam(f.name, !on)}
                          className="text-[11px] font-mono px-2.5 py-1 rounded-full focus-ring"
                          style={on
                            ? { color: agentColor, border: `1px solid ${agentColor}66`, background: `${agentColor}18` }
                            : { color: "var(--text-tertiary)", border: `1px solid ${subtleBorder}` }}
                          title={f.hint || f.label}
                          aria-pressed={on}
                        >
                          {f.label} {on ? "ON" : "OFF"}
                        </button>
                      );
                    }
                    return null;
                  })}
              </div>
            )}
          </div>
        )}

        {/* Footer — the diff, then the two ways out. */}
        <div className="flex items-center justify-between gap-3 flex-wrap pt-2" style={{ borderTop: `1px dashed ${subtleBorder}` }}>
          <span className="text-[10.5px] min-w-0 truncate" style={{ color: changes.length ? accent : "var(--text-tertiary)" }}>
            {changes.length ? changes.join(" · ") : "unchanged — runs exactly as it ran"}
            {draft.inPlace ? " · onto this card" : " · as a new card"}
          </span>
          <div className="flex items-center gap-2 shrink-0">
            <button
              onClick={() => { setDraft({ ...initial, inPlace: initial.inPlace && canRedo }); setKeepImages(true); }}
              disabled={submitting}
              className="task-action focus-ring disabled:opacity-40"
              title="Put every field back to how the task ran"
            >
              Reset
            </button>
            <button
              onClick={run}
              disabled={submitting || !draft.prompt.trim()}
              className="text-[11px] font-bold uppercase px-4 py-2 rounded-full focus-ring disabled:opacity-40"
              style={{ color: accent, border: `1px solid ${accent}66`, background: `${accent}1f`, letterSpacing: "0.08em" }}
              title={draft.inPlace
                ? `Redo this card on ${activeAgent?.label || draft.agent} — #${job.id.slice(0, 8)} goes back to RUNNING and its result is replaced (⌘/Ctrl + Enter)`
                : "Submit as a new task (⌘/Ctrl + Enter)"}
            >
              {submitting
                ? "RUNNING…"
                : `↻ ${draft.inPlace ? "Redo" : "Run"} on ${activeAgent?.label || draft.agent}`}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

export default ReplayPanel;
