/**
 * A Solidity editor in about two hundred lines and no dependencies.
 *
 * The trick is old and it holds up: a transparent `<textarea>` sits exactly on
 * top of a highlighted `<pre>`, both in the same monospace metrics. The
 * browser keeps doing selection, undo, IME, autocomplete and accessibility —
 * all the things a hand-rolled contenteditable gets wrong — and the only job
 * left is painting the same text underneath in colour.
 *
 * No CodeMirror, no Monaco, no CDN: this console is plain ES modules served
 * off the module's own port, and a code editor is not worth a build step or a
 * third-party script tag on a page where people type private keys.
 *
 * The tokenizer is a regex. It is not a Solidity parser and does not pretend
 * to be one — it colours comments, strings, numbers, keywords, value types and
 * the globals, which is what makes code readable at a glance. Anything it
 * cannot classify is left as plain text rather than guessed at.
 */

const KEYWORDS = ('pragma|solidity|abstract|contract|interface|library|is|'
  + 'function|modifier|constructor|event|error|struct|enum|mapping|returns|'
  + 'return|public|private|internal|external|view|pure|payable|virtual|'
  + 'override|immutable|constant|memory|storage|calldata|if|else|for|while|do|'
  + 'break|continue|new|delete|emit|require|revert|assert|try|catch|using|'
  + 'import|as|from|type|unchecked|receive|fallback|indexed|anonymous|this|'
  + 'super|assembly|let|switch|case|default|true|false');

const TYPES = 'address|bool|string|bytes\\d*|uint\\d*|int\\d*|byte|fixed|ufixed';
const GLOBALS = ('msg|block|tx|abi|now|wei|gwei|ether|seconds|minutes|hours|'
  + 'days|weeks|keccak256|sha256|ecrecover|selfdestruct|blockhash|gasleft');

const TOKEN = new RegExp([
  '(\\/\\*[\\s\\S]*?\\*\\/|\\/\\/[^\\n]*)',            // 1 comment
  '("(?:[^"\\\\\\n]|\\\\.)*"|\'(?:[^\'\\\\\\n]|\\\\.)*\')',  // 2 string
  '\\b(0x[0-9a-fA-F]+|\\d[\\d_]*(?:\\.\\d+)?)\\b',      // 3 number
  `\\b(?:${KEYWORDS})\\b`,                              // 4 keyword
  `\\b(?:${TYPES})\\b`,                                 // 5 type
  `\\b(?:${GLOBALS})\\b`,                               // 6 global
].map((part, i) => (i > 2 ? `(${part})` : part)).join('|'), 'g');

const esc = (s) => String(s).replace(/[&<>]/g, (c) =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]));

const CLASSES = ['tok-comment', 'tok-string', 'tok-number', 'tok-key',
  'tok-type', 'tok-global'];

export function highlight(source) {
  let out = '';
  let last = 0;
  TOKEN.lastIndex = 0;
  let match;
  while ((match = TOKEN.exec(source)) !== null) {
    // A zero-width match would spin here forever; the alternation cannot
    // produce one, but a regex is one edit away from being able to.
    if (match.index === TOKEN.lastIndex) { TOKEN.lastIndex += 1; continue; }
    out += esc(source.slice(last, match.index));
    const group = match.slice(1).findIndex((g) => g !== undefined);
    out += `<span class="${CLASSES[group] || ''}">${esc(match[0])}</span>`;
    last = match.index + match[0].length;
  }
  return out + esc(source.slice(last));
}

export class Editor {
  /**
   * @param {HTMLElement} host   an empty element to fill
   * @param {object} options     {value, onChange, onSave, readOnly, language}
   */
  constructor(host, options = {}) {
    this.host = host;
    this.onChange = options.onChange || (() => {});
    this.onSave = options.onSave || null;
    this.plain = options.language === 'json' || options.language === 'text';
    host.classList.add('editor');
    host.innerHTML = `
      <div class="ed-gutter" aria-hidden="true"></div>
      <div class="ed-scroll">
        <pre class="ed-paint" aria-hidden="true"><code></code></pre>
        <textarea class="ed-input" spellcheck="false" autocomplete="off"
                  autocapitalize="off" autocorrect="off" wrap="off"></textarea>
      </div>`;
    this.gutter = host.querySelector('.ed-gutter');
    this.scroll = host.querySelector('.ed-scroll');
    this.paint = host.querySelector('.ed-paint code');
    this.input = host.querySelector('.ed-input');
    this.input.readOnly = !!options.readOnly;
    this.input.placeholder = options.placeholder || '';

    this.input.addEventListener('input', () => { this.render(); this.onChange(this.value); });
    this.input.addEventListener('scroll', () => this.sync());
    this.input.addEventListener('keydown', (e) => this.keydown(e));
    this.setValue(options.value || '');
  }

  get value() { return this.input.value; }

  setValue(text) {
    this.input.value = text ?? '';
    this.render();
  }

  focus() { this.input.focus(); }

  set readOnly(flag) { this.input.readOnly = !!flag; this.host.classList.toggle('ro', !!flag); }

  render() {
    const text = this.input.value;
    // A trailing newline collapses in a <pre>, which puts the paint one line
    // out of register with the caret at the bottom of the file.
    this.paint.innerHTML = this.plain ? esc(text) + '\n' : highlight(text) + '\n';
    const lines = text.split('\n').length;
    if (this.lines !== lines) {
      this.lines = lines;
      this.gutter.innerHTML = Array.from({ length: lines },
        (_, i) => `<span>${i + 1}</span>`).join('');
    }
    this.sync();
  }

  sync() {
    this.paint.parentElement.scrollTop = this.input.scrollTop;
    this.paint.parentElement.scrollLeft = this.input.scrollLeft;
    this.gutter.scrollTop = this.input.scrollTop;
  }

  keydown(e) {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 's') {
      e.preventDefault();
      if (this.onSave) this.onSave(this.value);
      return;
    }
    if (e.key === 'Tab') {
      e.preventDefault();
      this.indent(e.shiftKey);
      return;
    }
    if (e.key === 'Enter') this.newline(e);
  }

  /** Tab indents; shift-tab outdents. With a selection, whole lines move —
   *  anything else makes tab a way to delete the code you just selected. */
  indent(back) {
    const { selectionStart: from, selectionEnd: to, value } = this.input;
    const unit = '  ';
    if (from === to && !back) {
      this.splice(from, to, unit, from + unit.length);
      return;
    }
    const start = value.lastIndexOf('\n', from - 1) + 1;
    const end = value.indexOf('\n', to);
    const block = value.slice(start, end === -1 ? value.length : end);
    const moved = block.split('\n').map((line) => (back
      ? line.replace(/^ {1,2}|^\t/, '')
      : unit + line)).join('\n');
    this.splice(start, end === -1 ? value.length : end, moved,
      start, start + moved.length);
  }

  /** Enter keeps the current indentation, and adds one level after a `{`. */
  newline(e) {
    const { selectionStart: from, selectionEnd: to, value } = this.input;
    if (from !== to) return;
    const lineStart = value.lastIndexOf('\n', from - 1) + 1;
    const line = value.slice(lineStart, from);
    const pad = (line.match(/^[ \t]*/) || [''])[0];
    const opens = /[{([]\s*$/.test(line);
    const closes = /^[\s]*[}\])]/.test(value.slice(from));
    if (!pad && !opens) return;
    e.preventDefault();
    const inner = pad + (opens ? '  ' : '');
    const text = opens && closes ? `\n${inner}\n${pad}` : `\n${inner}`;
    this.splice(from, to, text, from + 1 + inner.length);
  }

  /** One edit that the browser's own undo stack keeps, when it can.
   *  execCommand is deprecated and is still the only way to write into a
   *  textarea without throwing away undo history; the direct write is the
   *  fallback for browsers that have finally removed it. */
  splice(from, to, text, caret, caretEnd) {
    this.input.setSelectionRange(from, to);
    let inserted = false;
    try {
      inserted = document.execCommand('insertText', false, text);
    } catch { inserted = false; }
    if (!inserted) {
      const value = this.input.value;
      this.input.value = value.slice(0, from) + text + value.slice(to);
    }
    this.input.setSelectionRange(caret, caretEnd ?? caret);
    this.render();
    this.onChange(this.value);
  }
}
