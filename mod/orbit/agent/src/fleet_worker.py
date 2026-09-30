"""
fleet_worker - one fleet module, held in its own interpreter.

Run as `python3 fleet_worker.py <module>`: loads the module once, then answers
JSON lines on stdin with JSON lines on stdout, until stdin closes.

    {"op": "models"}                                  -> {"ok": true, "result": [ids]}
    {"op": "complete", "prompt": …, "model": …, …}    -> {"ok": true, "result": "text"}

Why a process and not an import: the fleet loads modules into one Python
process with `m.mod(name)()`, and several of them keep their code in a
top-level `src` package — the same name this agent's own code lives under.
Whichever imports first owns `sys.modules['src']`, and the other one's
`from src import …` reaches into the wrong tree. A worker per module has its
own `sys.modules`, so no module can break another, or the agent.

Standalone on purpose: no imports from the agent package, so it starts in any
interpreter that can import the framework.
"""
import inspect
import json
import os
import sys
import traceback

MOD_ROOT = os.environ.get('MOD_ROOT') or os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', '..'))

MAX_MODELS = 400
TEXT_KEYS = ('text', 'content', 'answer', 'output', 'response', 'completion', 'result')
# the kwargs a models() may take to mean "only the ones that chat"
TEXT_FILTERS = (('type', 'text'), ('kind', 'chat'))


def text_of(out) -> str:
    """The completion text out of whatever a module returned."""
    if out is None:
        return ''
    if isinstance(out, str):
        return out
    if isinstance(out, dict):
        if out.get('ok') is False or (out.get('error') and not any(out.get(k) for k in TEXT_KEYS)):
            raise RuntimeError(str(out.get('error') or out.get('detail') or 'the module refused'))
        choices = out.get('choices')
        if isinstance(choices, list) and choices:
            c = choices[0] or {}
            msg = c.get('message') or c.get('delta') or {}
            return str(msg.get('content') or c.get('text') or '')
        for k in TEXT_KEYS:
            v = out.get(k)
            if isinstance(v, str):
                return v
            if isinstance(v, (dict, list)):
                return text_of(v)
        if isinstance(out.get('message'), dict):
            return text_of(out['message'])
        return json.dumps(out, default=str)
    if isinstance(out, (list, tuple)):
        return ''.join(text_of(x) for x in out)
    if hasattr(out, '__iter__') and not isinstance(out, (bytes, bytearray)):
        return ''.join(text_of(x) for x in out)
    return str(out)


def ids_of(raw) -> list:
    """A flat, de-duplicated list of model ids out of whatever models() gave."""
    if isinstance(raw, dict):
        for k in ('models', 'data', 'items', 'results'):
            if isinstance(raw.get(k), (list, dict)):
                return ids_of(raw[k])
        # grouped lists ({served: […], local: […]}) — what is served first
        groups = [raw[k] for k in ('served', 'local', 'known_good') if isinstance(raw.get(k), list)] \
            or [v for v in raw.values() if isinstance(v, list)]
        if groups:
            return ids_of([row for g in groups for row in g])
        return [k for k in raw if isinstance(k, str)][:MAX_MODELS]   # {id: info}
    out, seen = [], set()
    for row in raw or []:
        mid = row if isinstance(row, str) else (
            (row.get('id') or row.get('key') or row.get('slug')
             or row.get('model') or row.get('name')) if isinstance(row, dict) else None)
        # a row that says it isn't text (image, audio, video) can't drive the loop
        if isinstance(row, dict):
            kind = str(row.get('type') or row.get('kind') or '').lower()
            if kind and kind not in ('text', 'llm', 'chat', 'language'):
                continue
        if isinstance(mid, str) and mid and mid not in seen:
            seen.add(mid)
            out.append(mid)
    return out[:MAX_MODELS]


def params_of(fn):
    try:
        return inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return {}


def call(fn, **kwargs):
    """Call fn with only the kwargs its signature names (all of them for **kw)."""
    params = params_of(fn)
    if not params or any(p.kind is p.VAR_KEYWORD for p in params.values()):
        return fn(**kwargs)
    return fn(**{k: v for k, v in kwargs.items() if k in params})


def models(client) -> list:
    fn = getattr(client, 'models', None) or getattr(client, 'model2info')
    params = params_of(fn)
    kw = {k: v for k, v in TEXT_FILTERS if k in params}
    return ids_of(fn(**kw))


def complete(client, name, prompt, model=None, max_tokens=1024,
             temperature=0.0, history=None, **_):
    common = {'max_tokens': max_tokens, 'temperature': temperature}
    if model:
        common['model'] = model
    messages = list(history or []) + [{'role': 'user', 'content': prompt}]
    for meth in ('chat', 'complete', 'ask'):
        fn = getattr(client, meth, None)
        if not callable(fn):
            continue
        params = params_of(fn)
        if meth == 'chat' and 'messages' in params:
            out = call(fn, messages=messages, **common)
        elif 'prompt' in params:
            out = call(fn, prompt=prompt, **common)
        elif 'message' in params:
            out = call(fn, message=prompt, **common)
        elif meth == 'complete' and model and 'model' in params:
            out = fn(model, prompt)
        else:
            continue
        text = text_of(out)
        if not text.strip():
            raise RuntimeError(f'{name}.{meth}() returned no text')
        return text
    raise RuntimeError(f"{name} has no chat()/ask() the agent knows how to call")


def main(name: str) -> None:
    # python put this file's own directory first on sys.path — the agent's
    # package, whose identity.py/utils.py would shadow the module's own
    here = os.path.dirname(os.path.abspath(__file__))
    sys.path[:] = [p for p in sys.path if os.path.abspath(p or '.') != here]
    sys.path.insert(0, MOD_ROOT)
    # module code prints freely; stdout is the wire, so its prints go to stderr
    wire, sys.stdout = sys.stdout, sys.stderr
    client, load_error = None, None
    try:
        import mod as m
        obj = m.mod(name)
        client = obj() if isinstance(obj, type) else obj
    except Exception as e:
        load_error = f'could not load the {name} module: {e}'

    def send(msg):
        wire.write(json.dumps(msg, default=str) + '\n')
        wire.flush()

    send({'ok': client is not None, 'ready': True, 'error': load_error})
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            if load_error:
                raise RuntimeError(load_error)
            op = req.pop('op', '')
            if op == 'models':
                send({'ok': True, 'result': models(client)})
            elif op == 'complete':
                send({'ok': True, 'result': complete(client, name, **req)})
            elif op == 'ping':
                send({'ok': True, 'result': 'pong'})
            else:
                raise ValueError(f'unknown op {op!r}')
        except Exception as e:
            print(traceback.format_exc(), file=sys.stderr)
            send({'ok': False, 'error': str(e) or type(e).__name__})


if __name__ == '__main__':
    main(sys.argv[1])
