/**
 * The build bench: write a contract, keep it, test it, deploy it, share it.
 *
 * Three columns, and the order is the order of the work. Your projects on the
 * left, the code in the middle, what happened on the right. Nothing here is a
 * modal, because a modal is a thing you dismiss and then cannot read while you
 * fix what it told you about.
 *
 * The one idea worth stating: a project is not a file on this box. Saving
 * uploads it to the **store module** and keeps the CID that comes back, so a
 * version is a CID, sharing is handing somebody a CID, and opening what
 * somebody shared needs no account at all. When the store will not take it —
 * asleep, or your address is not on its whitelist — the save still lands
 * locally and the left rail says why there is no CID yet, rather than
 * pretending the work is safe or throwing it away.
 */
import { Editor } from './editor.js';

const STARTER = `// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// A contract worth deploying twice: once to see it work, once to mean it.
contract Greeter {
    string public greeting;
    address public owner;

    event Greeted(address indexed who, string greeting);

    constructor(string memory first) {
        greeting = first;
        owner = msg.sender;
    }

    function greet(string calldata next) external {
        require(bytes(next).length > 0, "say something");
        greeting = next;
        emit Greeted(msg.sender, next);
    }
}
`;

export function initBuild(ctx) {
  const { call, q, toast, esc, short, $ } = ctx;

  let project = null;        // the open project, or null for a scratch buffer
  let file = 'Contract.sol'; // which file the editor is showing
  let files = { 'Contract.sol': STARTER };
  let compiled = null;
  let chosen = null;         // the contract inside the compile we would deploy
  let shared = null;         // a bundle opened by CID that is not ours
  let origin = null;         // the CID this buffer was copied from, if any
  let dirty = false;
  let storeState = null;
  let lastStatus = null;

  const editor = new Editor($('editor'), {
    value: STARTER,
    onChange: (text) => {
      files[file] = text;
      if (shared) becomeCopy();
      markDirty(true);
    },
    onSave: () => save(),
  });
  const suiteEditor = new Editor($('suite-editor'), {
    language: 'json',
    value: '',
    placeholder: 'a suite is JSON — press generate for a starter',
  });

  /* ── chrome ───────────────────────────────────────────────── */

  function markDirty(flag) {
    dirty = flag;
    $('pj-save').classList.toggle('urgent', flag);
    $('pj-save').textContent = flag ? 'save •' : 'save';
    renderChips();
  }

  /** The bench head says what this buffer is without being asked: whose it
   *  is, whether it reached the store, and whether what you see is saved. */
  function renderChips() {
    const el = $('pj-chips');
    const chips = [];
    if (shared) chips.push('<span class="chip live">someone else\u2019s</span>');
    else if (origin) chips.push('<span class="chip live">a copy</span>');
    if (project) {
      chips.push(project.public
        ? '<span class="chip good">public</span>'
        : '<span class="chip">private</span>');
      chips.push(project.cid
        ? `<button class="chip live" id="chip-cid" title="copy the CID">${esc(short(project.cid))}</button>`
        : '<span class="chip warn">no CID yet</span>');
    } else if (!shared) {
      // "unsaved" already says everything "edited" would; two chips for one
      // fact is noise on the only line that has to stay readable.
      chips.push(`<span class="chip">${ctx.address() ? 'unsaved' : 'scratch buffer'}</span>`);
    }
    if (dirty && project) chips.push('<span class="chip warn">edited</span>');
    el.innerHTML = chips.join('');
    const copy = $('chip-cid');
    if (copy) copy.onclick = () => { navigator.clipboard?.writeText(project.cid); toast('CID copied'); };
  }

  /** Whose code is on screen, said above the code rather than in a side
   *  panel — it decides what the save button is about to do. */
  function renderBanner() {
    const el = $('pj-banner');
    if (shared) {
      el.innerHTML = `<div class="banner">
        <b>${esc(shared.name)}</b>
        <span class="why">by ${esc(short(shared.author || '') || 'someone else')} —
          opened from the store. Edit it and it becomes your copy; fork it to
          keep the provenance.</span>
        <button class="tiny primary" id="bn-fork">fork it</button>
        <button class="tiny ghost" id="bn-copy-cid">copy CID</button>
      </div>`;
      $('bn-fork').onclick = forkShared;
      $('bn-copy-cid').onclick = () => {
        navigator.clipboard?.writeText(shared.cid); toast('CID copied');
      };
      return;
    }
    if (origin && !project) {
      el.innerHTML = `<div class="banner">
        <b>your copy</b>
        <span class="why">from <span class="mono">${esc(short(origin))}</span> —
          ${ctx.address() ? 'save it to keep it and to get a CID of your own.'
            : 'sign in to save it; until then it still compiles and deploys.'}</span>
        <button class="tiny ghost" id="bn-back">back to the original</button>
      </div>`;
      $('bn-back').onclick = () => openCid(origin);
      return;
    }
    if (project && project.public && project.cid) {
      el.innerHTML = `<div class="banner">
        <b>shared</b>
        <span class="why">anyone with this link opens exactly these bytes.</span>
        <span class="mono">${esc(short(project.cid))}</span>
        <button class="tiny ghost" id="bn-link">copy link</button>
      </div>`;
      $('bn-link').onclick = () => {
        navigator.clipboard?.writeText(shareUrl(project.cid));
        toast('share link copied');
      };
      return;
    }
    el.innerHTML = '';
  }

  function becomeCopy() {
    origin = shared.cid;
    shared = null;
    project = null;
    renderBanner();
    renderShare();
    toast('this is your copy now — save it to keep it');
  }

  async function forkShared() {
    if (!ctx.address()) { toast('sign in to fork this into your workspace', true); return; }
    const cid = shared?.cid || origin;
    if (!cid) return;
    try {
      const got = await call('/fork', { method: 'POST', json: { cid } });
      toast(`forked as ${got.name}`);
      await loadProjects();
      openProject(got.id);
    } catch (e) { toast(e.message, true); }
  }

  /** A balance is read at a glance or not at all: eighteen decimals of wei is
   *  precision nobody is checking on a status line. The full figure is on the
   *  overview tab, where it is the subject rather than an aside. */
  function money(amount) {
    const n = Number(amount);
    if (!Number.isFinite(n)) return String(amount);
    if (n === 0) return '0';
    if (n >= 1000) return n.toFixed(2);
    if (n >= 0.001) return String(Number(n.toFixed(4)));
    return n.toExponential(2);
  }

  /** The share link as a stranger would paste it: absolute, and pointing at
   *  this deployment rather than at whatever origin happens to be default. */
  function shareUrl(cid) {
    return `${location.origin}${location.pathname}?open=${cid}`;
  }

  /** A CID, or anything a person plausibly pastes that contains one — a
   *  share link, a store url, a line with the CID at the end of it. */
  function cidOf(text) {
    const raw = String(text || '').trim();
    if (!raw) return '';
    const param = raw.match(/[?&](?:open|cid)=([^&#\s]+)/);
    if (param) return decodeURIComponent(param[1]);
    if (raw.includes('/')) return raw.split(/[/?#]/).filter(Boolean).pop() || '';
    return raw.split(/\s+/).pop();
  }

  function out(where, html, kind) {
    const el = $(where);
    el.className = `out${kind ? ` ${kind}` : ''}`;
    el.innerHTML = html;
  }

  function showResult(which) {
    document.querySelectorAll('.res-tab').forEach((t) =>
      t.classList.toggle('on', t.dataset.res === which));
    document.querySelectorAll('.res').forEach((p) =>
      p.classList.toggle('on', p.id === which));
  }

  document.querySelectorAll('.res-tab').forEach((tab) => {
    tab.onclick = () => showResult(tab.dataset.res);
  });

  /* ── the file strip ───────────────────────────────────────── */

  function renderFiles() {
    const strip = $('pj-files');
    strip.innerHTML = Object.keys(files).map((path) =>
      `<button class="file${path === file ? ' on' : ''}" data-file="${esc(path)}">`
      + `${esc(path)}${Object.keys(files).length > 1
        ? `<span class="x" data-drop="${esc(path)}" title="remove">×</span>` : ''}</button>`
    ).join('') + '<button class="file add" id="file-add" title="another file">+</button>';

    strip.querySelectorAll('[data-file]').forEach((b) => {
      b.onclick = (e) => {
        if (e.target.dataset.drop) return;
        file = b.dataset.file;
        editor.setValue(files[file] ?? '');
        renderFiles();
      };
    });
    strip.querySelectorAll('[data-drop]').forEach((x) => {
      x.onclick = (e) => {
        e.stopPropagation();
        const path = x.dataset.drop;
        if (Object.keys(files).length < 2) return;
        delete files[path];
        if (file === path) { file = Object.keys(files)[0]; editor.setValue(files[file]); }
        markDirty(true);
        renderFiles();
      };
    });
    $('file-add').onclick = () => {
      const name = prompt('new file (a .sol name):', 'Lib.sol');
      if (!name) return;
      const path = name.endsWith('.sol') ? name : `${name}.sol`;
      if (files[path] !== undefined) { toast(`${path} is already here`, true); return; }
      files[path] = `// SPDX-License-Identifier: MIT\npragma solidity ^0.8.20;\n\n`;
      file = path;
      editor.setValue(files[path]);
      markDirty(true);
      renderFiles();
    };
  }

  /* ── the project rail ─────────────────────────────────────── */

  async function loadProjects() {
    const rail = $('pj-list');
    if (!ctx.address()) {
      rail.innerHTML = '<p class="hint">Sign in to keep what you write. '
        + 'Until then this is a scratch buffer — it compiles, it deploys, '
        + 'it is not saved anywhere.</p>';
      renderStore(null);
      return;
    }
    try {
      const got = await call('/projects');
      storeState = got.store;
      renderStore(got.store);
      const list = got.projects || [];
      if (!list.length) {
        rail.innerHTML = '<p class="hint">Nothing saved yet. Write something '
          + 'and press <b>save</b> — it goes to the store and comes back with '
          + 'a CID.</p>';
        return;
      }
      rail.innerHTML = list.map((p) => `
        <button class="pj${project && p.id === project.id ? ' on' : ''}" data-project="${p.id}">
          <span class="pj-name">${esc(p.name)}</span>
          <span class="pj-meta">${p.file_count} file${p.file_count === 1 ? '' : 's'}
            · ${esc((p.contracts || []).join(', ') || 'no contract yet')}</span>
          <span class="pj-tags">
            ${p.public ? '<span class="tag pub">public</span>' : ''}
            ${p.cid ? `<span class="tag cid">${esc(short(p.cid))}</span>`
              : '<span class="tag warn">not stored</span>'}
          </span>
        </button>`).join('');
      // `data-project`, not `data-open`: the deployed-contracts tab already
      // owns `[data-open]`, and two panes answering one selector is how a
      // click in one of them opens something in the other.
      rail.querySelectorAll('[data-project]').forEach((b) => {
        b.onclick = () => openProject(b.dataset.project);
      });
    } catch (e) {
      rail.innerHTML = `<p class="hint err">${esc(e.message)}</p>`;
    }
  }

  function renderStore(state) {
    const el = $('store-state');
    if (!state) { el.innerHTML = ''; return; }
    if (!state.reachable) {
      el.innerHTML = `<div class="note bad"><b>store unreachable</b>
        <span>${esc(state.error || '')}</span>
        <span class="hint">Projects still save on this box; they get no CID
        and cannot be shared until the store answers.</span></div>`;
      return;
    }
    if (state.can_share) {
      const quota = state.quota || {};
      el.innerHTML = `<div class="note ok"><b>store ready</b>
        <span class="hint">${esc(short(state.address || ''))}${quota.remaining_bytes !== undefined
          ? ` · ${(quota.remaining_bytes / 1048576).toFixed(0)} MB left` : ''}</span></div>`;
      return;
    }
    const blockers = state.blockers || [];
    const needsTerms = blockers.some((b) => b.includes('terms'));
    el.innerHTML = `<div class="note warn"><b>storage is blocked</b>
      ${blockers.map((b) => `<span class="hint">${esc(b)}</span>`).join('')}
      ${needsTerms ? '<button class="tiny ghost" id="store-accept">accept the terms</button>' : ''}
      </div>`;
    const accept = $('store-accept');
    if (accept) {
      accept.onclick = async () => {
        try {
          await call('/store/terms', { method: 'POST' });
          toast('terms accepted — the store will take uploads now');
          loadProjects();
        } catch (e) { toast(e.message, true); }
      };
    }
  }

  async function openProject(id) {
    try {
      const got = await call(`/projects/${encodeURIComponent(id)}`);
      project = got;
      shared = null;
      origin = got.origin_cid || null;
      files = { ...got.files };
      file = got.entry && files[got.entry] ? got.entry : Object.keys(files)[0];
      editor.setValue(files[file]);
      editor.readOnly = false;
      $('pj-name').value = got.name;
      suiteEditor.setValue((got.tests || []).length
        ? JSON.stringify(got.tests, null, 2) : '');
      compiled = null; chosen = null;
      $('b-contract').innerHTML = '';
      $('ctor').innerHTML = '';
      out('b-out', '');
      markDirty(false);
      renderFiles();
      renderBanner();
      renderShare();
      loadProjects();
      loadRuns();
    } catch (e) { toast(e.message, true); }
  }

  function newProject() {
    project = null; shared = null; origin = null;
    files = { 'Contract.sol': STARTER };
    file = 'Contract.sol';
    editor.setValue(STARTER);
    editor.readOnly = false;
    $('pj-name').value = '';
    suiteEditor.setValue('');
    compiled = null; chosen = null;
    $('b-contract').innerHTML = '';
    $('ctor').innerHTML = '';
    out('b-out', '');
    markDirty(false);
    renderFiles();
    renderBanner();
    renderShare();
    loadProjects();
    editor.focus();
  }

  /* ── saving and sharing ───────────────────────────────────── */

  function suites() {
    const text = suiteEditor.value.trim();
    if (!text) return null;
    try {
      const parsed = JSON.parse(text);
      return Array.isArray(parsed) ? parsed : [parsed];
    } catch (e) {
      throw new Error(`the test suite is not valid JSON: ${e.message}`);
    }
  }

  async function save() {
    if (!ctx.address()) { toast('sign in to save — this is a scratch buffer', true); return; }
    const name = $('pj-name').value.trim();
    let tests = null;
    try { tests = suites(); } catch (e) { toast(e.message, true); showResult('res-tests'); return; }
    const body = { name: name || undefined, files, entry: file,
                   tests: tests || [], origin_cid: origin || undefined };
    try {
      const got = project
        ? await call(`/projects/${project.id}`, { method: 'PUT', json: body })
        : await call('/projects', { method: 'POST', json: body });
      project = got;
      origin = got.origin_cid || origin;
      $('pj-name').value = got.name;
      markDirty(false);
      renderBanner();
      renderShare();
      loadProjects();
      if (got.store && got.store.stored) {
        toast(`saved · ${short(got.cid)}`);
      } else {
        toast(`saved on this box — the store said: ${got.store?.reason || 'no'}`, true);
        showResult('res-share');
      }
    } catch (e) { toast(e.message, true); }
  }

  function renderShare() {
    const el = $('res-share');
    if (shared) {
      el.innerHTML = `
        <div class="note"><b>somebody else's project</b>
          <span class="hint">Opened from the store by CID. Read it, compile it,
          deploy it as it stands — or fork it and it is yours: your slug, your
          CID once you save, your right to delete it.</span></div>
        <dl class="facts">
          <dt>name</dt><dd>${esc(shared.name)}</dd>
          <dt>author</dt><dd class="mono wrap">${esc(shared.author || 'unknown')}</dd>
          <dt>cid</dt><dd class="mono wrap">${esc(shared.cid)}</dd>
        </dl>
        <div class="row wrap">
          <button class="primary" id="pj-fork">fork into my workspace</button>
          <button class="ghost tiny" id="share-copy-open">copy this link</button>
        </div>`;
      $('pj-fork').onclick = forkShared;
      $('share-copy-open').onclick = () => {
        navigator.clipboard?.writeText(shareUrl(shared.cid));
        toast('share link copied');
      };
      return;
    }
    if (!project) {
      el.innerHTML = `
        <ol class="steps">
          <li><b>write</b> it here — or start from a template.</li>
          <li><b>save</b> it: the bytes go to the store and come back as a CID.</li>
          <li><b>share</b> publishes that CID, and the CID is the whole link.</li>
        </ol>
        <p class="hint">Content-addressed, so what somebody opens is exactly
          what you saved and a later edit cannot change it under them.
          ${ctx.address() ? '' : 'Sign in (top right) to get that far — until then this is a scratch buffer that still compiles and deploys.'}</p>
        <div class="row"><button class="primary" id="share-save-now">${
          ctx.address() ? 'save it and publish' : 'sign in to share'}</button></div>`;
      $('share-save-now').onclick = () => (ctx.address() ? shareNow() : ctx.signIn());
      return;
    }
    const link = project.cid ? shareUrl(project.cid) : null;
    el.innerHTML = `
      <dl class="facts">
        <dt>project</dt><dd>${esc(project.name)}</dd>
        <dt>visible</dt><dd>${project.public ? 'public — anyone with the CID'
          : 'private — only you'}</dd>
        ${origin ? `<dt>forked from</dt><dd class="mono wrap">${esc(short(origin))}</dd>` : ''}
      </dl>
      ${project.cid ? `<div class="big-cid" title="the version somebody opens">${esc(project.cid)}</div>`
        : '<p class="hint warn-t">Not in the store yet — publish it and it gets a CID.</p>'}
      ${link ? `<div class="share-link">
        <input class="grow mono" id="share-link" readonly value="${esc(link)}">
        <button class="tiny ghost" id="share-copy">copy link</button>
        <button class="tiny ghost" id="share-copy-cid">copy CID</button>
        <a class="tiny" href="${esc(link)}" target="_blank" rel="noreferrer">open it ↗</a>
      </div>` : ''}
      <div class="row wrap">
        <button class="${project.public ? 'ghost' : 'primary'}" id="share-go">
          ${project.public ? 'make it private' : 'publish it'}</button>
        <span class="hint">${project.public
          ? 'Anyone already holding the CID keeps what they fetched — that is what content addressing means.'
          : 'Publishing makes the stored object readable by anyone with the CID, signed in or not.'}</span>
      </div>
      ${(project.versions || []).length ? `<h3>versions</h3><div class="rows">${
        project.versions.map((v) => `<div class="item">
          <span class="mono grow">${esc(short(v.cid))}</span>
          <span class="muted">${esc(v.note || '')}</span>
          <button class="tiny ghost" data-ver="${esc(v.cid)}">open</button>
        </div>`).join('')}</div>` : ''}`;

    const copy = (text, what) => { navigator.clipboard?.writeText(text); toast(`${what} copied`); };
    if (link) {
      $('share-copy').onclick = () => copy(link, 'link');
      $('share-copy-cid').onclick = () => copy(project.cid, 'CID');
    }
    $('share-go').onclick = () => publish(!project.public);
    el.querySelectorAll('[data-ver]').forEach((b) => {
      b.onclick = () => openCid(b.dataset.ver);
    });
  }

  async function publish(on) {
    try {
      const path = on ? 'share' : 'unshare';
      const got = await call(`/projects/${project.id}/${path}`, { method: 'POST' });
      project = { ...project, public: got.public, cid: got.cid || project.cid };
      toast(got.public ? 'published — the link is the CID' : 'made private');
      renderChips();
      renderBanner();
      renderShare();
      loadProjects();
    } catch (e) { toast(e.message, true); }
  }

  /** The share button is one press from writing to a link: save what is on
   *  screen (publishing last week's version is worse than a click), then
   *  publish it, then show the link. */
  async function shareNow() {
    showResult('res-share');
    if (shared) { renderShare(); return; }
    if (!ctx.address()) {
      renderShare();
      toast('sign in to publish — a CID is stored under your address', true);
      return;
    }
    if (!project || dirty) {
      await save();
      if (!project) return;
    }
    if (!project.public) await publish(true);
    else renderShare();
  }

  async function openCid(cid) {
    cid = cidOf(cid);
    if (!cid) { toast('paste a CID (or a share link) first', true); return; }
    try {
      const got = await call(`/open?${q({ cid })}`);
      shared = got;
      project = null;
      origin = null;
      files = { ...got.files };
      file = got.entry && files[got.entry] ? got.entry : Object.keys(files)[0];
      editor.setValue(files[file]);
      $('pj-name').value = got.name;
      suiteEditor.setValue((got.tests || []).length
        ? JSON.stringify(got.tests, null, 2) : '');
      compiled = null; chosen = null;
      $('b-contract').innerHTML = '';
      $('ctor').innerHTML = '';
      markDirty(false);
      renderFiles();
      renderBanner();
      renderShare();
      showResult('res-share');
      $('pj-cid').value = '';
      toast(`opened ${got.name} from the store`);
    } catch (e) { toast(e.message, true); }
  }

  /* ── compile ──────────────────────────────────────────────── */

  async function compile() {
    out('b-out', 'compiling…');
    showResult('res-out');
    try {
      const got = await call('/compile', { method: 'POST', json: {
        sources: files, optimize: $('b-optimize').checked,
      }});
      compiled = got;
      const deployable = (got.contracts || []).filter((c) => c.deployable);
      const select = $('b-contract');
      select.innerHTML = deployable.map((c) =>
        `<option value="${esc(c.name)}">${esc(c.name)}</option>`).join('')
        || '<option value="">nothing deployable</option>';
      select.onchange = () => pick(select.value);
      pick(deployable[0]?.name);
      const warnings = got.warnings || [];
      out('b-out', `<span class="ok">compiled with solc ${esc(got.compiler?.version || '')}</span>\n`
        + deployable.map((c) => `${esc(c.name)} — ${c.size} bytes, `
          + `${(c.abi || []).filter((e) => e.type === 'function').length} functions`).join('\n')
        + (warnings.length ? `\n\n<span class="warn-t">${warnings.length} warning(s)</span>\n`
          + esc(warnings.map((w) => w.formattedMessage || w.message).join('\n')).slice(0, 4000) : ''));
    } catch (e) {
      compiled = null; chosen = null;
      out('b-out', `<span class="err">${esc(e.message)}</span>`
        + (e.data?.errors ? `\n\n${esc(e.data.errors.map((x) =>
          x.formattedMessage || x.message).join('\n'))}` : ''), 'has-error');
    }
  }

  function pick(name) {
    chosen = (compiled?.contracts || []).find((c) => c.name === name) || null;
    $('b-contract').value = name || '';
    renderCtor();
  }

  function renderCtor() {
    const el = $('ctor');
    const inputs = chosen?.constructor?.inputs || [];
    if (!chosen) { el.innerHTML = ''; return; }
    if (!inputs.length) {
      el.innerHTML = '<span class="ctor-label">constructor</span>'
        + '<span class="hint">takes no arguments</span>';
      return;
    }
    el.innerHTML = '<span class="ctor-label">constructor</span>' + inputs.map((input, i) =>
      `<input data-ctor="${i}" placeholder="${esc(input.name || `arg ${i}`)} · ${esc(input.type)}">`
    ).join('');
  }

  function ctorArgs() {
    return [...document.querySelectorAll('#ctor [data-ctor]')].map((el) => {
      const text = el.value.trim();
      if (text === '') return '';
      if (text.startsWith('[') || text.startsWith('{')) {
        try { return JSON.parse(text); } catch { return text; }
      }
      if (text === 'true') return true;
      if (text === 'false') return false;
      return text;
    });
  }

  /* ── test ─────────────────────────────────────────────────── */

  async function generateSuite() {
    try {
      const got = await call('/test/generate', { method: 'POST', json: {
        files, contract: chosen?.name, optimize: $('b-optimize').checked,
      }});
      const existing = suiteEditor.value.trim();
      const merged = existing ? `${existing.replace(/\s*$/, '')}\n` : '';
      suiteEditor.setValue(merged
        ? `${merged}\n${JSON.stringify(got, null, 2)}`
        : JSON.stringify(got, null, 2));
      showResult('res-tests');
      toast(`${got.cases.length} starter case(s) — add expectations to make it a test`);
    } catch (e) { toast(e.message, true); }
  }

  async function runTests() {
    let parsed = null;
    try { parsed = suites(); } catch (e) { toast(e.message, true); return; }
    const account = $('b-account').value;
    if (!account) { toast('you need an account to sign the deploy', true); return; }
    showResult('res-tests');
    $('test-report').innerHTML = `<p class="running">running on <b>${esc(ctx.net())}</b> —
      this deploys the contract and sends every write for real, so it takes as
      long as the chain does.</p>`;
    try {
      const got = await call('/test', { method: 'POST', json: {
        project: project && !dirty ? String(project.id) : undefined,
        files: (!project || dirty) ? files : undefined,
        contract: chosen?.name,
        suites: parsed || undefined,
        // Always sent: a suite that leaves `args` null (which is what the
        // generated one does when there is a constructor) falls back to
        // whatever the constructor row on the bench holds.
        args: ctorArgs(),
        account,
        password: $('b-pw').value || undefined,
        network: ctx.net(),
        confirm: $('b-confirm').checked,
      }});
      renderReport(got);
      loadRuns();
      ctx.onActivity();
    } catch (e) {
      $('test-report').innerHTML = `<p class="out has-error"><span class="err">${esc(e.message)}</span></p>`;
    }
  }

  function renderReport(report) {
    const el = $('test-report');
    const verdict = report.ok ? 'passed' : 'failed';
    el.innerHTML = `
      <div class="verdict ${verdict}">
        <b>${report.passed}/${report.total} passed</b>
        <span>on ${esc(report.network)}${report.testnet ? ' · testnet' : ' · REAL MONEY'}</span>
        <span class="muted">${report.seconds}s</span>
        ${report.cid ? `<span class="tag cid" title="the full report in the store">${esc(short(report.cid))}</span>` : ''}
      </div>
      ${(report.suites || []).map((s) => `
        <div class="suite">
          <div class="suite-head">
            <b>${esc(s.name)}</b>
            ${s.contract ? `<span class="muted">${esc(s.contract)}</span>` : ''}
            ${s.address ? `<span class="mono muted">${esc(short(s.address))}</span>` : ''}
            <span class="grow"></span>
            <span class="${s.failed ? 'err' : 'ok'}">${s.passed}/${s.passed + s.failed}</span>
          </div>
          ${s.note ? `<p class="hint">${esc(s.note)}</p>` : ''}
          ${(s.cases || []).map((c) => `
            <div class="case ${c.ok ? 'ok' : 'bad'}">
              <span class="dot-case"></span>
              <span class="case-name">${esc(c.name)}</span>
              <span class="case-why">${esc(c.why || '')}</span>
              ${c.gas_used ? `<span class="muted">${c.gas_used} gas</span>` : ''}
            </div>`).join('')}
        </div>`).join('')}`;
  }

  async function loadRuns() {
    if (!ctx.address()) return;
    try {
      const { runs } = await call(`/tests?${q({ limit: 8 })}`);
      const el = $('test-runs');
      if (!runs.length) { el.innerHTML = ''; return; }
      el.innerHTML = '<h3>past runs</h3>' + runs.map((r) => `
        <button class="item run ${r.failed ? 'bad' : 'ok'}" data-run="${r.id}">
          <b>${r.passed}/${r.passed + r.failed}</b>
          <span class="muted">${esc(r.contract || '')}</span>
          <span class="grow muted">${esc(r.network)}</span>
          ${r.cid ? `<span class="tag cid">${esc(short(r.cid))}</span>` : ''}
        </button>`).join('');
      el.querySelectorAll('[data-run]').forEach((b) => {
        b.onclick = async () => {
          try {
            const got = await call(`/tests/${b.dataset.run}`);
            renderReport(got.report);
          } catch (e) { toast(e.message, true); }
        };
      });
    } catch { /* the runs list is a nicety, not a blocker */ }
  }

  /* ── deploy ───────────────────────────────────────────────── */

  async function deploy() {
    // Compiling first is not a separate errand: nobody wants "compile first"
    // as an answer to "deploy". The one thing this cannot guess is which
    // contract, and that only matters when the source declares several.
    if (!chosen) {
      await compile();
      if (!chosen) {
        toast('nothing deployable in this source — the compiler said why', true);
        return;
      }
    }
    const account = $('b-account').value;
    if (!account) {
      toast(ctx.address() ? 'make an account on the accounts tab — something has to sign this'
        : 'sign in and make an account — a deploy has to be signed by a key', true);
      return;
    }
    showResult('res-out');
    $('b-deployed').innerHTML = '';
    out('b-out', `deploying ${chosen.name} to ${ctx.net()}…`);
    try {
      const got = await call('/deploy', { method: 'POST', json: {
        sources: files, contract: chosen.name, args: ctorArgs(),
        account, password: $('b-pw').value || undefined,
        network: ctx.net(), optimize: $('b-optimize').checked,
        confirm: $('b-confirm').checked,
        note: project ? `project ${project.slug}` : undefined,
      }});
      out('b-out', '');
      renderDeployed(got);
      ctx.onActivity();
    } catch (e) {
      $('b-deployed').innerHTML = '';
      out('b-out', `<span class="err">${esc(e.message)}</span>`, 'has-error');
    }
  }

  /** Before the deploy button is pressed the output column answers the three
   *  questions that decide whether pressing it can work at all: is the chain
   *  answering, is there a compiler, and does the key that signs have gas. */
  async function renderReady(status) {
    const el = $('b-ready');
    const net = status.network || {};
    const account = $('b-account').value;
    const found = (status.accounts || []).find((a) => a.name === account)
      || (status.accounts || [])[0];
    el.innerHTML = `<dl class="facts">
      <dt>chain</dt><dd>${esc(net.label || net.network || ctx.net())}
        ${net.testnet === false ? '<span class="chip gold">real money</span>'
          : '<span class="chip good">testnet</span>'}</dd>
      <dt>rpc</dt><dd>${net.ok ? `block ${esc(String(net.block))}`
        : `<span class="err">${esc(net.error || 'unreachable')}</span>`}</dd>
      <dt>solc</dt><dd>${esc(status.solc?.default || 'fetched on demand')}</dd>
      <dt>signs</dt><dd>${found
        ? `${esc(found.name)} <span class="chip" id="ready-bal">…</span>`
        : (ctx.address() ? '<span class="chip warn">no account yet</span>'
                         : '<span class="chip warn">not signed in</span>')}</dd>
    </dl>`;
    if (!found) return;
    try {
      const got = await call(`/balance?${q({ address: found.address, network: ctx.net() })}`);
      const chip = $('ready-bal');
      // The status this was drawn from can be two refreshes old by now; only
      // write into the chip that is still on screen.
      if (chip) {
        chip.textContent = `${money(got.balance)} ${got.symbol}`;
        chip.className = `chip ${Number(got.balance) > 0 ? 'good' : 'warn'}`;
        chip.title = Number(got.balance) > 0 ? found.address
          : `${found.address} has nothing to pay gas with on ${ctx.net()}`;
      }
    } catch { /* a balance is context, not a blocker */ }
  }

  /** An address on its own is a thing to copy. An address with the next two
   *  moves attached — go look at it, go call it — is the end of the job. */
  function renderDeployed(got) {
    const el = $('b-deployed');
    const failed = !!got.error || got.status === 'reverted' || !got.address;
    el.className = `deployed${failed ? ' failed' : ''}`;
    el.innerHTML = `
      <div class="top">
        <b>${failed ? 'deploy failed' : 'deployed'}</b>
        <span class="meta">${esc(got.contract || chosen?.name || '')} ·
          ${esc(got.network || ctx.net())}</span>
      </div>
      ${got.address ? `<div class="addr">${esc(got.address)}</div>` : ''}
      <div class="meta">${esc(got.hash || '')}${got.gas_used ? ` · ${got.gas_used} gas` : ''}</div>
      ${got.error ? `<div class="meta err">${esc(got.error)}</div>` : ''}
      <div class="row">
        ${got.address ? '<button class="tiny primary" id="dep-interact">interact →</button>' : ''}
        ${got.address ? '<button class="tiny ghost" id="dep-copy">copy address</button>' : ''}
        ${got.explorer ? `<a class="tiny" href="${esc(got.explorer)}" target="_blank" rel="noreferrer">explorer ↗</a>` : ''}
      </div>`;
    if (got.address) {
      $('dep-interact').onclick = () => ctx.openContract(got.address);
      $('dep-copy').onclick = () => {
        navigator.clipboard?.writeText(got.address); toast('address copied');
      };
    }
  }

  /* ── templates ────────────────────────────────────────────── */

  function fillTemplates(list) {
    const select = $('b-template');
    if (select.dataset.filled) return;
    select.innerHTML = '<option value="">start from a template…</option>'
      + list.map((t) => `<option value="${esc(t.name)}">${esc(t.name)} — ${esc(t.use || t.title || '')}</option>`).join('');
    select.dataset.filled = '1';
    select.onchange = async () => {
      if (!select.value) return;
      try {
        const got = await call(`/templates/${select.value}`);
        const path = `${got.contract || got.name}.sol`;
        files = { [path]: got.source || '' };
        file = path;
        editor.setValue(files[path]);
        if (!$('pj-name').value.trim()) $('pj-name').value = got.contract || got.name;
        project = null; shared = null; origin = null;
        compiled = null; chosen = null;
        $('b-contract').innerHTML = ''; $('ctor').innerHTML = '';
        markDirty(true);
        renderFiles();
        renderBanner();
        renderShare();
        select.value = '';
        toast(`${got.name} loaded — compile to see its constructor`);
      } catch (e) { toast(e.message, true); }
    };
  }

  function fillAccounts(accounts) {
    const select = $('b-account');
    const keep = select.value;
    select.innerHTML = accounts.length
      ? accounts.map((a) => `<option value="${esc(a.name)}">${esc(a.name)} — ${esc(short(a.address))}</option>`).join('')
      : '<option value="">no accounts yet</option>';
    if (keep) select.value = keep;
    select.onchange = () => { if (lastStatus) renderReady(lastStatus); };
  }

  /* ── wiring ───────────────────────────────────────────────── */

  $('pj-new').onclick = newProject;
  $('pj-save').onclick = save;
  $('pj-share').onclick = shareNow;
  $('pj-open').onclick = () => openCid($('pj-cid').value);
  $('pj-cid').onkeydown = (e) => { if (e.key === 'Enter') openCid($('pj-cid').value); };
  $('pj-name').oninput = () => markDirty(true);
  $('b-compile').onclick = compile;
  $('b-deploy').onclick = deploy;
  $('b-test').onclick = runTests;
  $('suite-generate').onclick = generateSuite;
  $('suite-run').onclick = runTests;

  renderFiles();
  renderBanner();
  renderChips();
  renderShare();

  return {
    /** Called by the shell whenever status is refreshed. */
    sync(status) {
      lastStatus = status;
      fillTemplates(status.templates || []);
      fillAccounts(status.accounts || []);
      const real = status.network?.testnet === false;
      $('b-net').textContent = ctx.net();
      $('b-net').className = real ? 'net-real' : 'net-test';
      $('act-bar').classList.toggle('real', real);
      renderReady(status);
      $('b-deploy').title = real
        ? `${ctx.net()} is not a testnet — this spends real money, and the deploy is refused without confirm`
        : `deploy to ${ctx.net()}`;
      renderChips();
      if (status.store) renderStore(status.store);
      loadProjects();
      loadRuns();
    },
    openCid,
    isDirty: () => dirty,
  };
}
