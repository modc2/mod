'use client';

import { useEffect, useState } from 'react';
import { call } from './lib';
import { highlightLine } from './highlight';
import { inline } from './agent';
import './learn.css';

// LEARN: the stablecoin school. Lessons come from GET tutorials
// (src/tutorials.py) so the console, the MCP tools and the helper agent all
// read the same words. Finished lessons are ticked in localStorage.

type Section = { h: string; say?: string; code?: string; lang?: string; table?: string[][]; tip?: string; warn?: string };
type Lesson = {
  id: string;
  title: string;
  level: string;
  minutes: number;
  summary: string;
  template?: string;
  sections?: Section[];
  try?: { template: string; name: string; steps: string[] };
  prev?: string | null;
  next?: string | null;
};

const DONE_KEY = 'cairo.learn.done';
const LEVELS = ['start here', 'build', 'level up'];

export function Learn({
  lesson,
  setLesson,
  onNew,
  onAsk,
}: {
  lesson: string | null;
  setLesson: (id: string | null) => void;
  onNew: (template: string) => void;
  onAsk: () => void;
}) {
  const [index, setIndex] = useState<Lesson[]>([]);
  const [glossary, setGlossary] = useState<Record<string, string>>({});
  const [full, setFull] = useState<Lesson | null>(null);
  const [done, setDone] = useState<string[]>([]);

  useEffect(() => {
    call('tutorials').then((r) => {
      setIndex(r.lessons || []);
      setGlossary(r.glossary || {});
    });
    try {
      setDone(JSON.parse(localStorage.getItem(DONE_KEY) || '[]'));
    } catch {
      setDone([]);
    }
  }, []);
  useEffect(() => {
    setFull(null);
    if (lesson) call('tutorials', undefined, { id: lesson }).then((r) => (r.error ? setLesson(null) : setFull(r)));
    window.scrollTo({ top: 0 });
  }, [lesson]); // eslint-disable-line react-hooks/exhaustive-deps

  const markDone = (id: string) => {
    const next = done.includes(id) ? done : [...done, id];
    setDone(next);
    localStorage.setItem(DONE_KEY, JSON.stringify(next));
  };

  if (lesson) {
    if (!full) return <section className="card muted">Loading the lesson…</section>;
    const n = index.findIndex((l) => l.id === full.id) + 1;
    return (
      <article className="lesson">
        <div className="lessonnav">
          <button className="btn" onClick={() => setLesson(null)}>
            ← All lessons
          </button>
          <span className="spacer" />
          <span className="lstep">
            Lesson {n} of {index.length} · {full.minutes} min
          </span>
        </div>
        <header className="lessonhead">
          <span className={`lvl lvl-${LEVELS.indexOf(full.level)}`}>{full.level}</span>
          <h1>{full.title}</h1>
          <p>{full.summary}</p>
        </header>

        {full.sections?.map((s, i) => (
          <section key={i} className="lsec">
            <h2>{s.h}</h2>
            {s.say?.split(/\n\s*\n/).map((para, j) => <Para key={j} text={para} />)}
            {s.table && (
              <div className="ltable">
                <table>
                  <thead>
                    <tr>
                      {s.table[0].map((c, j) => (
                        <th key={j}>{c}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {s.table.slice(1).map((row, j) => (
                      <tr key={j}>
                        {row.map((c, k) => (
                          <td key={k}>{inline(c)}</td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {s.code && (
              <pre className="lcode">
                {s.code.split('\n').map((l, j) => (
                  <div key={j} dangerouslySetInnerHTML={{ __html: highlightLine(l) || ' ' }} />
                ))}
              </pre>
            )}
            {s.tip && <p className="callout tip">{inline(s.tip)}</p>}
            {s.warn && <p className="callout warn">{inline(s.warn)}</p>}
          </section>
        ))}

        {full.try && (
          <section className="ltry">
            <h2>Try it yourself</h2>
            <p>One click makes the contract, its tests and a simple app. Then:</p>
            <ol>
              {full.try.steps.map((s, i) => (
                <li key={i}>{inline(s)}</li>
              ))}
            </ol>
            <button className="btn primary" onClick={() => onNew(full.try!.template)}>
              Make this contract
            </button>
          </section>
        )}

        <div className="lessonfoot">
          {full.prev ? (
            <button className="btn" onClick={() => setLesson(full.prev!)}>
              ← Back
            </button>
          ) : (
            <span />
          )}
          <button className="btn" onClick={onAsk} title="the helper in the bottom-right corner">
            Stuck? Ask the helper
          </button>
          {full.next ? (
            <button
              className="btn primary"
              onClick={() => {
                markDone(full.id);
                setLesson(full.next!);
              }}
            >
              Got it, next lesson →
            </button>
          ) : (
            <button
              className="btn primary"
              onClick={() => {
                markDone(full.id);
                setLesson(null);
              }}
            >
              Finish ✓
            </button>
          )}
        </div>
      </article>
    );
  }

  const nextUp = index.find((l) => !done.includes(l.id));
  return (
    <div className="learn">
      <div className="learnhead">
        <div>
          <h1>Learn: stablecoins, from zero</h1>
          <p>
            Short lessons in plain English. No coding experience needed: you make real (practice) stablecoins with one click and play with
            them. {done.length > 0 && <b>{done.length} of {index.length} done.</b>}
          </p>
        </div>
        {nextUp && (
          <button className="btn primary" onClick={() => setLesson(nextUp.id)}>
            {done.length ? 'Continue' : 'Start'}: {nextUp.title}
          </button>
        )}
      </div>

      {LEVELS.map((lvl, li) => {
        const rows = index.filter((l) => l.level === lvl);
        if (!rows.length) return null;
        return (
          <section key={lvl} className="ltrack">
            <h2>
              <span className={`lvl lvl-${li}`}>{lvl}</span>
            </h2>
            <div className="lgrid">
              {rows.map((l) => {
                const n = index.findIndex((x) => x.id === l.id) + 1;
                const ok = done.includes(l.id);
                return (
                  <button key={l.id} className={`lcard ${ok ? 'done' : ''}`} onClick={() => setLesson(l.id)}>
                    <span className="lnum">{ok ? '✓' : n}</span>
                    <span className="lbody">
                      <b>{l.title}</b>
                      <span>{l.summary}</span>
                      <i>
                        {l.minutes} min{l.template ? ' · makes a contract' : ''}
                      </i>
                    </span>
                  </button>
                );
              })}
            </div>
          </section>
        );
      })}

      <section className="ltrack">
        <h2>
          <span className="lvl">words, explained</span>
        </h2>
        <dl className="gloss">
          {Object.entries(glossary).map(([k, v]) => (
            <div key={k}>
              <dt>{k}</dt>
              <dd>{v}</dd>
            </div>
          ))}
        </dl>
      </section>
    </div>
  );
}

function Para({ text }: { text: string }) {
  const lines = text.split('\n');
  if (lines.length > 1 && lines.every((l) => /^\d+\. /.test(l)))
    return (
      <ol className="lol">
        {lines.map((l, i) => (
          <li key={i}>{inline(l.replace(/^\d+\. /, ''))}</li>
        ))}
      </ol>
    );
  return <p>{inline(text)}</p>;
}
