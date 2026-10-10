'use client';
/* A subnet's public source, spelled out: the actual git URL (not just a
 * "GitHub" pill), a copyable `git clone`, or a plain "none on chain". */
import { useCopy } from '@/lib/hooks';

/* on-chain identity fields are free text: "github.com/a/b", "https://…/b.git", "a/b" */
export function gitUrl(raw?: string | null): { url: string; label: string; clone: string | null } | null {
  let s = (raw || '').trim();
  if (!s) return null;
  if (/^[\w.-]+\/[\w.-]+$/.test(s)) s = 'github.com/' + s;
  if (!/^https?:\/\//i.test(s)) s = 'https://' + s.replace(/^\/+/, '');
  let u: URL;
  try { u = new URL(s); } catch { return null; }
  const parts = u.pathname.split('/').filter(Boolean);
  const path = parts.slice(0, 2).join('/').replace(/\.git$/, '');
  const url = `https://${u.host}/${parts.length >= 2 ? path : parts.join('/')}`.replace(/\/$/, '');
  return {
    url, label: url.replace(/^https:\/\/(www\.)?/, ''),
    clone: parts.length >= 2 ? `git clone ${url}.git` : null,      // an org page has nothing to clone
  };
}

export default function RepoLine({ github }: { github?: string | null }) {
  const g = gitUrl(github);
  const [done, copy] = useCopy();
  return (
    <div className="repo-line">
      <span className="repo-k">Source</span>
      {g ? (
        <>
          <a className="repo-url" href={g.url} target="_blank" rel="noopener noreferrer">{g.label}</a>
          <button className="repo-copy" onClick={() => copy(g.clone || g.url)} title={g.clone || g.url}>
            {done ? 'copied' : g.clone ? 'copy git clone' : 'copy url'}
          </button>
        </>
      ) : <span className="muted">no public repo in this subnet's on-chain identity</span>}
    </div>
  );
}
