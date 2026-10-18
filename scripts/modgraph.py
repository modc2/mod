#!/usr/bin/env python3
"""Map every mod (core/* + orbit/*) and how they depend on each other.

Edge kinds, strongest evidence first:
  declared  config.json deps / dependencies / depends / forked_from / plays_in
  call      m.mod('x') · m.fn('x/..') · c.call('x/..') · self.m.mod('x')
  port      localhost:PORT / 127.0.0.1:PORT where PORT is another mod's port
  path      a literal 'orbit/x' or 'core/x' path in source
  submod    parent contains a nested mod with its own config.json

Writes to --out (default mod/core/docs/docs/modgraph/):
  modgraph.json   nodes + edges with evidence (file:line)
  modgraph.dot    graphviz
  modgraph.mmd    mermaid (edges between connected mods only)
  MODGRAPH.md     report: hubs, per-mod deps/dependents, cycles, unresolved
  modgraph.html   self-contained interactive graph (no CDN)

usage: python3 scripts/modgraph.py [--out DIR]
"""
import argparse, collections, json, os, re, sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'mod')
ROOT = os.path.normpath(ROOT)
ORBITS = ['core', 'orbit']
SKIP_DIRS = {'node_modules', '.next', 'out', 'dist', 'target', 'vendor', '.git', '__pycache__',
             'venv', '.venv', 'env', 'logs', '.turbo', 'coverage', '.cache', 'site-packages',
             'data', 'snapshots', '.pytest_cache', 'artifacts', 'cache', 'typechain-types'}
EXTS = {'.py', '.rs', '.ts', '.tsx', '.js', '.jsx', '.mjs', '.cjs', '.go', '.sh', '.toml', '.yml', '.yaml', '.html', '.cairo', '.sol', '.json'}
MAX_BYTES = 600_000
SHARED_PORTS = {3000, 8080, 8000, 5000, 3001, 80, 443, 8545, 59999}  # gateway / generic dev / test placeholder ports
PORT_OVERRIDES = {9000: 'server'}  # the activator (core/server/activator) fronts every slept mod on :9000
MIN_EDGE_PORT = 4000               # 3xxx Next dev ports get reused across mods; too noisy to attribute
# names too generic to trust as a bare path/port match
GENERIC = {'app', 'api', 'test', 'search', 'data', 'config', 'key', 'tool', 'base', 'edit', 'query', 'model',
           'server', 'local', 'tree', 'docs', 'web', 'mod', 'src', 'remote', 'content', 'asset', 'archive',
           'desc', 'skill', 'skills', 'cli', 'tx', 'v1', 'dev', 'tabs', 'id', 'safe', 'terminal'}

CALL_RE = re.compile(r"""\b(?:self\.)?(?:m|mod|c)\.(mod|module|fn|call|serve|connect)\(\s*f?['"]([A-Za-z0-9_.@-]+)(?:[/'"])""")
PORT_RE = re.compile(r'(?:localhost|127\.0\.0\.1|0\.0\.0\.0)[:"\', ]+(\d{4,5})\b')
PATH_RE = re.compile(r'(?<![A-Za-z0-9_])(orbit|core)/([A-Za-z0-9_@-]+)')


def load_json(p):
    try:
        with open(p) as f:
            return json.load(f)
    except Exception:
        return None


def discover():
    mods = {}  # name -> info
    for orbit in ORBITS:
        base = os.path.join(ROOT, orbit)
        for name in sorted(os.listdir(base)):
            d = os.path.join(base, name)
            if not os.path.isdir(d) or name.startswith(('.', '_')) or name == '__pycache__':
                continue
            cfg = load_json(os.path.join(d, 'config.json')) or {}
            if not isinstance(cfg, dict):
                cfg = {}
            if name in mods:  # core wins over orbit on a name collision
                mods[name].setdefault('shadowed', []).append(f'{orbit}/{name}')
                continue
            mods[name] = {'name': name, 'orbit': orbit, 'dir': d, 'cfg': cfg,
                          'description': (cfg.get('description') or '')[:200]}
    return mods


def ports_of(cfg):
    out = set()
    for k in ('port', 'app_port', 'mcp_port', 'api_port'):
        v = cfg.get(k)
        if isinstance(v, int):
            out.add(v)
        elif isinstance(v, str) and v.isdigit():
            out.add(int(v))
    ps = cfg.get('ports')
    if isinstance(ps, dict):
        ps = list(ps.values())
    if isinstance(ps, list):
        for v in ps:
            if isinstance(v, int):
                out.add(v)
    urls = cfg.get('urls') if isinstance(cfg.get('urls'), dict) else {}
    for u in (v for k, v in urls.items() if k in ('api', 'app', 'mcp')):  # 'gateway' urls point at someone else
        m = re.search(r'(?:localhost|127\.0\.0\.1):(\d{4,5})', str(u))
        if m:
            out.add(int(m.group(1)))
    return out - SHARED_PORTS


def find_submods(info):
    """Nested dirs carrying their own config.json with a name → submods."""
    subs = []
    root = info['dir']
    for dp, dns, fns in os.walk(root):
        dns[:] = [x for x in dns if x not in SKIP_DIRS and not x.startswith('.')]
        if dp == root:
            continue
        depth = os.path.relpath(dp, root).count(os.sep) + 1
        if depth > 3:
            dns[:] = []
            continue
        if 'config.json' in fns:
            cfg = load_json(os.path.join(dp, 'config.json'))
            if isinstance(cfg, dict) and cfg.get('name') and ('port' in cfg or 'fns' in cfg or 'description' in cfg):
                subs.append({'name': f"{info['name']}.{os.path.basename(dp)}", 'rel': os.path.relpath(dp, ROOT),
                             'port': cfg.get('port')})
    return subs


def resolve(target, mods, aliases):
    t = target.strip().lstrip('@') if target != '@ref' else target
    if t in mods:
        return t
    if t in aliases:
        return aliases[t]
    parts = t.split('.')
    if len(parts) > 1:
        if parts[0] in ('model', 'models') and parts[-1] in mods:
            return parts[-1]
        if parts[0] in mods:
            return parts[0]
        if parts[-1] in mods:
            return parts[-1]
    return None


def scan(mods):
    claims = collections.defaultdict(set)
    for n, info in mods.items():
        for p in ports_of(info['cfg']):
            claims[p].add(n)
    # a port claimed by two configs can't be attributed — skip it rather than guess
    port2mod = {p: next(iter(v)) for p, v in claims.items() if len(v) == 1 and p >= MIN_EDGE_PORT}
    port2mod.update({p: m for p, m in PORT_OVERRIDES.items() if m in mods})
    aliases = {}
    for n, info in mods.items():
        cn = info['cfg'].get('name')
        if isinstance(cn, str) and cn != n and cn not in mods:
            aliases[cn] = n
    # well-known internal names that live inside another mod
    aliases.update({'auth': 'server', 'server.auth': 'server', 'namespace': 'server', 'pm2': 'pm',
                    'meter': 'server', 'worker': 'server', 'gate': 'server', 'contracts': 'chain'})
    aliases = {k: v for k, v in aliases.items() if v in mods}

    edges = collections.defaultdict(lambda: {'kinds': collections.Counter(), 'evidence': []})
    unresolved = collections.Counter()

    def add(src, dst, kind, ev):
        if not dst or dst == src:
            return
        e = edges[(src, dst)]
        e['kinds'][kind] += 1
        if len(e['evidence']) < 6:
            e['evidence'].append(f'{kind}: {ev}')

    # declared
    for n, info in mods.items():
        cfg = info['cfg']
        for k in ('deps', 'dependencies', 'depends', 'forked_from', 'plays_in'):
            v = cfg.get(k)
            if v is None:
                continue
            names = v if isinstance(v, list) else list(v.keys()) if isinstance(v, dict) else [v]
            for t in names:
                if not isinstance(t, str):
                    continue
                t2 = t.split('/')[-1]
                r = resolve(t2, mods, aliases)
                if r:
                    add(n, r, 'declared', f'config.json {k}: {t}')
                elif k in ('deps', 'dependencies', 'depends'):
                    unresolved[f'{t} (declared by {n})'] += 1

    # code
    for n, info in mods.items():
        own_ports = ports_of(info['cfg'])
        root = info['dir']
        # don't let core/<x> scanning descend into orbit/; core has no nested orbit but be safe
        for dp, dns, fns in os.walk(root):
            rel_parts = os.path.relpath(dp, root).split(os.sep)
            dns[:] = [x for x in dns if x not in SKIP_DIRS and not x.startswith('.')]
            for fn in fns:
                ext = os.path.splitext(fn)[1]
                if ext not in EXTS or fn.endswith(('.min.js', '.map', 'package-lock.json', '.lock')):
                    continue
                if ext == '.json' and fn not in ('config.json', 'package.json', 'app.json', 'overrides.json'):
                    continue
                p = os.path.join(dp, fn)
                try:
                    if os.path.getsize(p) > MAX_BYTES:
                        continue
                    with open(p, errors='ignore') as f:
                        lines = f.readlines()
                except Exception:
                    continue
                relp = os.path.relpath(p, ROOT)
                in_tests = any(x in ('test', 'tests', '__tests__') for x in rel_parts) or fn.startswith('test_')
                for i, line in enumerate(lines, 1):
                    if len(line) > 2000:
                        continue
                    if ext == '.py' or ext in ('.sh',):
                        for verb, t in CALL_RE.findall(line):
                            r = resolve(t, mods, aliases)
                            if r:
                                add(n, r, 'call', f'{relp}:{i}')
                            elif not t.startswith(('{', 'self')) and '{' not in t:
                                unresolved[t] += 1
                    if not in_tests and ('localhost' in line or '127.0.0.1' in line or '0.0.0.0' in line):
                        for ps in PORT_RE.findall(line):
                            pi = int(ps)
                            if pi in own_ports:
                                continue
                            r = port2mod.get(pi)
                            if r:
                                add(n, r, 'port', f'{relp}:{i} :{pi}')
                    if 'orbit/' in line or 'core/' in line:
                        for orb, t in PATH_RE.findall(line):
                            if t in GENERIC and orb == 'orbit':
                                pass
                            if t in mods and mods[t]['orbit'] == orb and t not in GENERIC:
                                add(n, t, 'path', f'{relp}:{i}')
    return edges, unresolved, port2mod


def tarjan(nodes, adj):
    idx, low, on, st, out, c = {}, {}, set(), [], [], [0]
    sys.setrecursionlimit(10000)

    def sc(v):
        idx[v] = low[v] = c[0]; c[0] += 1; st.append(v); on.add(v)
        for w in adj.get(v, ()):
            if w not in idx:
                sc(w); low[v] = min(low[v], low[w])
            elif w in on:
                low[v] = min(low[v], idx[w])
        if low[v] == idx[v]:
            comp = []
            while True:
                w = st.pop(); on.discard(w); comp.append(w)
                if w == v:
                    break
            if len(comp) > 1:
                out.append(sorted(comp))
    for v in nodes:
        if v not in idx:
            sc(v)
    return out


KIND_ORDER = ['declared', 'call', 'port', 'path', 'submod']


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default=os.path.join(ROOT, 'core', 'docs', 'docs', 'modgraph'))
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)

    mods = discover()
    edges, unresolved, port2mod = scan(mods)
    submods = {}
    for n, info in mods.items():
        s = find_submods(info)
        if s:
            submods[n] = s

    E = []
    for (s, d), e in sorted(edges.items()):
        kinds = [k for k in KIND_ORDER if e['kinds'].get(k)]
        E.append({'source': s, 'target': d, 'kind': kinds[0], 'kinds': dict(e['kinds']),
                  'weight': sum(e['kinds'].values()), 'evidence': e['evidence']})
    out_deg = collections.Counter(e['source'] for e in E)
    in_deg = collections.Counter(e['target'] for e in E)
    N = []
    for n, info in sorted(mods.items()):
        cfg = info['cfg']
        N.append({'id': n, 'orbit': info['orbit'], 'description': info['description'],
                  'ports': sorted(ports_of(cfg)), 'deps_out': out_deg[n], 'dependents_in': in_deg[n],
                  'private': bool(cfg.get('private')), 'submods': [x['name'] for x in submods.get(n, [])],
                  'shadowed': info.get('shadowed', [])})
    adj = collections.defaultdict(set)
    for e in E:
        adj[e['source']].add(e['target'])
    cycles = tarjan(sorted(mods), adj)
    sadj = collections.defaultdict(set)
    for e in E:
        if 'declared' in e['kinds'] or 'call' in e['kinds']:
            sadj[e['source']].add(e['target'])
    strong_cycles = tarjan(sorted(mods), sadj)

    graph = {'root': ROOT, 'nodes': N, 'edges': E, 'submods': submods, 'cycles': cycles, 'strong_cycles': strong_cycles,
             'unresolved': dict(unresolved.most_common(60)),
             'port_collisions': {}, 'counts': {'mods': len(N), 'edges': len(E),
                                               'by_kind': dict(collections.Counter(e['kind'] for e in E))}}
    # port collisions: same port claimed by >1 mod
    claims = collections.defaultdict(list)
    for n, info in mods.items():
        for p in ports_of(info['cfg']):
            claims[p].append(n)
    graph['port_collisions'] = {str(p): v for p, v in sorted(claims.items()) if len(v) > 1}

    with open(os.path.join(a.out, 'modgraph.json'), 'w') as f:
        json.dump(graph, f, indent=1)

    # DOT
    colors = {'declared': '#2a78d6', 'call': '#eb6834', 'port': '#1baf7a', 'path': '#eda100'}
    with open(os.path.join(a.out, 'modgraph.dot'), 'w') as f:
        f.write('digraph mods {\n  rankdir=LR; node [shape=box, fontname="Helvetica", fontsize=10];\n')
        for nd in N:
            if nd['deps_out'] or nd['dependents_in']:
                fill = '#cde2fb' if nd['orbit'] == 'core' else '#ffffff'
                f.write(f'  "{nd["id"]}" [style=filled, fillcolor="{fill}"];\n')
        for e in E:
            f.write(f'  "{e["source"]}" -> "{e["target"]}" [color="{colors[e["kind"]]}", penwidth={min(1 + e["weight"] ** 0.5 / 2, 4):.1f}];\n')
        for p, subs in submods.items():
            for s in subs:
                f.write(f'  "{p}" -> "{s["name"]}" [style=dotted, arrowhead=odiamond];\n')
        f.write('}\n')

    # Mermaid — only declared+call edges keep it legible
    with open(os.path.join(a.out, 'modgraph.mmd'), 'w') as f:
        f.write('graph LR\n')
        safe = lambda s: re.sub(r'[^A-Za-z0-9_]', '_', s)
        for e in E:
            if e['kind'] in ('declared', 'call'):
                arrow = '==>' if e['kind'] == 'declared' else '-->'
                f.write(f'  {safe(e["source"])}["{e["source"]}"] {arrow} {safe(e["target"])}["{e["target"]}"]\n')

    write_report(a.out, graph, mods)
    write_html(a.out, graph)
    print(json.dumps(graph['counts']), '→', a.out)


def write_report(out, g, mods):
    N = {n['id']: n for n in g['nodes']}
    deps = collections.defaultdict(list)
    rdeps = collections.defaultdict(list)
    for e in g['edges']:
        deps[e['source']].append(e)
        rdeps[e['target']].append(e)
    L = ['# Mod dependency graph', '',
         f"Generated by `scripts/modgraph.py` over `{g['root']}` — {g['counts']['mods']} mods, "
         f"{g['counts']['edges']} edges ({', '.join(f'{k} {v}' for k, v in g['counts']['by_kind'].items())}).", '',
         'Edge kind = strongest evidence: **declared** (config.json deps) > **call** (`m.mod(\'x\')`, `m.fn(\'x/..\')`) '
         '> **port** (localhost:PORT owned by x) > **path** (literal `orbit/x`). Open `modgraph.html` for the interactive graph; '
         '`modgraph.json` has file:line evidence for every edge.', '']
    L += ['## Hubs — most depended-on', '', '| mod | orbit | dependents | top dependents |', '|---|---|---|---|']
    for n in sorted(N.values(), key=lambda x: -x['dependents_in'])[:30]:
        if not n['dependents_in']:
            break
        top = ', '.join(e['source'] for e in sorted(rdeps[n['id']], key=lambda e: -e['weight'])[:8])
        L.append(f"| {n['id']} | {n['orbit']} | {n['dependents_in']} | {top} |")
    L += ['', '## Most dependencies', '', '| mod | deps | deps list |', '|---|---|---|']
    for n in sorted(N.values(), key=lambda x: -x['deps_out'])[:25]:
        if not n['deps_out']:
            break
        L.append(f"| {n['id']} | {n['deps_out']} | {', '.join(e['target'] for e in sorted(deps[n['id']], key=lambda e: -e['weight']))} |")
    L += ['', '## Cycles (strongly connected groups)', '']
    L += ['Using declared + call edges only (the hard runtime/import loops):', '']
    L += [f'- {" ↔ ".join(c)}' for c in g['strong_cycles']] or ['none']
    L += ['', 'Including port + path edges:', '']
    L += [f'- {" ↔ ".join(c)}' for c in g['cycles']] or ['none']
    L += ['', '## Submods (nested mods with their own config.json)', '']
    for p, subs in sorted(g['submods'].items()):
        L.append(f"- **{p}**: {', '.join(s['name'].split('.', 1)[1] for s in subs)}")
    L += ['', '## Port collisions (one port claimed by several configs)', '']
    L += [f'- :{p} — {", ".join(v)}' for p, v in g['port_collisions'].items()] or ['none']
    isolated = [n['id'] for n in N.values() if not n['deps_out'] and not n['dependents_in']]
    L += ['', f'## Standalone mods ({len(isolated)}) — no edges either way', '', ', '.join(isolated)]
    L += ['', '## Unresolved references (named in code/config, no such mod)', '']
    L += [f'- `{k}` ×{v}' for k, v in g['unresolved'].items()] or ['none']
    L += ['', '## Every mod', '', '| mod | orbit | depends on | used by |', '|---|---|---|---|']
    for n in sorted(N.values(), key=lambda x: x['id']):
        d = ', '.join(f"{e['target']}<sub>{'h' if e['kind'] == 'path' else e['kind'][0]}</sub>" for e in sorted(deps[n['id']], key=lambda e: e['target']))
        r = ', '.join(e['source'] for e in sorted(rdeps[n['id']], key=lambda e: e['source']))
        L.append(f"| {n['id']} | {n['orbit']} | {d or '—'} | {r or '—'} |")
    L += ['', 'Kind subscripts: d=declared c=call p=port h=path.']
    with open(os.path.join(out, 'MODGRAPH.md'), 'w') as f:
        f.write('\n'.join(L) + '\n')


HTML = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>mod graph</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{--bg:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--line:#e4e3df;--k1:#2a78d6;--k2:#eb6834;--k3:#1baf7a;--k4:#eda100;--core:#cde2fb}
@media (prefers-color-scheme:dark){:root{--bg:#1a1a19;--ink:#fff;--ink2:#c3c2b7;--line:#383835;--k1:#3987e5;--k2:#d95926;--k3:#199e70;--k4:#c98500;--core:#184f95}}
*{box-sizing:border-box}body{margin:0;font:13px/1.4 system-ui,sans-serif;background:var(--bg);color:var(--ink);display:flex;height:100vh}
#side{width:320px;border-right:1px solid var(--line);padding:12px;overflow:auto}#main{flex:1;position:relative}
svg{width:100%;height:100%;display:block}input,select{width:100%;padding:6px;margin:4px 0 8px;background:transparent;color:var(--ink);border:1px solid var(--line);border-radius:4px}
.lg{display:flex;align-items:center;gap:6px;margin:2px 0;cursor:pointer;user-select:none}.lg i{width:18px;height:2px;display:inline-block}
.lg.off{opacity:.35}h3{margin:12px 0 4px;font-size:12px;letter-spacing:.04em;color:var(--ink2);text-transform:uppercase}
.row{cursor:pointer;padding:1px 0}.row:hover{text-decoration:underline}.mut{color:var(--ink2)}
#tip{position:absolute;pointer-events:none;background:var(--bg);border:1px solid var(--line);border-radius:4px;padding:6px 8px;display:none;max-width:320px;box-shadow:0 2px 8px #0002}
text{fill:var(--ink);font-size:10px;pointer-events:none}
</style></head><body>
<div id="side">
<b>mod dependency graph</b> <span class="mut" id="cnt"></span>
<input id="q" placeholder="focus a mod (name)…">
<label class="mut">depth around focus</label><select id="depth"><option selected>1</option><option>2</option><option>3</option><option value="99">all</option></select>
<label><input type="checkbox" id="iso" style="width:auto"> show standalone mods</label>
<h3>edge kind (click to toggle)</h3><div id="legend"></div>
<h3>selected</h3><div id="info" class="mut">click a node</div>
<h3>hubs</h3><div id="hubs"></div>
</div>
<div id="main"><svg id="svg"></svg><div id="tip"></div></div>
<script>
const G = __DATA__;
const KC={declared:'var(--k1)',call:'var(--k2)',port:'var(--k3)',path:'var(--k4)'};
const KL={declared:'declared in config.json',call:"m.mod('x') / m.fn('x/…')",port:'localhost:PORT of x',path:"literal 'orbit/x' path"};
const on={declared:1,call:1,port:1,path:1};
const byId={};G.nodes.forEach(n=>byId[n.id]=n);
document.getElementById('cnt').textContent=`· ${G.counts.mods} mods · ${G.counts.edges} edges`;
const lg=document.getElementById('legend');
for(const k in KC){const d=document.createElement('div');d.className='lg';d.innerHTML=`<i style="background:${KC[k]}"></i>${k} <span class=mut>— ${KL[k]} (${G.counts.by_kind[k]||0})</span>`;d.onclick=()=>{on[k]^=1;d.classList.toggle('off');build()};lg.appendChild(d)}
document.getElementById('hubs').innerHTML=[...G.nodes].sort((a,b)=>b.dependents_in-a.dependents_in).slice(0,15).map(n=>`<div class=row data-id="${n.id}">${n.id} <span class=mut>← ${n.dependents_in}</span></div>`).join('');
document.querySelectorAll('#hubs .row').forEach(r=>r.onclick=()=>focus(r.dataset.id));
let focusId=null;
function focus(id){focusId=id;document.getElementById('q').value=id||'';build();showInfo(id)}
document.getElementById('q').onchange=e=>focus(byId[e.target.value.trim()]?e.target.value.trim():null);
document.getElementById('depth').onchange=build;document.getElementById('iso').onchange=build;
function showInfo(id){const el=document.getElementById('info');if(!id){el.textContent='click a node';return}
 const n=byId[id];const out=G.edges.filter(e=>e.source==id),inn=G.edges.filter(e=>e.target==id);
 const li=(e,o)=>`<div class=row data-id="${o}"><i style="display:inline-block;width:10px;height:2px;background:${KC[e.kind]};vertical-align:middle"></i> ${o} <span class=mut>${e.kind} ×${e.weight}</span></div>`;
 el.innerHTML=`<b>${id}</b> <span class=mut>${n.orbit}${n.ports.length?' · :'+n.ports.join(' :'):''}</span><div class=mut style="margin:4px 0">${(n.description||'').replace(/</g,'&lt;')}</div>`+
 (n.submods.length?`<div class=mut>submods: ${n.submods.join(', ')}</div>`:'')+
 `<h3>depends on (${out.length})</h3>${out.map(e=>li(e,e.target)).join('')||'—'}<h3>used by (${inn.length})</h3>${inn.map(e=>li(e,e.source)).join('')||'—'}`;
 el.querySelectorAll('.row').forEach(r=>r.onclick=()=>focus(r.dataset.id));}
const svg=document.getElementById('svg'),tip=document.getElementById('tip');
let sim=null;
function build(){
 const E=G.edges.filter(e=>on[e.kind]);
 let keep=new Set();
 if(focusId){const D=+document.getElementById('depth').value;let fr=new Set([focusId]);keep.add(focusId);
  for(let i=0;i<D;i++){const nx=new Set();E.forEach(e=>{if(fr.has(e.source)&&!keep.has(e.target))nx.add(e.target);if(fr.has(e.target)&&!keep.has(e.source))nx.add(e.source)});nx.forEach(x=>keep.add(x));fr=nx;if(!nx.size)break}}
 else{E.forEach(e=>{keep.add(e.source);keep.add(e.target)});if(document.getElementById('iso').checked)G.nodes.forEach(n=>keep.add(n.id))}
 const nodes=[...keep].map(id=>({id,n:byId[id],x:Math.random()*800,y:Math.random()*600,vx:0,vy:0}));
 const idx={};nodes.forEach((d,i)=>idx[d.id]=i);
 const links=E.filter(e=>e.source in idx&&e.target in idx).map(e=>({e,s:nodes[idx[e.source]],t:nodes[idx[e.target]]}));
 draw(nodes,links);
}
function draw(nodes,links){
 const W=svg.clientWidth,H=svg.clientHeight;svg.setAttribute('viewBox',`0 0 ${W} ${H}`);
 svg.innerHTML=`<defs>${Object.keys(KC).map(k=>`<marker id="a-${k}" viewBox="0 0 10 10" refX="10" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M0,0L10,5L0,10z" style="fill:${KC[k]}"/></marker>`).join('')}</defs><g id="vp"></g>`;
 const vp=svg.querySelector('#vp');
 const deg={};links.forEach(l=>{deg[l.t.id]=(deg[l.t.id]||0)+1});
 const R=d=>4+Math.min(14,Math.sqrt(d.n.dependents_in)*2);
 const le=links.map(l=>{const p=document.createElementNS('http://www.w3.org/2000/svg','line');p.setAttribute('stroke',KC[l.e.kind]);p.setAttribute('stroke-opacity',.55);p.setAttribute('stroke-width',Math.min(1+Math.sqrt(l.e.weight)/3,3));p.setAttribute('marker-end',`url(#a-${l.e.kind})`);vp.appendChild(p);return p});
 const ne=nodes.map(d=>{const g=document.createElementNS('http://www.w3.org/2000/svg','g');const c=document.createElementNS('http://www.w3.org/2000/svg','circle');
  c.setAttribute('r',R(d));c.setAttribute('fill',d.n.orbit=='core'?'var(--core)':'var(--bg)');c.setAttribute('stroke',d.id==focusId?'var(--k2)':'var(--ink2)');c.setAttribute('stroke-width',d.id==focusId?3:1.2);
  const t=document.createElementNS('http://www.w3.org/2000/svg','text');t.textContent=d.id;t.setAttribute('dx',R(d)+3);t.setAttribute('dy',3);
  g.appendChild(c);if(nodes.length<140||d.n.dependents_in>=4||d.id==focusId)g.appendChild(t);g.style.cursor='pointer';
  g.onmouseenter=ev=>{tip.style.display='block';tip.innerHTML=`<b>${d.id}</b> <span class=mut>${d.n.orbit}</span><br>depends on ${d.n.deps_out} · used by ${d.n.dependents_in}<br><span class=mut>${(d.n.description||'').slice(0,140).replace(/</g,'&lt;')}</span>`};
  g.onmousemove=ev=>{const r=svg.parentNode.getBoundingClientRect();tip.style.left=(ev.clientX-r.left+14)+'px';tip.style.top=(ev.clientY-r.top+14)+'px'};
  g.onmouseleave=()=>tip.style.display='none';g.onclick=()=>focus(d.id);vp.appendChild(g);return g});
 // simple force layout
 const k=Math.min(150,Math.max(40,Math.sqrt(W*H/Math.max(nodes.length,1))*1.1));let it=0;if(sim)cancelAnimationFrame(sim);
 if(focusId){const f=nodes.find(d=>d.id==focusId);if(f){f.x=W/2;f.y=H/2}}
 function step(){const a=Math.max(.02,1-it/300);
  for(let i=0;i<nodes.length;i++){const p=nodes[i];for(let j=i+1;j<nodes.length;j++){const q=nodes[j];let dx=p.x-q.x,dy=p.y-q.y,d2=dx*dx+dy*dy+.01;if(d2>k*k*16)continue;const f=k*k/d2*1.6;p.vx+=dx*f*.01;p.vy+=dy*f*.01;q.vx-=dx*f*.01;q.vy-=dy*f*.01}}
  links.forEach(l=>{const dx=l.t.x-l.s.x,dy=l.t.y-l.s.y,d=Math.sqrt(dx*dx+dy*dy)+.01,f=(d-k)/d*.05;l.s.vx+=dx*f;l.s.vy+=dy*f;l.t.vx-=dx*f;l.t.vy-=dy*f});
  nodes.forEach(p=>{p.vx+=(W/2-p.x)*.0012;p.vy+=(H/2-p.y)*.0012;if(p.id==focusId){p.vx=p.vy=0;p.x=W/2;p.y=H/2}p.x+=Math.max(-20,Math.min(20,p.vx*a));p.y+=Math.max(-20,Math.min(20,p.vy*a));p.vx*=.6;p.vy*=.6;p.x=Math.max(10,Math.min(W-60,p.x));p.y=Math.max(10,Math.min(H-10,p.y))});
  links.forEach((l,i)=>{const dx=l.t.x-l.s.x,dy=l.t.y-l.s.y,d=Math.sqrt(dx*dx+dy*dy)||1,r=R(l.t)+2;le[i].setAttribute('x1',l.s.x);le[i].setAttribute('y1',l.s.y);le[i].setAttribute('x2',l.t.x-dx/d*r);le[i].setAttribute('y2',l.t.y-dy/d*r)});
  nodes.forEach((p,i)=>ne[i].setAttribute('transform',`translate(${p.x},${p.y})`));
  if(++it<600)sim=requestAnimationFrame(step)}
 step();
}
window.onresize=build;build();
</script></body></html>"""


def write_html(out, g):
    slim = {'nodes': g['nodes'], 'counts': g['counts'],
            'edges': [{k: e[k] for k in ('source', 'target', 'kind', 'weight')} for e in g['edges']]}
    with open(os.path.join(out, 'modgraph.html'), 'w') as f:
        f.write(HTML.replace('__DATA__', json.dumps(slim)))


if __name__ == '__main__':
    main()
