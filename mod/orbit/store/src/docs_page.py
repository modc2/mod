"""
The manual, as a page.

`docs.py` is the manual as data, which is the right shape for the console and
for the `store_docs` tool and the wrong shape for a person who typed the URL
into a browser. This renders that same dictionary as one self-contained HTML
document — no stylesheet to fetch, no script, no build step — so `/docs` can
answer a browser with something readable and a program with JSON, and neither
of them has to know the other exists.

There is nothing here that decides *what* the documentation says. Every string
on the rendered page came out of `docs.document()`, so a route that changes is
still wrong in exactly one place. This file only decides what it looks like.
"""
import html

from . import docs

STYLE = """
:root{--bg:#0b0d11;--panel:#141821;--line:#242936;--ink:#e9edf5;--dim:#8d97a9;
  --accent:#6ee7b7;--warn:#fbbf24;--bad:#f87171}
@media (prefers-color-scheme: light){
  :root{--bg:#f5f7fb;--panel:#fff;--line:#e3e7ef;--ink:#141824;--dim:#5f6982;
    --accent:#0d9a6c;--warn:#a86a00;--bad:#c33}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font:15px/1.6 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
.wrap{max-width:860px;margin:0 auto;padding:36px 22px 90px}
code,pre,.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:13px}
h1{font-size:26px;margin:0 0 6px}
h2{font-size:18px;margin:38px 0 12px;padding-bottom:8px;
  border-bottom:1px solid var(--line)}
h3{font-size:14px;margin:22px 0 8px;color:var(--dim);
  text-transform:uppercase;letter-spacing:.08em}
p{margin:10px 0}
.lede{color:var(--dim);margin:0 0 4px}
a{color:var(--accent)}
.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;
  padding:16px 18px;margin:12px 0}
.card h4{margin:0 0 4px;font-size:15px}
.dim{color:var(--dim)}
pre{background:var(--panel);border:1px solid var(--line);border-radius:10px;
  padding:12px 14px;overflow-x:auto;margin:10px 0}
table{border-collapse:collapse;width:100%;margin:10px 0;font-size:14px}
td,th{text-align:left;padding:7px 10px;border-bottom:1px solid var(--line);
  vertical-align:top}
th{color:var(--dim);font-weight:600;font-size:12px;text-transform:uppercase;
  letter-spacing:.06em}
td.k{white-space:nowrap;width:1%}
.verb{display:inline-block;min-width:52px;padding:1px 7px;border-radius:5px;
  font-size:11px;font-weight:700;background:var(--line);color:var(--dim)}
.verb.GET{color:var(--accent)}
.verb.POST{color:var(--warn)}
.verb.DELETE{color:var(--bad)}
.warn{border-left:3px solid var(--warn);padding-left:12px;color:var(--dim)}
nav{display:flex;flex-wrap:wrap;gap:8px;margin:18px 0 4px}
nav a{background:var(--panel);border:1px solid var(--line);border-radius:999px;
  padding:5px 13px;text-decoration:none;font-size:13px}
"""


def _e(value):
    return html.escape(str(value if value is not None else ''))


def _rows(pairs):
    return '\n'.join(
        f'<tr><td class="k mono">{_e(k)}</td><td>{_e(v)}</td></tr>'
        for k, v in pairs)


def _sharing(block):
    ways = ''.join(
        f'<div class="card"><h4>{_e(way["name"])}</h4>'
        f'<p class="dim">{_e(way["audience"])}</p>'
        f'<table>{_rows([("page", way["page"]), ("bytes", way["bytes"]),("credential", way["credential"]),("expiry", way["expiry"])])}</table>'
        f'<p class="warn">{_e(way["note"])}</p></div>'
        for way in block['ways'])
    return (f'<h2 id="sharing">{_e(block["title"])}</h2>'
            f'<p class="lede">{_e(block["summary"])}</p>{ways}'
            f'<h3>page versus bytes</h3><p>{_e(block["page_vs_bytes"])}</p>'
            f'<h3>why it can only be used once</h3>'
            f'<p>{_e(block["one_time_mechanism"])}</p>'
            f'<p>{_e(block["spent_codes"])}</p>')


def _naming(block):
    forms = '\n'.join(
        f'<tr><td class="k">{_e(row["form"])}</td>'
        f'<td class="mono">{_e(row["example"])}</td>'
        f'<td class="dim">{_e(row.get("note", ""))}</td></tr>'
        for row in block['a_picture'])
    durations = '\n'.join(
        f'<tr><td class="k">{_e(row["form"])}</td>'
        f'<td class="mono">{_e(row["example"])}</td><td></td></tr>'
        for row in block['a_duration'])
    return (f'<h2 id="naming">{_e(block["title"])}</h2>'
            f'<h3>naming a picture</h3><table>{forms}</table>'
            f'<p class="warn">{_e(block["ambiguity"])}</p>'
            f'<h3>saying how long</h3><table>{durations}</table>'
            f'<p class="dim">{_e(block["duration_bounds"])}</p>'
            f'<h3>naming a code</h3><p>{_e(block["a_code"])}</p>')


def _endpoints(rows):
    body = '\n'.join(
        f'<tr><td class="k"><span class="verb {_e(row["method"])}">'
        f'{_e(row["method"])}</span></td>'
        f'<td class="k mono">{_e(row["path"])}</td>'
        f'<td>{_e(row["what"])}'
        + ('<span class="dim"> · yours</span>' if row.get('auth') else '')
        + '</td></tr>'
        for row in rows)
    return ('<h2 id="http">Every endpoint</h2>'
            '<table><tr><th></th><th>path</th><th>what</th></tr>'
            f'{body}</table>')


def _mcp(block):
    tools = '\n'.join(
        f'<tr><td class="k mono">{_e(tool["name"])}</td>'
        f'<td>{_e(tool["description"])}</td></tr>'
        for tool in block['tools'])
    import json
    config = _e(json.dumps(block['client_config'], indent=2))
    return ('<h2 id="mcp">For an agent</h2>'
            f'<p>{_e(block["instructions"])}</p>'
            '<h3>connecting</h3>'
            f'<table>{_rows([("http", block["http"]),("stdio", block["stdio"]),("schema", block["schema_url"])])}</table>'
            f'<pre>{config}</pre>'
            f'<h3>{len(block["tools"])} tools</h3>'
            f'<table>{tools}</table>'
            + _mcp_extras(block))


def _mcp_extras(block):
    out = ''
    if block.get('prompts'):
        rows = '\n'.join(
            f'<tr><td class="k mono">{_e(p["name"])}</td>'
            f'<td>{_e(p["description"])}</td></tr>' for p in block['prompts'])
        out += f'<h3>prompts</h3><table>{rows}</table>'
    if block.get('resources'):
        rows = '\n'.join(
            f'<tr><td class="k mono">{_e(r["uriTemplate"])}</td>'
            f'<td>{_e(r["description"])}</td></tr>'
            for r in block['resources'])
        out += f'<h3>resources</h3><table>{rows}</table>'
    for key, heading in (('batching', 'batching'),
                         ('pictures_come_back_as_pictures',
                          'pictures come back as pictures')):
        if block.get(key):
            out += f'<h3>{heading}</h3><p>{_e(block[key])}</p>'
    return out


def _cli(rows):
    body = '\n'.join(
        f'<tr><td class="k mono">{_e(row["command"])}</td>'
        f'<td>{_e(row["what"])}</td></tr>' for row in rows)
    return f'<h2 id="cli">Command line</h2><table>{body}</table>'


def _safety(block):
    return ('<h2 id="safety">What it refuses, and what it cannot promise</h2>'
            '<table>'
            + _rows([(k, v) for k, v in block.items()])
            + '</table>')


def _env(rows):
    body = '\n'.join(
        f'<tr><td class="k mono">{_e(row["name"])}</td><td>{_e(row["what"])}'
        f'<br><span class="dim mono">{_e(row["value"])}</span></td></tr>'
        for row in rows)
    return f'<h2 id="env">Environment</h2><table>{body}</table>'


def render(section: str = '') -> str:
    """The whole manual as one page, or one section of it."""
    document = docs.document()
    if section and section not in document:
        raise docs.library.StoreError(f'no such section: {section}', 404)

    wanted = [section] if section else ['sharing', 'naming', 'endpoints',
                                        'mcp', 'cli', 'safety', 'env']
    renderers = {'sharing': _sharing, 'naming': _naming,
                 'endpoints': _endpoints, 'mcp': _mcp, 'cli': _cli,
                 'safety': _safety, 'env': _env}
    body = ''.join(renderers[name](document[name])
                   for name in wanted if name in renderers)

    nav = '' if section else (
        '<nav>' + ''.join(
            f'<a href="#{anchor}">{label}</a>' for anchor, label in
            (('sharing', 'Two ways to share'), ('naming', 'Naming things'),
             ('http', 'HTTP'),
             ('mcp', 'Agents · MCP'), ('cli', 'Command line'),
             ('safety', 'Safety'), ('env', 'Environment')))
        + '</nav>')

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="dark light">
<title>store — the manual</title>
<style>{STYLE}</style></head>
<body><div class="wrap">
<h1>store</h1>
<p class="lede">{_e(document['what'])}</p>
<p class="dim mono">{_e(document['share_base'])} · state {_e(document['state'])}</p>
{nav}
{body}
<h2>This same page as data</h2>
<p>Everything above came out of one dictionary. A program should read that
rather than this: <code>GET /docs</code> with an
<code>Accept: application/json</code> header, <code>python3 mod.py docs</code>,
or the <code>store_docs</code> tool. Add <code>?section=</code> to any of them
for one part.</p>
</div></body></html>"""
