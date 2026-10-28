"""
One wire standard: OpenAI /chat/completions in, OpenAI /chat/completions out.

Most providers in providers.json already speak it. A few models only accept a
different shape — SayGM lists `api_shapes` per model, and its Claude models
take only Anthropic `messages`, its Gemini models only `generateContent`, and
gpt-5.5-pro only `responses`. This module translates an OpenAI request body
into whichever shape the model accepts and translates the reply (and every
stream chunk) back, so callers never see the difference.

    chat(cfg, key, model, body, shapes)  -> OpenAI chat.completion dict
    stream(cfg, key, model, body, shapes) -> iterator of OpenAI chat.completion.chunk dicts

Pure requests + json; no FastAPI. Raises catalog.ProviderError on upstream errors.
"""

import json
import time
import uuid
from typing import Iterator, Optional

import requests

try:
    from app.catalog import ProviderError
except ImportError:
    from catalog import ProviderError

SHAPE_ORDER = ('chat.completions', 'messages', 'generateContent', 'streamGenerateContent', 'responses')
TIMEOUT = 300


def pick_shape(shapes: Optional[list]) -> str:
    if not shapes:
        return 'chat.completions'
    for s in SHAPE_ORDER:
        if s in shapes:
            return 'generateContent' if s == 'streamGenerateContent' else s
    raise ProviderError(400, f'model serves {shapes}, not chat')


def _post(url: str, headers: dict, body: dict, stream: bool = False, custom: bool = False):
    try:
        r = requests.post(url, headers={**headers, 'Content-Type': 'application/json'}, json=body,
                          timeout=TIMEOUT, stream=stream, allow_redirects=not custom)
    except requests.RequestException as e:
        raise ProviderError(502, f'{url.split("/")[2]} unreachable: {e}')
    if r.status_code >= 400:
        try:
            err = r.json()
            err = err.get('error', err) if isinstance(err, dict) else err
            msg = err.get('message') if isinstance(err, dict) else err
        except ValueError:
            msg = r.text[:400]
        raise ProviderError(r.status_code, f'{r.status_code}: {str(msg)[:500]}')
    return r


def _sse(resp) -> Iterator[dict]:
    """Parse an SSE body into JSON payloads (skips [DONE], comments, keepalives)."""
    for raw in resp.iter_lines(decode_unicode=True):
        if not raw or not raw.startswith('data:'):
            continue
        data = raw[5:].strip()
        if not data or data == '[DONE]':
            continue
        try:
            yield json.loads(data)
        except ValueError:
            continue


def _root(url: str) -> str:
    """https://api.saygm.com/v1 -> https://api.saygm.com (for /v1beta paths)."""
    u = url.rstrip('/')
    return u[:-3] if u.endswith('/v1') else u


def _cid() -> str:
    return 'chatcmpl-' + uuid.uuid4().hex[:24]


def _completion(model: str, text: str, tool_calls: list, finish: str, usage: Optional[dict]) -> dict:
    msg = {'role': 'assistant', 'content': text or (None if tool_calls else '')}
    if tool_calls:
        msg['tool_calls'] = tool_calls
    return {'id': _cid(), 'object': 'chat.completion', 'created': int(time.time()), 'model': model,
            'choices': [{'index': 0, 'message': msg, 'finish_reason': finish}],
            'usage': usage}


def _chunk(cid: str, model: str, delta: dict, finish: Optional[str] = None, usage: Optional[dict] = None) -> dict:
    out = {'id': cid, 'object': 'chat.completion.chunk', 'created': int(time.time()), 'model': model,
           'choices': [{'index': 0, 'delta': delta, 'finish_reason': finish}]}
    if usage:
        out['usage'] = usage
    return out


def _usage(inp, out) -> Optional[dict]:
    if inp is None and out is None:
        return None
    return {'prompt_tokens': inp or 0, 'completion_tokens': out or 0, 'total_tokens': (inp or 0) + (out or 0)}


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return ''.join(p.get('text', '') for p in content if isinstance(p, dict) and p.get('type') in ('text', 'input_text'))
    return '' if content is None else str(content)


def _data_url(url: str) -> Optional[tuple]:
    if url.startswith('data:') and ';base64,' in url:
        head, b64 = url[5:].split(';base64,', 1)
        return head or 'image/png', b64
    return None


# ── OpenAI chat.completions (passthrough) ────────────────────────────

def _openai_headers(cfg: dict, key: Optional[str]) -> dict:
    return {'Authorization': f'Bearer {key}'} if key else {}


def _oa_chat(cfg, key, model, body):
    r = _post(cfg['url'].rstrip('/') + '/chat/completions', _openai_headers(cfg, key),
              {**body, 'model': model, 'stream': False}, custom=cfg.get('custom'))
    return r.json()


def _oa_stream(cfg, key, model, body):
    req = {**body, 'model': model, 'stream': True}
    req.setdefault('stream_options', {'include_usage': True})
    try:
        r = _post(cfg['url'].rstrip('/') + '/chat/completions', _openai_headers(cfg, key), req,
                  stream=True, custom=cfg.get('custom'))
    except ProviderError as e:
        if e.status == 400 and 'stream_options' in e.detail:  # older servers reject it
            req.pop('stream_options')
            r = _post(cfg['url'].rstrip('/') + '/chat/completions', _openai_headers(cfg, key), req,
                      stream=True, custom=cfg.get('custom'))
        else:
            raise
    yield from _sse(r)


# ── Anthropic messages ───────────────────────────────────────────────

def _to_anthropic(model: str, body: dict) -> dict:
    system, msgs = [], []
    for m in body.get('messages') or []:
        role, content = m.get('role'), m.get('content')
        if role in ('system', 'developer'):
            system.append(_text(content))
            continue
        if role == 'tool':
            block = {'type': 'tool_result', 'tool_use_id': m.get('tool_call_id'), 'content': _text(content)}
            if msgs and msgs[-1]['role'] == 'user' and isinstance(msgs[-1]['content'], list):
                msgs[-1]['content'].append(block)
            else:
                msgs.append({'role': 'user', 'content': [block]})
            continue
        blocks = []
        if isinstance(content, list):
            for p in content:
                if not isinstance(p, dict):
                    continue
                if p.get('type') == 'text':
                    blocks.append({'type': 'text', 'text': p.get('text', '')})
                elif p.get('type') == 'image_url':
                    url = (p.get('image_url') or {}).get('url', '')
                    d = _data_url(url)
                    blocks.append({'type': 'image', 'source': {'type': 'base64', 'media_type': d[0], 'data': d[1]}}
                                  if d else {'type': 'image', 'source': {'type': 'url', 'url': url}})
        elif content:
            blocks.append({'type': 'text', 'text': str(content)})
        for tc in m.get('tool_calls') or []:
            fn = tc.get('function') or {}
            try:
                args = json.loads(fn.get('arguments') or '{}')
            except ValueError:
                args = {}
            blocks.append({'type': 'tool_use', 'id': tc.get('id'), 'name': fn.get('name'), 'input': args})
        msgs.append({'role': 'assistant' if role == 'assistant' else 'user', 'content': blocks or ''})
    out = {'model': model, 'messages': msgs,
           'max_tokens': body.get('max_tokens') or body.get('max_completion_tokens') or 4096}
    if system:
        out['system'] = '\n\n'.join(s for s in system if s)
    for k in ('temperature', 'top_p'):
        if body.get(k) is not None:
            out[k] = body[k]
    if body.get('stop'):
        out['stop_sequences'] = [body['stop']] if isinstance(body['stop'], str) else body['stop']
    if body.get('tools'):
        out['tools'] = [{'name': t['function']['name'], 'description': t['function'].get('description', ''),
                         'input_schema': t['function'].get('parameters') or {'type': 'object', 'properties': {}}}
                        for t in body['tools'] if t.get('type') == 'function']
    return out


STOP = {'end_turn': 'stop', 'stop_sequence': 'stop', 'max_tokens': 'length', 'tool_use': 'tool_calls',
        'STOP': 'stop', 'MAX_TOKENS': 'length', 'SAFETY': 'content_filter'}


def _ant_headers(key):
    return {'x-api-key': key or '', 'anthropic-version': '2023-06-01'}


def _ant_chat(cfg, key, model, body):
    r = _post(cfg['url'].rstrip('/') + '/messages', _ant_headers(key), _to_anthropic(model, body))
    res = r.json()
    text = ''.join(b.get('text', '') for b in res.get('content') or [] if b.get('type') == 'text')
    calls = [{'id': b['id'], 'type': 'function',
              'function': {'name': b['name'], 'arguments': json.dumps(b.get('input') or {})}}
             for b in res.get('content') or [] if b.get('type') == 'tool_use']
    u = res.get('usage') or {}
    return _completion(model, text, calls, STOP.get(res.get('stop_reason'), 'stop'),
                       _usage(u.get('input_tokens'), u.get('output_tokens')))


def _ant_stream(cfg, key, model, body):
    r = _post(cfg['url'].rstrip('/') + '/messages', _ant_headers(key),
              {**_to_anthropic(model, body), 'stream': True}, stream=True)
    cid, inp, out, tools = _cid(), None, None, {}
    yield _chunk(cid, model, {'role': 'assistant', 'content': ''})
    for ev in _sse(r):
        t = ev.get('type')
        if t == 'message_start':
            inp = ((ev.get('message') or {}).get('usage') or {}).get('input_tokens')
        elif t == 'content_block_start' and (ev.get('content_block') or {}).get('type') == 'tool_use':
            b = ev['content_block']
            tools[ev.get('index')] = len(tools)
            yield _chunk(cid, model, {'tool_calls': [{'index': tools[ev.get('index')], 'id': b.get('id'), 'type': 'function',
                                                      'function': {'name': b.get('name'), 'arguments': ''}}]})
        elif t == 'content_block_delta':
            d = ev.get('delta') or {}
            if d.get('type') == 'text_delta':
                yield _chunk(cid, model, {'content': d.get('text', '')})
            elif d.get('type') == 'input_json_delta' and ev.get('index') in tools:
                yield _chunk(cid, model, {'tool_calls': [{'index': tools[ev['index']],
                                                          'function': {'arguments': d.get('partial_json', '')}}]})
        elif t == 'message_delta':
            out = (ev.get('usage') or {}).get('output_tokens', out)
            reason = (ev.get('delta') or {}).get('stop_reason')
            if reason:
                yield _chunk(cid, model, {}, STOP.get(reason, 'stop'), _usage(inp, out))
        elif t == 'error':
            raise ProviderError(424, str((ev.get('error') or {}).get('message') or ev)[:500])


# ── Gemini generateContent ───────────────────────────────────────────

def _to_gemini(body: dict) -> dict:
    system, contents = [], []
    for m in body.get('messages') or []:
        role, content = m.get('role'), m.get('content')
        if role in ('system', 'developer'):
            system.append(_text(content))
            continue
        parts = []
        if isinstance(content, list):
            for p in content:
                if isinstance(p, dict) and p.get('type') == 'text':
                    parts.append({'text': p.get('text', '')})
                elif isinstance(p, dict) and p.get('type') == 'image_url':
                    d = _data_url((p.get('image_url') or {}).get('url', ''))
                    if d:
                        parts.append({'inlineData': {'mimeType': d[0], 'data': d[1]}})
        else:
            parts.append({'text': _text(content)})
        contents.append({'role': 'model' if role == 'assistant' else 'user', 'parts': parts or [{'text': ''}]})
    gen = {}
    if body.get('max_tokens') or body.get('max_completion_tokens'):
        gen['maxOutputTokens'] = body.get('max_tokens') or body.get('max_completion_tokens')
    for k, g in (('temperature', 'temperature'), ('top_p', 'topP')):
        if body.get(k) is not None:
            gen[g] = body[k]
    if body.get('stop'):
        gen['stopSequences'] = [body['stop']] if isinstance(body['stop'], str) else body['stop']
    out = {'contents': contents}
    if gen:
        out['generationConfig'] = gen
    if system:
        out['systemInstruction'] = {'parts': [{'text': '\n\n'.join(system)}]}
    return out


def _gem_parse(res: dict) -> tuple:
    cand = (res.get('candidates') or [{}])[0]
    parts = (cand.get('content') or {}).get('parts') or []
    text = ''.join(p.get('text', '') for p in parts if not p.get('thought'))
    images = [f"data:{p['inlineData'].get('mimeType', 'image/png')};base64,{p['inlineData']['data']}"
              for p in parts if isinstance(p.get('inlineData'), dict)]
    u = res.get('usageMetadata') or {}
    return text, images, cand.get('finishReason'), _usage(u.get('promptTokenCount'), u.get('candidatesTokenCount'))


def _gem_chat(cfg, key, model, body):
    r = _post(f"{_root(cfg['url'])}/v1beta/models/{model}:generateContent",
              _openai_headers(cfg, key), _to_gemini(body))
    text, images, finish, usage = _gem_parse(r.json())
    out = _completion(model, text, [], STOP.get(finish, 'stop'), usage)
    if images:  # same field OpenRouter uses for image-output chat models
        out['choices'][0]['message']['images'] = [{'type': 'image_url', 'image_url': {'url': u}} for u in images]
    return out


def _gem_stream(cfg, key, model, body, shapes):
    cid = _cid()
    yield _chunk(cid, model, {'role': 'assistant', 'content': ''})
    if 'streamGenerateContent' not in (shapes or []):
        res = _gem_chat(cfg, key, model, body)
        msg = res['choices'][0]['message']
        yield _chunk(cid, model, {'content': msg.get('content') or '', **({'images': msg['images']} if msg.get('images') else {})})
        yield _chunk(cid, model, {}, res['choices'][0]['finish_reason'], res.get('usage'))
        return
    r = _post(f"{_root(cfg['url'])}/v1beta/models/{model}:streamGenerateContent?alt=sse",
              _openai_headers(cfg, key), _to_gemini(body), stream=True)
    finish, usage = None, None
    for ev in _sse(r):
        text, _imgs, f, u = _gem_parse(ev)
        finish, usage = f or finish, u or usage
        if text:
            yield _chunk(cid, model, {'content': text})
    yield _chunk(cid, model, {}, STOP.get(finish, 'stop'), usage)


# ── OpenAI responses ─────────────────────────────────────────────────

def _resp_chat(cfg, key, model, body):
    msgs = body.get('messages') or []
    req = {'model': model,
           'input': [{'role': m['role'] if m.get('role') != 'system' else 'developer', 'content': _text(m.get('content'))}
                     for m in msgs]}
    if body.get('max_tokens') or body.get('max_completion_tokens'):
        req['max_output_tokens'] = body.get('max_tokens') or body.get('max_completion_tokens')
    res = _post(cfg['url'].rstrip('/') + '/responses', _openai_headers(cfg, key), req).json()
    text = res.get('output_text') or ''.join(
        c.get('text', '') for o in res.get('output') or [] if o.get('type') == 'message'
        for c in o.get('content') or [] if c.get('type') in ('output_text', 'text'))
    u = res.get('usage') or {}
    return _completion(model, text, [], 'stop', _usage(u.get('input_tokens'), u.get('output_tokens')))


def _resp_stream(cfg, key, model, body):
    res = _resp_chat(cfg, key, model, body)
    cid = _cid()
    yield _chunk(cid, model, {'role': 'assistant', 'content': res['choices'][0]['message']['content']})
    yield _chunk(cid, model, {}, 'stop', res.get('usage'))


# ── public ───────────────────────────────────────────────────────────

def chat(cfg: dict, key: Optional[str], model: str, body: dict, shapes: Optional[list] = None) -> dict:
    """OpenAI chat.completions request body -> OpenAI chat.completion, whatever the model speaks."""
    shape = pick_shape(shapes)
    if shape == 'messages':
        return _ant_chat(cfg, key, model, body)
    if shape == 'generateContent':
        return _gem_chat(cfg, key, model, body)
    if shape == 'responses':
        return _resp_chat(cfg, key, model, body)
    return _oa_chat(cfg, key, model, body)


def stream(cfg: dict, key: Optional[str], model: str, body: dict, shapes: Optional[list] = None) -> Iterator[dict]:
    """Same, streamed: yields OpenAI chat.completion.chunk dicts."""
    shape = pick_shape(shapes)
    if shape == 'messages':
        return _ant_stream(cfg, key, model, body)
    if shape == 'generateContent':
        return _gem_stream(cfg, key, model, body, shapes)
    if shape == 'responses':
        return _resp_stream(cfg, key, model, body)
    return _oa_stream(cfg, key, model, body)
