// Minimal LaTeX -> HTML renderer for whitepaper.tex.
//
// Covers exactly the subset the paper uses (parts, sections, lists, tabularx
// tables, lstlisting, theorem-style boxes, inline + display math via KaTeX,
// \ref/\label) so the web page renders the same source as the PDF and can
// never drift from it again.

import katex from "katex";

export type TocEntry = { id: string; num: string; title: string; level: 0 | 1 | 2 };
export type Paper = {
  title: string;
  subtitle: string;
  version: string;
  date: string;
  abstract: string;
  html: string;
  toc: TocEntry[];
  words: number;
};

const THEOREMS: Record<string, string> = {
  definition: "Definition",
  property: "Property",
  claim: "Claim",
};

const BADGES: Record<string, string> = {
  live: "live",
  proposed: "proposed",
  "reference impl.": "ref",
  "primitives live": "partial",
};

const ROMAN = ["", "I", "II", "III", "IV", "V", "VI"];

function esc(s: string) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function slug(s: string) {
  return s
    .toLowerCase()
    .replace(/<[^>]+>/g, "")
    .replace(/&[a-z]+;/g, "")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "");
}

function math(src: string, display: boolean) {
  try {
    return katex.renderToString(src, { displayMode: display, throwOnError: false, strict: "ignore" });
  } catch {
    return `<code>${esc(src)}</code>`;
  }
}

/** Index of the brace closing the group opened at `s[open] === "{"`. */
function closeBrace(s: string, open: number) {
  let depth = 0;
  for (let i = open; i < s.length; i++) {
    const c = s[i];
    if (c === "\\") { i++; continue; }
    if (c === "{") depth++;
    else if (c === "}" && --depth === 0) return i;
  }
  return s.length - 1;
}

/** Read `{arg}` starting at i (skipping spaces). Returns [arg, nextIndex]. */
function readArg(s: string, i: number): [string, number] {
  while (s[i] === " ") i++;
  if (s[i] !== "{") return ["", i];
  const j = closeBrace(s, i);
  return [s.slice(i + 1, j), j + 1];
}

/** Read an optional `[opt]` starting at i. */
function readOpt(s: string, i: number): [string | null, number] {
  if (s[i] !== "[") return [null, i];
  let depth = 0;
  for (let j = i; j < s.length; j++) {
    if (s[j] === "{") depth++;
    else if (s[j] === "}") depth--;
    else if (s[j] === "]" && depth === 0) return [s.slice(i + 1, j), j + 1];
  }
  return [null, i];
}

/** Find the \end{env} matching a \begin{env} whose body starts at `from`. */
function findEnd(s: string, env: string, from: number) {
  const open = `\\begin{${env}}`;
  const close = `\\end{${env}}`;
  let depth = 1;
  let i = from;
  while (i < s.length) {
    const a = s.indexOf(open, i);
    const b = s.indexOf(close, i);
    if (b < 0) return [s.length, s.length];
    if (a >= 0 && a < b) { depth++; i = a + open.length; continue; }
    if (--depth === 0) return [b, b + close.length];
    i = b + close.length;
  }
  return [s.length, s.length];
}

class Renderer {
  toc: TocEntry[] = [];
  labels = new Map<string, { num: string; id: string }>();
  part = 0;
  sec = 0;
  sub = 0;
  eq = 0;
  thm: Record<string, number> = {};
  appendix = false;
  lastId = "top";
  lastNum = "";

  // ── inline ────────────────────────────────────────────────────────
  inline(s: string): string {
    let out = "";
    for (let i = 0; i < s.length; i++) {
      const c = s[i];
      if (c === "$") {
        const j = s.indexOf("$", i + 1);
        if (j < 0) { out += "$"; continue; }
        out += math(s.slice(i + 1, j), false);
        i = j;
      } else if (c === "\\") {
        const m = /^\\([a-zA-Z]+)\*?|^\\(.)/.exec(s.slice(i));
        if (!m) continue;
        i += m[0].length;
        const r = this.command(m[1] ?? m[2], s, i);
        out += r[0];
        i = r[1] - 1;
      } else if (c === "{") {
        const j = closeBrace(s, i);
        out += this.inline(s.slice(i + 1, j));
        i = j;
      } else if (c === "}") {
        // stray
      } else if (c === "~") {
        out += "&nbsp;";
      } else if (c === "-" && s.startsWith("---", i)) {
        out += "&mdash;"; i += 2;
      } else if (c === "-" && s.startsWith("--", i)) {
        out += "&ndash;"; i += 1;
      } else if (c === "`" && s[i + 1] === "`") {
        out += "&ldquo;"; i++;
      } else if (c === "'" && s[i + 1] === "'") {
        out += "&rdquo;"; i++;
      } else if (c === "%" ) {
        const j = s.indexOf("\n", i);
        i = j < 0 ? s.length : j;
      } else {
        out += esc(c);
      }
    }
    return out;
  }

  command(name: string, s: string, i: number): [string, number] {
    const wrap = (tag: string, cls = ""): [string, number] => {
      const [a, j] = readArg(s, i);
      return [`<${tag}${cls ? ` class="${cls}"` : ""}>${this.inline(a)}</${tag}>`, j];
    };
    switch (name) {
      case "texttt": return wrap("code");
      case "textbf": return wrap("strong");
      case "emph": case "textit": return wrap("em");
      case "textcolor": { const [, j] = readArg(s, i); const [a, k] = readArg(s, j); return [this.inline(a), k]; }
      case "ref": case "eqref": {
        const [a, j] = readArg(s, i);
        return [`\u0000REF:${a}\u0000`, j];
      }
      case "label": { const [a, j] = readArg(s, i); this.labels.set(a, { num: this.lastNum, id: this.lastId }); return ["", j]; }
      case "S": return ["&sect;", i];
      case "ldots": case "dots": return ["&hellip;", i];
      case "textasciitilde": return ["~", s.startsWith("{}", i) ? i + 2 : i];
      case "LaTeX": return ["LaTeX", i];
      case "noindent": case "medskip": case "bigskip": case "smallskip": case "centering":
      case "par": case "newpage": case "small": case "large": case "Large": case "footnotesize":
        return ["", s[i] === " " ? i + 1 : i];
      case "url": { const [a, j] = readArg(s, i); return [`<a href="${esc(a)}">${esc(a)}</a>`, j]; }
      case "href": { const [u, j] = readArg(s, i); const [t, k] = readArg(s, j); return [`<a href="${esc(u)}">${this.inline(t)}</a>`, k]; }
      case "footnote": { const [a, j] = readArg(s, i); return [` <span class="fn">(${this.inline(a)})</span>`, j]; }
      case ",": return ["&thinsp;", i];
      case " ": return [" ", i];
      case "\\": return ["<br/>", i];
      case "_": case "&": case "%": case "$": case "#": case "{": case "}": return [esc(name), i];
      default: return ["", i];
    }
  }

  // ── blocks ────────────────────────────────────────────────────────
  paragraphs(s: string): string {
    return s
      .split(/\n\s*\n/)
      .map((p) => p.trim())
      .filter(Boolean)
      .map((p) => {
        const html = this.inline(p).trim();
        return html ? `<p>${html}</p>` : "";
      })
      .join("\n");
  }

  heading(level: 0 | 1 | 2, starred: boolean, title: string) {
    let num = "";
    if (level === 0) {
      this.part++;
      num = `Part ${ROMAN[this.part] ?? this.part}`;
    } else if (level === 1 && !starred) {
      this.sec++;
      this.sub = 0;
      this.thm = {};
      num = this.appendix ? String.fromCharCode(64 + this.sec) : String(this.sec);
    } else if (level === 2 && !starred) {
      this.sub++;
      const s = this.appendix ? String.fromCharCode(64 + this.sec) : String(this.sec);
      num = `${s}.${this.sub}`;
    }
    const titleHtml = this.inline(title);
    const id = slug((num ? num + "-" : "") + titleHtml) || `s${this.toc.length}`;
    this.lastId = id;
    this.lastNum = num;
    this.toc.push({ id, num, title: titleHtml.replace(/<[^>]+>/g, ""), level });
    const anchor = `<a class="anchor" href="#${id}" aria-label="Link to section">#</a>`;
    if (level === 0) {
      return `<div class="part" id="${id}"><div class="part-num">${num}</div><h2 class="part-title">${titleHtml}</h2></div>`;
    }
    const tag = level === 1 ? "h2" : "h3";
    return `<${tag} id="${id}">${num ? `<span class="num">${num}</span>` : ""}${titleHtml}${anchor}</${tag}>`;
  }

  list(body: string, ordered: boolean) {
    const items: string[] = [];
    let depth = 0;
    let start = -1;
    const re = /\\begin\{(itemize|enumerate)\}|\\end\{(itemize|enumerate)\}|\\item\b/g;
    let m: RegExpExecArray | null;
    while ((m = re.exec(body))) {
      if (m[1]) depth++;
      else if (m[2]) depth--;
      else if (depth === 0) {
        if (start >= 0) items.push(body.slice(start, m.index));
        start = m.index + m[0].length;
      }
    }
    if (start >= 0) items.push(body.slice(start));
    const tag = ordered ? "ol" : "ul";
    return `<${tag}>${items.map((it) => `<li>${this.blocks(it.trim(), true)}</li>`).join("")}</${tag}>`;
  }

  table(body: string, caption: string | null) {
    // body is the inside of tabular/tabularx: drop the column spec(s).
    let s = body.trim();
    if (s.startsWith("{")) s = s.slice(closeBrace(s, 0) + 1); // width or spec
    s = s.trim();
    if (s.startsWith("{")) s = s.slice(closeBrace(s, 0) + 1); // spec (tabularx)
    const hasMid = s.includes("\\midrule");
    const [head, rest] = hasMid ? s.split("\\midrule") : ["", s];
    const rows = (t: string) =>
      t
        .replace(/\\(top|bottom)rule/g, "")
        .split(/\\\\(?:\[[^\]]*\])?/)
        .map((r) => r.trim())
        .filter(Boolean)
        .map((r) => r.split(/(?<!\\)&/).map((c) => c.trim()));
    const cell = (c: string, tag: string) => {
      const html = this.inline(c);
      const plain = html.replace(/<[^>]+>/g, "").trim();
      const badge = BADGES[plain];
      return `<${tag}>${badge ? `<span class="badge badge-${badge}">${plain}</span>` : html}</${tag}>`;
    };
    const thead = rows(head).map((r) => `<tr>${r.map((c) => cell(c, "th")).join("")}</tr>`).join("");
    const tbody = rows(rest).map((r) => `<tr>${r.map((c) => cell(c, "td")).join("")}</tr>`).join("");
    return `<figure class="tbl"><div class="tbl-scroll"><table>${thead ? `<thead>${thead}</thead>` : ""}<tbody>${tbody}</tbody></table></div>${
      caption ? `<figcaption>${this.inline(caption)}</figcaption>` : ""
    }</figure>`;
  }

  code(body: string, opts: string | null) {
    const lang = /style=(\w+)/.exec(opts ?? "")?.[1] ?? (/language=(\w+)/.exec(opts ?? "")?.[1]?.toLowerCase() ?? "");
    const src = body.replace(/^\n/, "").replace(/\s+$/, "");
    return `<div class="code"${lang ? ` data-lang="${lang}"` : ""}><pre><code>${highlight(src, lang)}</code></pre></div>`;
  }

  displayMath(src: string, numbered: boolean) {
    let label: string | null = null;
    src = src.replace(/\\label\{([^}]*)\}/g, (_, l) => { label = l; return ""; });
    let num = "";
    let id = "";
    if (numbered) {
      this.eq++;
      num = `(${this.eq})`;
      id = `eq-${this.eq}`;
      if (label) this.labels.set(label, { num: String(this.eq), id });
    }
    return `<div class="eq"${id ? ` id="${id}"` : ""}>${math(src.trim(), true)}${num ? `<span class="eq-num">${num}</span>` : ""}</div>`;
  }

  blocks(s: string, tight = false): string {
    let out = "";
    const re = /\\begin\{([a-zA-Z*]+)\}|\\(part|section|subsection|subsubsection)(\*?)\s*\{|\\\[|\\appendix\b|\\tableofcontents\b|\\addcontentsline\{[^}]*\}\{[^}]*\}\{[^}]*\}/g;
    let i = 0;
    let m: RegExpExecArray | null;
    const flush = (t: string) => {
      const p = this.paragraphs(t);
      out += tight && !out && p.startsWith("<p>") && !p.includes("</p>\n<p>") ? p.slice(3, -4) : p;
    };
    while ((m = re.exec(s))) {
      flush(s.slice(i, m.index));
      let j = m.index + m[0].length;
      if (m[0] === "\\appendix") {
        this.appendix = true;
        this.sec = 0;
      } else if (m[0] === "\\[") {
        const k = s.indexOf("\\]", j);
        out += this.displayMath(s.slice(j, k), false);
        j = k + 2;
      } else if (m[2]) {
        const k = closeBrace(s, j - 1);
        const level = m[2] === "part" ? 0 : m[2] === "section" ? 1 : 2;
        out += this.heading(level as 0 | 1 | 2, m[3] === "*", s.slice(j, k));
        j = k + 1;
        const lab = /^\s*\\label\{([^}]*)\}/.exec(s.slice(j));
        if (lab) { this.labels.set(lab[1], { num: this.lastNum, id: this.lastId }); j += lab[0].length; }
      } else if (m[1]) {
        const env = m[1];
        const [bodyEnd, after] = findEnd(s, env, j);
        let opt: string | null;
        [opt, j] = readOpt(s, j);
        const body = s.slice(j, bodyEnd);
        j = after;
        out += this.env(env, body, opt);
      }
      i = j;
      re.lastIndex = j;
    }
    flush(s.slice(i));
    return out;
  }

  env(env: string, body: string, opt: string | null): string {
    switch (env) {
      case "itemize": return this.list(body, false);
      case "enumerate": return this.list(body, true);
      case "lstlisting": case "verbatim": return this.code(body, opt);
      case "equation": return this.displayMath(body, true);
      case "equation*": case "align*": return this.displayMath(body, false);
      case "tabular": case "tabularx": return this.table(body, null);
      case "table": case "figure": {
        const cap = /\\caption\{/.exec(body);
        const caption = cap ? body.slice(cap.index + 9, closeBrace(body, cap.index + 8)) : null;
        const t = /\\begin\{(tabularx?)\}/.exec(body);
        if (!t) return "";
        const [e] = findEnd(body, t[1], t.index + t[0].length);
        return this.table(body.slice(t.index + t[0].length, e), caption);
      }
      case "center": case "abstract": return this.blocks(body);
      default: {
        const name = THEOREMS[env];
        if (!name) return this.blocks(body);
        this.thm[env] = (this.thm[env] ?? 0) + 1;
        const num = `${this.appendix ? String.fromCharCode(64 + this.sec) : this.sec}.${this.thm[env]}`;
        const id = `${env}-${num.replace(".", "-")}`;
        this.lastId = id;
        this.lastNum = num;
        body = body.replace(/^\s*\\label\{([^}]*)\}/, (_, l) => { this.labels.set(l, { num, id }); return ""; });
        return `<div class="thm thm-${env}" id="${id}"><div class="thm-head">${name} ${num}${
          opt ? ` <span class="thm-title">${this.inline(opt)}</span>` : ""
        }</div>${this.blocks(body)}</div>`;
      }
    }
  }

  resolve(html: string) {
    return html.replace(/\u0000REF:([^\u0000]*)\u0000/g, (_, key) => {
      const l = this.labels.get(key);
      return l ? `<a class="ref" href="#${l.id}">${l.num}</a>` : "??";
    });
  }
}

const KW: Record<string, RegExp> = {
  solidity: /\b(contract|function|external|internal|public|private|view|pure|returns|return|require|mapping|address|uint256|bytes32|bool|memory|calldata|struct|event|emit|modifier|interface|string|if|indexed)\b/g,
  python: /\b(class|def|return|self|import|from|if|else|for|in|None|True|False|list|dict|str|bool|int)\b/g,
};

function highlight(src: string, lang: string) {
  const kw = KW[lang];
  const comment = lang === "python" ? /#.*$/ : /\/\/.*$/;
  return src
    .split("\n")
    .map((line) => {
      const cm = comment.exec(line);
      const codePart = cm ? line.slice(0, cm.index) : line;
      let html = esc(codePart);
      if (kw) html = html.replace(kw, '<span class="tk-kw">$1</span>');
      if (!lang) html = html.replace(/(&quot;|")([^"]*)(")/g, '<span class="tk-str">"$2"</span>');
      return html + (cm ? `<span class="tk-cm">${esc(cm[0])}</span>` : "");
    })
    .join("\n");
}

export function renderPaper(tex: string): Paper {
  const docStart = tex.indexOf("\\begin{document}");
  const docEnd = tex.lastIndexOf("\\end{document}");
  let doc = tex.slice(docStart >= 0 ? docStart + 16 : 0, docEnd >= 0 ? docEnd : undefined);
  const r = new Renderer();

  // Title page → metadata.
  let title = "MOD Whitepaper";
  let subtitle = "";
  let version = "";
  let date = "";
  let abstract = "";
  const tp = /\\begin\{titlepage\}([\s\S]*?)\\end\{titlepage\}/.exec(doc);
  if (tp) {
    const t = tp[1];
    const huge = /\\Huge[^{]*\\textcolor\{[^}]*\}\{/.exec(t);
    if (huge) {
      const o = huge.index + huge[0].length - 1;
      title = t.slice(o + 1, closeBrace(t, o)).replace(/\\\\/g, " ");
    }
    const lg = /\\Large\\textcolor\{gray\}\{/.exec(t);
    if (lg) {
      const o = lg.index + lg[0].length - 1;
      subtitle = r.inline(t.slice(o + 1, closeBrace(t, o)).replace(/\\\\/g, " "));
    }
    version = /Version\s+([\w.]+)/.exec(t)?.[1] ?? "";
    date = /\\large\s+((?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4})/.exec(t)?.[1] ?? "";
    const ab = /\\begin\{abstract\}([\s\S]*?)\\end\{abstract\}/.exec(t);
    if (ab) abstract = r.blocks(ab[1]);
    doc = doc.replace(tp[0], "");
  }

  const words = doc.replace(/\\[a-zA-Z]+|[{}$\\]/g, " ").split(/\s+/).filter(Boolean).length;
  const html = r.resolve(r.blocks(doc));
  return { title, subtitle, version, date, abstract: r.resolve(abstract), html, toc: r.toc, words };
}
