// A small Cairo highlighter for the editor overlay. One regex pass, no deps:
// the editor is a transparent <textarea> over a <pre> painted from this, so
// tokens only need colors, never structure.

const KEYWORDS =
  'as|break|const|continue|else|enum|extern|false|fn|for|if|impl|implicits|let|loop|match|mod|mut|nopanic|of|pub|ref|return|self|static|struct|super|trait|true|type|use|while';
const TYPES =
  'felt252|u8|u16|u32|u64|u128|u256|usize|i8|i16|i32|i64|i128|bool|ByteArray|ContractAddress|ClassHash|Span|Array|Map|Option|Result|Felt252Dict|Nullable|Box';

const TOKEN = new RegExp(
  [
    '(\\/\\/[^\\n]*)', // 1 comment
    '(#\\[[^\\]\\n]*\\])', // 2 attribute
    '("(?:[^"\\\\\\n]|\\\\.)*"|\'(?:[^\'\\\\\\n]|\\\\.)*\')', // 3 string / short string
    `\\b(${KEYWORDS})\\b`, // 4 keyword
    `\\b(${TYPES})\\b`, // 5 builtin type
    '\\b(0x[0-9a-fA-F_]+|\\d[\\d_]*(?:_?[ui]\\d+|_felt252)?)\\b', // 6 number
    '\\b([a-z_][a-z0-9_]*)(?=\\s*(?:::<[^>]*>)?\\s*\\()', // 7 call / fn name
    '\\b([A-Z][A-Za-z0-9_]*)\\b', // 8 type / trait / contract
    '([a-z_][a-z0-9_]*!)', // 9 macro
  ].join('|'),
  'g',
);

const CLASS = ['', 'c', 'a', 's', 'k', 't', 'n', 'f', 'T', 'm'];

function esc(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

export function highlightLine(line: string): string {
  let out = '';
  let last = 0;
  TOKEN.lastIndex = 0;
  for (let m = TOKEN.exec(line); m; m = TOKEN.exec(line)) {
    out += esc(line.slice(last, m.index));
    const g = m.findIndex((v, i) => i > 0 && v !== undefined);
    out += `<span class="h${CLASS[g]}">${esc(m[0])}</span>`;
    last = m.index + m[0].length;
  }
  return out + esc(line.slice(last));
}

export function isCairoish(path: string | null): boolean {
  return !!path && /\.(cairo|toml)$/.test(path);
}
