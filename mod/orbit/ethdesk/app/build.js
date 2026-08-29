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
  let dirty = false;
  let storeState = null;

  const editor = new Editor($('editor'), {
    value: STARTER,
    onChange: (text) => { files[file] = text; markDirty(true); },
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
      renderShare();
      loadProjects();
      loadRuns();
    } catch (e) { toast(e.message, true); }
  }

  function newProject() {
    project = null; shared = null;
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
    renderShare();
    loadProjects();
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
    const body = { name: name || undefined, files, entry: file, tests: tests || [] };
    try {
      const got = project
        ? await call(`/projects/${project.id}`, { method: 'PUT', json: body })
        : await call('/projects', { method: 'POST', json: body });
      project = got;
      $('pj-name').value = got.name;
      markDirty(false);
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
        <div class="note"><b>you are reading somebody else's project</b>
          <span class="hint">Opened from the store by CID. Fork it to get your
          own copy — yours to edit, yours to deploy.</span></div>
        <dl class="facts">
          <dt>name</dt><dd>${esc(shared.name)}</dd>
          <dt>author</dt><dd class="mono">${esc(shared.author || 'unknown')}</dd>
          <dt>cid</dt><dd class="mono wrap">${esc(shared.cid)}</dd>
        </dl>
        <div class="row"><button class="primary" id="pj-fork">fork into my workspace</button></div>`;
      $('pj-fork').onclick = async () => {
        try {
          const got = await call('/fork', { method: 'POST', json: { cid: shared.cid } });
          toast(`forked as ${got.name}`);
          await loadProjects();
          openProject(got.id);
        } catch (e) { toast(e.message, true); }
      };
      return;
    }
    if (!project) {
      el.innerHTML = `<p class="hint">Nothing to share yet. A project gets a
        CID the moment it reaches the store; the CID <em>is</em> the share —
        content-addressed, so what somebody opens is exactly what you saved,
        and a later edit cannot change it under them.</p>`;
      return;
    }
    const links = project.cid ? {
      open: `${location.origin}${location.pathname}?open=${project.cid}`,
      store: `/api/store/get?cid=${project.cid}`,
    } : null;
    el.innerHTML = `
      <dl class="facts">
        <dt>project</dt><dd>${esc(project.name)}</dd>
        <dt>cid</dt><dd class="mono wrap">${project.cid
          ? esc(project.cid) : '<span class="warn-t">not in the store yet</span>'}</dd>
        <dt>visible</dt><dd>${project.public ? 'public — anyone with the CID'
          : 'private — only you'}</dd>
      </dl>
      ${links ? `<div class="row wrap">
        <input class="grow mono" id="share-link" readonly value="${esc(links.open)}">
        <button class="tiny ghost" id="share-copy">copy link</button>
        <button class="tiny ghost" id="share-copy-cid">copy CID</button>
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
    if (links) {
      $('share-copy').onclick = () => copy(links.open, 'link');
      $('share-copy-cid').onclick = () => copy(project.cid, 'CID');
    }
    $('share-go').onclick = async () => {
      try {
        const path = project.public ? 'unshare' : 'share';
        const got = await call(`/projects/${project.id}/${path}`, { method: 'POST' });
        project = { ...project, public: got.public, cid: got.cid || project.cid };
        toast(got.public ? 'published — the CID is the link' : 'made private');
        renderShare();
        loadProjects();
      } catch (e) { toast(e.message, true); }
    };
    el.querySelectorAll('[data-ver]').forEach((b) => {
      b.onclick = () => openCid(b.dataset.ver);
    });
  }

  async function openCid(cid) {
    cid = (cid || '').trim();
    if (!cid) return;
    try {
      const got = await call(`/open?${q({ cid })}`);
      shared = got;
      project = null;
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
      renderShare();
      showResult('res-share');
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
    if (!chosen) { toast('compile first — I need to know which contract', true); return; }
    const account = $('b-account').value;
    if (!account) { toast('you need an account to sign this', true); return; }
    showResult('res-out');
    out('b-out', `deploying ${chosen.name} to ${ctx.net()}…`);
    try {
      const got = await call('/deploy', { method: 'POST', json: {
        sources: files, contract: chosen.name, args: ctorArgs(),
        account, password: $('b-pw').value || undefined,
        network: ctx.net(), optimize: $('b-optimize').checked,
        confirm: $('b-confirm').checked,
        note: project ? `project ${project.slug}` : undefined,
      }});
      out('b-out', `<span class="${got.error ? 'err' : 'ok'}">${esc(got.status || 'sent')}</span> `
        + `${esc(got.contract || '')}\n`
        + (got.address ? `<b>${esc(got.address)}</b>\n` : '')
        + `${esc(got.hash || '')}\n`
        + (got.gas_used ? `${got.gas_used} gas\n` : '')
        + (got.explorer ? `<a href="${esc(got.explorer)}" target="_blank" rel="noreferrer">${esc(got.explorer)}</a>` : '')
        + (got.error ? `\n<span class="err">${esc(got.error)}</span>` : ''));
      ctx.onActivity();
    } catch (e) {
      out('b-out', `<span class="err">${esc(e.message)}</span>`, 'has-error');
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
        project = null; shared = null; compiled = null; chosen = null;
        $('b-contract').innerHTML = ''; $('ctor').innerHTML = '';
        markDirty(true);
        renderFiles();
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
  }

  /* ── wiring ───────────────────────────────────────────────── */

  $('pj-new').onclick = newProject;
  $('pj-save').onclick = save;
  $('pj-share').onclick = () => { showResult('res-share'); renderShare(); };
  $('pj-open').onclick = () => openCid($('pj-cid').value);
  $('pj-cid').onkeydown = (e) => { if (e.key === 'Enter') openCid($('pj-cid').value); };
  $('pj-name').oninput = () => markDirty(true);
  $('b-compile').onclick = compile;
  $('b-deploy').onclick = deploy;
  $('b-test').onclick = runTests;
  $('suite-generate').onclick = generateSuite;
  $('suite-run').onclick = runTests;

  renderFiles();
  renderShare();

  return {
    /** Called by the shell whenever status is refreshed. */
    sync(status) {
      fillTemplates(status.templates || []);
      fillAccounts(status.accounts || []);
      $('b-net').textContent = ctx.net();
      $('b-net').className = status.network?.testnet === false ? 'net-real' : 'net-test';
      if (status.store) renderStore(status.store);
      loadProjects();
      loadRuns();
    },
    openCid,
    isDirty: () => dirty,
  };
}
