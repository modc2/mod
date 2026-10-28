// Just enough markdown for agent answers: paragraphs, - and 1. lists,
// **bold**, *italic*, `code`. Builds React elements — never innerHTML.
import { Fragment } from 'react';

function inline(s: string, key: string): React.ReactNode[] {
  const out: React.ReactNode[] = [];
  const rx = /(\*\*[^*]+\*\*|`[^`]+`|\*[^*\s][^*]*\*)/g;
  let last = 0, m: RegExpExecArray | null, i = 0;
  while ((m = rx.exec(s))) {
    if (m.index > last) out.push(s.slice(last, m.index));
    const t = m[0];
    const k = `${key}-${i++}`;
    if (t.startsWith('**')) out.push(<b key={k}>{t.slice(2, -2)}</b>);
    else if (t.startsWith('`')) out.push(<code key={k}>{t.slice(1, -1)}</code>);
    else out.push(<i key={k}>{t.slice(1, -1)}</i>);
    last = m.index + t.length;
  }
  if (last < s.length) out.push(s.slice(last));
  return out;
}

export function Md({ text }: { text: string }) {
  const blocks = (text || '').replace(/\r/g, '').split(/\n{2,}/);
  return (
    <div className="md">
      {blocks.map((b, bi) => {
        const lines = b.split('\n').filter(l => l.trim());
        const ul = lines.length && lines.every(l => /^\s*[-*] /.test(l));
        const ol = lines.length && lines.every(l => /^\s*\d+\. /.test(l));
        if (ul || ol) {
          const items = lines.map((l, li) => <li key={li}>{inline(l.replace(/^\s*([-*]|\d+\.) /, ''), `${bi}-${li}`)}</li>);
          return ul ? <ul key={bi}>{items}</ul> : <ol key={bi}>{items}</ol>;
        }
        // a heading line followed by a list in the same block
        const first = lines.findIndex(l => /^\s*([-*]|\d+\.) /.test(l));
        if (first > 0 && lines.slice(first).every(l => /^\s*([-*]|\d+\.) /.test(l))) {
          const isOl = /^\s*\d+\. /.test(lines[first]);
          const items = lines.slice(first).map((l, li) => <li key={li}>{inline(l.replace(/^\s*([-*]|\d+\.) /, ''), `${bi}-l${li}`)}</li>);
          return (
            <Fragment key={bi}>
              <p>{lines.slice(0, first).map((l, li) => <Fragment key={li}>{li > 0 && <br />}{inline(l, `${bi}-h${li}`)}</Fragment>)}</p>
              {isOl ? <ol>{items}</ol> : <ul>{items}</ul>}
            </Fragment>
          );
        }
        return <p key={bi}>{lines.map((l, li) => <Fragment key={li}>{li > 0 && <br />}{inline(l.replace(/^#+\s*/, ''), `${bi}-${li}`)}</Fragment>)}</p>;
      })}
    </div>
  );
}
