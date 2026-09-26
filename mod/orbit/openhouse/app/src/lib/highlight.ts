/* A dependency-free tokenizer for the three languages /code serves.

   Highlight.js and Shiki both cost more than the thing they colour here —
   four files, three grammars, no user-supplied code. This is one regex per
   language plus two word sets, and it emits vibe-aware token classes so the
   colours follow whichever of the eight skins is on rather than being baked
   in as hex.

   Multi-line constructs (block comments, triple-quoted docstrings) are the
   reason this tokenizes the whole file and then splits on newlines, instead
   of tokenizing line by line: a `/* ... *\/` spanning forty lines has to stay
   one comment the whole way down. */

export type TokenKind =
  | 'comment' | 'string' | 'number' | 'keyword' | 'type' | 'decorator' | 'plain'

export interface Token { kind: TokenKind; text: string }

/* Tailwind classes, every colour a CSS variable — see tailwind.config.ts.
   `white` is re-pointed at --ink, so `text-white/45` is a soft ink, and the
   whole palette repaints when the vibe changes. */
export const TOKEN_CLASS: Record<TokenKind, string> = {
  comment: 'text-white/45 italic',
  string: 'text-emerald-500',
  number: 'text-lilac',
  keyword: 'text-coral',
  type: 'text-sky',
  decorator: 'text-ember',
  plain: 'text-white/85',
}

const SOLIDITY_KEYWORDS = new Set([
  'pragma', 'solidity', 'contract', 'interface', 'library', 'abstract', 'is',
  'function', 'modifier', 'event', 'error', 'struct', 'enum', 'mapping',
  'constructor', 'receive', 'fallback', 'using', 'import', 'returns', 'return',
  'memory', 'storage', 'calldata', 'public', 'private', 'internal', 'external',
  'pure', 'view', 'payable', 'constant', 'immutable', 'override', 'virtual',
  'if', 'else', 'for', 'while', 'do', 'break', 'continue', 'new', 'delete',
  'emit', 'require', 'revert', 'assert', 'try', 'catch', 'unchecked',
  'assembly', 'indexed', 'anonymous', 'type',
])

const SOLIDITY_TYPES = new Set([
  'address', 'bool', 'string', 'bytes', 'byte', 'uint', 'int', 'fixed', 'ufixed',
  'msg', 'block', 'tx', 'this', 'super', 'true', 'false',
  'wei', 'gwei', 'ether', 'seconds', 'minutes', 'hours', 'days', 'weeks',
])

const PYTHON_KEYWORDS = new Set([
  'def', 'class', 'return', 'yield', 'if', 'elif', 'else', 'for', 'while',
  'break', 'continue', 'pass', 'import', 'from', 'as', 'try', 'except',
  'finally', 'raise', 'with', 'lambda', 'global', 'nonlocal', 'assert', 'del',
  'in', 'is', 'not', 'and', 'or', 'async', 'await', 'match', 'case',
])

const PYTHON_BUILTINS = new Set([
  'self', 'cls', 'True', 'False', 'None', 'int', 'str', 'float', 'bool',
  'list', 'dict', 'set', 'tuple', 'len', 'range', 'print', 'open', 'sorted',
  'sum', 'min', 'max', 'abs', 'round', 'enumerate', 'zip', 'isinstance',
  'getattr', 'setattr', 'hasattr', 'super', 'Exception', 'type',
])

const TS_KEYWORDS = new Set([
  'const', 'let', 'var', 'function', 'return', 'if', 'else', 'for', 'while',
  'break', 'continue', 'new', 'class', 'extends', 'implements', 'interface',
  'type', 'enum', 'import', 'from', 'export', 'default', 'async', 'await',
  'try', 'catch', 'finally', 'throw', 'typeof', 'instanceof', 'in', 'of',
  'delete', 'void', 'as', 'satisfies', 'readonly', 'public', 'private',
])

const TS_TYPES = new Set([
  'string', 'number', 'boolean', 'any', 'unknown', 'never', 'null',
  'undefined', 'true', 'false', 'this', 'Record', 'Array', 'Promise',
])

/* One pass, alternation ordered by precedence: a `#` inside a string must not
   start a comment, so strings and comments are matched in the same sweep
   rather than in two. Capture-group index → token kind. */
const GRAMMARS: Record<string, { re: RegExp; words: (w: string) => TokenKind }> = {
  solidity: {
    re: /(\/\/[^\n]*|\/\*[\s\S]*?\*\/)|("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')|(\b0x[0-9a-fA-F_]+\b|\b\d[\d_]*(?:\.\d+)?(?:e[+-]?\d+)?\b)|([A-Za-z_$][\w$]*)/g,
    words: (w) => {
      if (SOLIDITY_KEYWORDS.has(w)) return 'keyword'
      // uint256 / int8 / bytes32 — the sized primitives are one family
      if (SOLIDITY_TYPES.has(w) || /^(u?int|bytes)\d+$/.test(w)) return 'type'
      return 'plain'
    },
  },
  python: {
    re: /(#[^\n]*)|([rbfuRBFU]{0,2}(?:"""[\s\S]*?"""|'''[\s\S]*?'''|"(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*'))|(@[A-Za-z_][\w.]*)|(\b0x[0-9a-fA-F_]+\b|\b\d[\d_]*(?:\.\d+)?(?:e[+-]?\d+)?\b)|([A-Za-z_][\w]*)/g,
    words: (w) => {
      if (PYTHON_KEYWORDS.has(w)) return 'keyword'
      if (PYTHON_BUILTINS.has(w)) return 'type'
      return 'plain'
    },
  },
  typescript: {
    re: /(\/\/[^\n]*|\/\*[\s\S]*?\*\/)|(`(?:\\.|[^`\\])*`|"(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*')|(\b0x[0-9a-fA-F_]+\b|\b\d[\d_]*(?:\.\d+)?(?:e[+-]?\d+)?\b)|([A-Za-z_$][\w$]*)/g,
    words: (w) => {
      if (TS_KEYWORDS.has(w)) return 'keyword'
      if (TS_TYPES.has(w)) return 'type'
      return 'plain'
    },
  },
}

/** Which capture group carries which kind, per grammar shape. Python has the
 *  extra decorator group, so the two layouts are listed rather than guessed. */
const KINDS: Record<string, TokenKind[]> = {
  solidity: ['comment', 'string', 'number', 'plain'],
  python: ['comment', 'string', 'decorator', 'number', 'plain'],
  typescript: ['comment', 'string', 'number', 'plain'],
}

/** Tokenize `source`, then cut the token stream at newlines so each line can
 *  be rendered as its own row. A token that spans lines (a block comment)
 *  reappears on every line it covers, keeping its kind. */
export function highlightLines(source: string, language: string): Token[][] {
  const grammar = GRAMMARS[language]
  const text = source.replace(/\n$/, '')

  if (!grammar) {
    return text.split('\n').map(line => [{ kind: 'plain' as TokenKind, text: line }])
  }

  const kinds = KINDS[language]
  const flat: Token[] = []
  let last = 0
  grammar.re.lastIndex = 0

  for (let m = grammar.re.exec(text); m !== null; m = grammar.re.exec(text)) {
    if (m.index > last) flat.push({ kind: 'plain', text: text.slice(last, m.index) })

    let kind: TokenKind = 'plain'
    for (let g = 0; g < kinds.length; g++) {
      if (m[g + 1] !== undefined) { kind = kinds[g]; break }
    }
    // The identifier group is the only one that needs a word-list decision.
    if (kind === 'plain') kind = grammar.words(m[0])

    flat.push({ kind, text: m[0] })
    last = m.index + m[0].length
  }
  if (last < text.length) flat.push({ kind: 'plain', text: text.slice(last) })

  const lines: Token[][] = [[]]
  for (const tok of flat) {
    const parts = tok.text.split('\n')
    parts.forEach((part, i) => {
      if (i > 0) lines.push([])
      if (part) lines[lines.length - 1].push({ kind: tok.kind, text: part })
    })
  }
  return lines
}

/** Case-insensitive match count for the find-in-file box. Returns the line
 *  numbers (0-based) that contain `query`, so the viewer can jump between
 *  them without re-scanning on every keystroke. */
export function findLines(lines: string[], query: string): number[] {
  if (!query) return []
  const needle = query.toLowerCase()
  const hits: number[] = []
  for (let i = 0; i < lines.length; i++) {
    if (lines[i].toLowerCase().includes(needle)) hits.push(i)
  }
  return hits
}

/** Split one token's text around every occurrence of `query`, so matches can
 *  be marked without losing the syntax colour underneath them. */
export function splitOnQuery(text: string, query: string): { text: string; hit: boolean }[] {
  if (!query) return [{ text, hit: false }]
  const out: { text: string; hit: boolean }[] = []
  const hay = text.toLowerCase()
  const needle = query.toLowerCase()
  let i = 0
  for (let at = hay.indexOf(needle); at !== -1; at = hay.indexOf(needle, i)) {
    if (at > i) out.push({ text: text.slice(i, at), hit: false })
    out.push({ text: text.slice(at, at + needle.length), hit: true })
    i = at + needle.length
  }
  if (i < text.length) out.push({ text: text.slice(i), hit: false })
  return out
}
