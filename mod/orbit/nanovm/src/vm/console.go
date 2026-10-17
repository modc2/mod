package vm

// The console is one embedded page, no build step, no framework. It computes
// its API base from its own URL so it works both direct (:51230/nanovm/) and
// behind the gateway, where the module is mounted under /nanovm/.
const consoleHTML = `<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>nanovm</title>
<style>
:root{--bg:#0b0e14;--panel:#121722;--line:#1f2736;--fg:#d7dde8;--dim:#7d8799;--acc:#5ad7a7;--bad:#e06c75;--mono:ui-monospace,SFMono-Regular,Menlo,monospace}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.5 var(--mono)}
header{display:flex;align-items:baseline;gap:12px;padding:16px 20px;border-bottom:1px solid var(--line)}
header h1{font-size:16px;margin:0;letter-spacing:.08em}header .v{color:var(--dim)}
main{max-width:980px;margin:0 auto;padding:20px}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:14px 16px;margin-bottom:16px}
h2{font-size:12px;letter-spacing:.12em;color:var(--dim);margin:0 0 10px;text-transform:uppercase}
table{width:100%;border-collapse:collapse}td,th{padding:6px 8px;text-align:left;border-top:1px solid var(--line);font-size:13px}
th{color:var(--dim);border-top:0;font-weight:normal}
.run{color:var(--acc)}.exit{color:var(--dim)}
button{background:transparent;border:1px solid var(--line);color:var(--fg);border-radius:5px;padding:3px 10px;font:12px var(--mono);cursor:pointer}
button:hover{border-color:var(--acc);color:var(--acc)}button.del:hover{border-color:var(--bad);color:var(--bad)}
input{background:var(--bg);border:1px solid var(--line);color:var(--fg);border-radius:5px;padding:6px 8px;font:13px var(--mono)}
form{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
pre{background:var(--bg);border:1px solid var(--line);border-radius:6px;padding:10px;overflow:auto;max-height:280px;white-space:pre-wrap}
.err{color:var(--bad)}.hint{color:var(--dim);font-size:12px;margin-top:8px}
</style></head><body>
<header><h1>NANOVM</h1><span class="v" id="meta">...</span></header>
<main>
<div class="panel"><h2>Boot</h2>
<form id="boot">
<input name="name" placeholder="name" required size="10">
<input name="cmd" placeholder='cmd, e.g. sleep 300' required size="28">
<input name="mem" placeholder="mem (64M)" size="8">
<input name="cpu" placeholder="cpu (0.5)" size="7">
<input name="rootfs" placeholder="rootfs (blank = host, read-only)" size="24">
<label><input type="checkbox" name="net"> net</label>
<button>boot</button>
</form>
<div class="hint">Blank rootfs boots a read-only lens over the host with a private /proc and /tmp. Point rootfs at a directory (docker export, debootstrap, <code>nanovm rootfs</code>) to pivot into it.</div>
<div id="bootmsg" class="hint"></div></div>
<div class="panel"><h2>Machines</h2><table id="vms"><thead><tr><th>name</th><th>status</th><th>pid</th><th>cmd</th><th>caps</th><th></th></tr></thead><tbody></tbody></table></div>
<div class="panel"><h2>Output</h2>
<form id="execbar" style="margin-bottom:10px">
<input id="execname" placeholder="vm" readonly size="10">
<input id="execcmd" placeholder='command to run inside, e.g. ps aux' size="40">
<button>run inside</button>
</form>
<pre id="out">select a vm's logs or exec to see output here</pre></div>
</main>
<script>
const base = location.pathname.replace(/nanovm\/?$/, '');
const api = p => fetch(base + p.replace(/^\//,''));
const $ = s => document.querySelector(s);
async function j(p, opt){ const r = await fetch(base + p.replace(/^\//,''), opt); const b = await r.json(); if(!r.ok) throw new Error(b.error||r.status); return b; }
async function refresh(){
  try{
    const h = await j('health');
    $('#meta').textContent = 'v0.1.0 - ' + h.running + '/' + h.vms + ' running - ' + h.backend;
    const d = await j('vms'); const tb = $('#vms tbody'); tb.innerHTML='';
    for(const v of d.vms){
      const tr = document.createElement('tr'); const s = v.spec;
      const caps = [s.mem, s.cpu?s.cpu+'c':'', s.pids?s.pids+'p':'', s.net?'net':'no-net'].filter(Boolean).join(' ');
      tr.innerHTML = '<td>'+s.name+'</td><td class="'+(v.status==='running'?'run':'exit')+'">'+v.status+(v.status==='exited'?' ('+v.exit_code+')':'')+'</td>'+
        '<td>'+(v.status==='running'?v.pid:'-')+'</td><td>'+s.cmd.join(' ')+'</td><td>'+caps+'</td><td></td>';
      const td = tr.lastChild;
      for(const [label, fn] of [['logs',()=>logs(s.name)],['exec',()=>execIn(s.name)],['stop',()=>act('vms/'+s.name+'/stop','POST')],['rm',()=>act('vms/'+s.name,'DELETE')]]){
        const b = document.createElement('button'); b.textContent = label; if(label==='rm') b.className='del'; b.onclick = fn; td.appendChild(b); td.appendChild(document.createTextNode(' '));
      }
      tb.appendChild(tr);
    }
  }catch(e){ $('#meta').innerHTML = '<span class="err">'+e.message+'</span>'; }
}
async function act(p, m){ try{ await j(p, {method:m}); }catch(e){ show('error: '+e.message); } refresh(); }
async function logs(n){ try{ const d = await j('vms/'+n+'/logs?tail=200'); show(d.logs || '(empty)'); }catch(e){ show('error: '+e.message); } }
function execIn(n){ $('#execname').value = n; $('#execcmd').focus(); }
$('#execbar').onsubmit = async ev => { ev.preventDefault();
  const n = $('#execname').value, c = $('#execcmd').value; if(!n||!c){ show('pick a vm (its exec button) and type a command'); return; }
  try{ const d = await j('vms/'+n+'/exec', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({cmd:['sh','-c',c]})});
    show('$ '+c+'\n'+d.output+'\n(exit '+d.exit_code+')'); }catch(e){ show('error: '+e.message); } };
function show(t){ $('#out').textContent = t; }
$('#boot').onsubmit = async ev => { ev.preventDefault(); const f = new FormData(ev.target);
  const spec = { name: f.get('name'), cmd: ['sh','-c', f.get('cmd')], net: !!f.get('net') };
  if(f.get('mem')) spec.mem = f.get('mem'); if(f.get('cpu')) spec.cpu = parseFloat(f.get('cpu')); if(f.get('rootfs')) spec.rootfs = f.get('rootfs');
  try{ const st = await j('vms', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(spec)});
    $('#bootmsg').textContent = 'booted ' + st.spec.name + ' (pid ' + st.pid + ')' + (st.warning ? ' - ' + st.warning : ''); ev.target.reset();
  }catch(e){ $('#bootmsg').innerHTML = '<span class="err">'+e.message+'</span>'; }
  refresh(); };
refresh(); setInterval(refresh, 4000);
</script>
</body></html>
`
