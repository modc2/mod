#!/usr/bin/env python3
"""infer mcp — twenty-eight tools: prove a model said it, and make it cheaper.

Two halves, in the order the work actually goes.

The board: `infer_run` asks a model something at temperature 0 and files the
answer as a signed, content-addressed receipt; `infer_replicate` asks the same
question again and reports whether the answer held; `infer_verify` rechecks
every hash and the signature from the content itself. `infer_board` is what
everyone else's receipts look like.

The optimizer: get a model in, read what it is, ask what to try, try it, and
check the result is still the same model. `infer_optimize` is the one that
matters; everything else exists so its answer can be trusted — or, now, posted
as a receipt somebody can re-run.

Self-contained JSON-RPC 2.0 on the standard library, no `mcp` package.

    python3 mcp.py                     # stdio — one JSON message per line
    python3 mcp.py --http --port 50820 # Streamable HTTP — POST /mcp
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    # Appended, not prepended: this directory holds a mod.py that would shadow
    # the protocol's own `mod` package for anything that imports us.
    sys.path.append(HERE)

import engine as E                                          # noqa: E402
import proofs as P                                          # noqa: E402
from engine import InferError                               # noqa: E402

SUPPORTED_PROTOCOL_VERSIONS = ('2025-06-18', '2025-03-26', '2024-11-05')
DEFAULT_PROTOCOL_VERSION = '2025-03-26'

INSTRUCTIONS = (
    'Two things live here and they meet in the middle. '
    'THE BOARD is for reproducible inference: a claim that a model produced a '
    'particular output, run at temperature 0, hashed, signed with a mod '
    'protocol key and published to core/store so its CID is a permanent handle. '
    'It only accepts greedy runs — temperature 0, top_p 1, one candidate, no '
    'penalties — and refuses anything else with 422, because sampled receipts '
    'would make every divergence on the board unreadable. Receipts that share a '
    'request_hash are one QUESTION, and the question is what carries a verdict: '
    'unreplicated (one receipt), self-reproduced (ran again, same bytes, same '
    'signer), reproduced (same bytes, two independent signers), divergent (one '
    'greedy question, more than one answer). Use infer_run to run and post in '
    'one move, infer_replicate to check somebody else\'s claim, infer_verify to '
    'recheck the hashes and the signature without trusting this module, and '
    'infer_canon when you want to prepare and sign a claim somewhere else and '
    'still land on the same question. '
    'Be careful with one thing: temperature 0 is greedy, NOT deterministic, on '
    'any hosted endpoint. Batch composition, expert routing and float reduction '
    'order are not part of the request, so a divergent verdict on a hosted model '
    'is a real property of the endpoint and not a bug on the board. The only '
    'bit-exact runtime here is local ONNX (runtime=onnx), pinned to one thread '
    'on CPU with the graph optimizer off — those receipts can be re-executed by '
    'infer_verify rerun=true and are expected to match exactly. '
    'THE OPTIMIZER is inference optimization, architecture-agnostic. Everything '
    'in that half works on '
    'one format — ONNX — because that is the binary both onnxruntime (here) '
    'and onnxruntime-web (a browser tab) execute without a second conversion, '
    'so a model optimized once runs in both places. A CNN, an LSTM, a '
    'transformer and a gradient-boosted forest are all just graphs by the time '
    'they get here, and the passes read the graph. '
    'The path: infer_add (or infer_export from torch) → infer_inspect to see '
    'what it is → infer_plan for what is worth trying on it → infer_optimize '
    'to do it. infer_optimize already benchmarks before and after, compares '
    'the outputs and re-checks browser portability, so read its `verdict` '
    'first and its `passes` list when you want to know which step did what. '
    'Two things to actually watch for. Quantization makes models smaller and '
    'frequently SLOWER on small graphs — never recommend int8 without running '
    'infer_compare on the real model. And exactly one pass breaks browser '
    'deployment: `all`, which rewrites the graph into com.microsoft.nchwc '
    'layout operators for the CPU that ran it; `extended` is safe, because the '
    'browser wasm backend does register the contrib operators fusion emits '
    '(measured in a browser, not assumed). infer_optimize reports '
    '`portability_lost` when it happens. Nothing here guesses at accuracy: '
    'infer_parity feeds both models the same inputs and reports how far the '
    'numbers moved.'
)


def _str(desc, **extra):
    return {'type': 'string', 'description': desc, **extra}


def _num(desc, **extra):
    return {'type': 'number', 'description': desc, **extra}


def _bool(desc):
    return {'type': 'boolean', 'description': desc}


_REF = _str('a stored model: its id, its name, an id prefix, or a path to an '
            '.onnx file on this box')
_BENCH = {
    'runs': _num('timed iterations after warmup (default 30)'),
    'batch': _num('what to make the first symbolic dimension (default 1)'),
    'shapes': {'type': 'object', 'description': 'override an input shape by '
                                                'name, e.g. {"input_ids": "1,128"}'},
}


# ── handlers ─────────────────────────────────────────────────────

def _t_health(a):
    return E.health()


def _t_models(a):
    return E.models(limit=a.get('limit') or 200)


def _t_add(a):
    return E.add(data=a.get('data'), path=a.get('path'), url=a.get('url'),
                 name=a.get('name'), note=a.get('note'))


def _t_inspect(a):
    return E.inspect(a['model'])


def _t_plan(a):
    return E.plan(a['model'], target=a.get('target') or 'local')


def _t_passes(a):
    return E.passes()


def _t_optimize(a):
    return E.optimize(a['model'], passes_=a.get('passes'), name=a.get('name'),
                      check=a.get('check', True), runs=a.get('runs') or E.DEFAULT_RUNS,
                      batch=a.get('batch') or 1, samples=a.get('samples') or 4,
                      tol=a.get('tol') if a.get('tol') is not None else 1e-3,
                      shapes=a.get('shapes'), threads=a.get('threads'))


def _t_bench(a):
    return E.bench(a['model'], runs=a.get('runs') or E.DEFAULT_RUNS,
                   warmup=a.get('warmup') if a.get('warmup') is not None
                   else E.DEFAULT_WARMUP,
                   batch=a.get('batch') or 1, threads=a.get('threads'),
                   provider=a.get('provider'), shapes=a.get('shapes'))


def _t_parity(a):
    return E.parity(a['a'], a['b'], samples=a.get('samples') or 8,
                    batch=a.get('batch') or 1,
                    tol=a.get('tol') if a.get('tol') is not None else 1e-3,
                    shapes=a.get('shapes'))


def _t_portable(a):
    return E.portable(a['model'])


# The five that change the answer to "should I ship this". `slim` and `shapes`
# are housekeeping and would only pad the table with two identical rows.
COMPARABLE = ['basic', 'extended', 'all', 'fp16', 'int8']


def _t_compare(a):
    """Every pass in the list, each applied on its own, side by side."""
    which = a.get('passes') or COMPARABLE
    if isinstance(which, str):
        which = [p.strip() for p in which.replace(' ', ',').split(',') if p.strip()]
    rows = []
    for name in which:
        try:
            rep = E.optimize(a['model'], passes_=[name], check=True,
                             runs=a.get('runs') or 20, batch=a.get('batch') or 1,
                             shapes=a.get('shapes'))
            rows.append({'pass': name, 'ok': True, 'id': rep['result']['id'],
                         'bytes': rep['result']['bytes'],
                         'size_ratio': (rep.get('size') or {}).get('ratio'),
                         'speedup': (rep.get('speed') or {}).get('speedup'),
                         'max_abs_err': (rep.get('parity') or {}).get('max_abs_err'),
                         'portable': (rep.get('portable') or {}).get('portable'),
                         'verdict': rep['verdict']})
        except InferError as e:
            rows.append({'pass': name, 'ok': False, 'error': e.message})
        except Exception as e:
            rows.append({'pass': name, 'ok': False, 'error': f'{type(e).__name__}: {e}'})
    ranked = sorted([r for r in rows if r.get('speedup')],
                    key=lambda r: -r['speedup'])
    return {'model': a['model'], 'tried': which, 'results': rows,
            'fastest': ranked[0]['pass'] if ranked else None,
            'smallest': min([r for r in rows if r.get('size_ratio')],
                            key=lambda r: -r['size_ratio'])['pass']
            if any(r.get('size_ratio') for r in rows) else None,
            'note': 'each pass applied alone to the same source, so the rows are '
                    'comparable — a real build usually stacks several'}


def _t_export(a):
    return E.export(a['source'], name=a.get('name'), opset=a.get('opset') or 17,
                    shape=a.get('shape'), weights=a.get('weights'))


def _t_examples(a):
    return E.examples()


def _t_delete(a):
    return E.delete(a['model'])


# ── the board ────────────────────────────────────────────────────

def _t_board(a):
    return P.board(model=a.get('model'), provider=a.get('provider'),
                   runtime=a.get('runtime'), verdict=a.get('verdict'),
                   by=a.get('by'), q=a.get('q'), limit=a.get('limit') or 50,
                   sort=a.get('sort') or 'recent')


def _t_status(a):
    return P.status()


def _t_run(a):
    return P.run(a['model'], provider=a.get('provider'), runtime=a.get('runtime'),
                 sign=a.get('sign', True), publish=a.get('publish', True),
                 repeat=a.get('repeat') or 1, prompt=a.get('prompt'),
                 messages=a.get('messages'), system=a.get('system'),
                 max_tokens=a.get('max_tokens') or 512, seed=a.get('seed'),
                 stop=a.get('stop'), api_key=a.get('api_key'),
                 batch=a.get('batch') or 1, shapes=a.get('shapes'))


def _t_post(a):
    return P.post(claim=a.get('claim'), sign=a.get('sign', True),
                  publish=a.get('publish', True),
                  attestation=a.get('attestation'),
                  **{k: v for k, v in a.items()
                     if k not in ('claim', 'sign', 'publish', 'attestation')})


def _t_replicate(a):
    return P.replicate(question_id=a.get('question'), receipt=a.get('receipt'),
                       provider=a.get('provider'), sign=a.get('sign', True),
                       publish=a.get('publish', True), api_key=a.get('api_key'))


def _t_question(a):
    return P.question(a['question'], full=a.get('full', False))


def _t_receipt(a):
    return P.receipt(a['receipt'])


def _t_verify(a):
    return P.verify(a['receipt'], rerun=a.get('rerun', False),
                    fetch=a.get('fetch', True))


def _t_diff(a):
    return P.diff(a['a'], a['b'])


def _t_leaderboard(a):
    return P.leaderboard(runtime=a.get('runtime'),
                         min_receipts=a.get('min_receipts') or 2)


def _t_canon(a):
    return P.canonical(runtime=a.get('runtime') or 'llm', model=a.get('model'),
                       prompt=a.get('prompt'), messages=a.get('messages'),
                       system=a.get('system'), max_tokens=a.get('max_tokens') or 512,
                       seed=a.get('seed'), stop=a.get('stop'),
                       batch=a.get('batch') or 1, shapes=a.get('shapes'))


def _t_providers(a):
    if a.get('add') or a.get('base'):
        return P.add_provider(a.get('add') or a.get('name'), a.get('base'),
                              style=a.get('style') or 'openai', note=a.get('note'))
    return P.providers()


def _t_set_key(a):
    return P.set_key(a['provider'], a.get('key'))


def _t_import(a):
    return P.fetch(a['cid'])


_MSGS = {'type': 'array', 'description': 'a chat, as [{role, content}] — use '
                                         'this instead of prompt for anything '
                                         'multi-turn',
         'items': {'type': 'object'}}

TOOLS = {
    'infer_board': {
        'description': 'The board: every question anybody has posted, newest '
                       'first, one row per question rather than per receipt. A '
                       'question is a canonical request hash, so the same prompt '
                       'to the same model through two different routers is ONE '
                       'row with two receipts under it. Each row carries its '
                       'verdict — unreplicated, self-reproduced, reproduced, or '
                       'divergent — plus how many receipts agree, which '
                       'providers served them, who signed them, and where the '
                       'answers first parted when they did. Sort by `divergent` '
                       'to see the models that will not hold still.',
        'inputSchema': {'type': 'object', 'properties': {
            'model': _str('only this model (prefix match on the normalized name)'),
            'provider': _str('only questions some receipt was served by'),
            'runtime': _str('llm or onnx', enum=['llm', 'onnx']),
            'verdict': _str('filter to one verdict',
                            enum=['unreplicated', 'self-reproduced',
                                  'reproduced', 'divergent']),
            'by': _str('only questions this address signed a receipt for'),
            'q': _str('substring of the prompt, the output or the model name'),
            'sort': _str('recent (default), replicated, divergent, oldest, model',
                         enum=['recent', 'replicated', 'divergent', 'oldest', 'model']),
            'limit': _num('rows (default 50)')}},
        'handler': _t_board,
    },
    'infer_run': {
        'description': 'Ask a model something at temperature 0, then hash, sign '
                       'and publish the answer as a receipt — the one call that '
                       'does the whole thing. provider picks the endpoint '
                       '(infer_providers says which are reachable); '
                       'runtime=onnx instead runs a stored ONNX graph on this '
                       'box, which is the only bit-exact option here. Set '
                       'repeat=2..10 to run the same question several times '
                       'against the same endpoint: that is the cheapest possible '
                       'test of whether a model is deterministic at all, and it '
                       'comes back as `self_consistent` before anybody else '
                       'spends money replicating it.',
        'inputSchema': {'type': 'object', 'properties': {
            'model': _str('the model to ask, as the provider names it — '
                          '"openai/gpt-4o", "claude-sonnet-4", or for '
                          'runtime=onnx a stored model id or name'),
            'prompt': _str('what to ask it'),
            'messages': _MSGS,
            'system': _str('system prompt, kept separate in the canonical form'),
            'provider': _str('openrouter (default), openai, anthropic, groq, '
                             'deepseek, together, mistral, local, or onnx'),
            'runtime': _str('llm (default) or onnx', enum=['llm', 'onnx']),
            'max_tokens': _num('cap on the answer (default 512) — it is part of '
                               'the question, so changing it changes the hash'),
            'seed': _num('provider seed where one exists; recorded either way'),
            'stop': {'type': 'array', 'items': {'type': 'string'},
                     'description': 'stop sequences'},
            'repeat': _num('run it this many times, 1-10 (default 1)'),
            'api_key': _str('a key for this call only — never stored, never '
                            'written into the receipt'),
            'batch': _num('runtime=onnx: what to make the first symbolic dim'),
            'shapes': {'type': 'object', 'description': 'runtime=onnx: override '
                                                        'an input shape by name'},
            'publish': _bool('push the receipt to core/store (default true)'),
            'sign': _bool('sign it with this box\'s key (default true)')},
            'required': ['model']},
        'handler': _t_run,
    },
    'infer_post': {
        'description': 'Post a run you did somewhere else. Give it the model, '
                       'the prompt (or messages) and the exact output, and it is '
                       'canonicalized, hashed, signed and published exactly like '
                       'a local run — the board never trusts a hash it was '
                       'handed, it recomputes every one from the content. This '
                       'is the main way models get onto the board: the run does '
                       'not have to have happened here. Refused with 422 if the '
                       'sampling parameters you declare are not greedy, and '
                       'refused if a stated output_hash does not match the '
                       'output it names. Pass `attestation` (a mod protocol '
                       'token over the receipt hash) to file it under your own '
                       'address instead of this box\'s.',
        'inputSchema': {'type': 'object', 'properties': {
            'model': _str('the model that produced it'),
            'output': _str('exactly what it said — byte for byte, no trimming'),
            'prompt': _str('what it was asked'),
            'messages': _MSGS,
            'system': _str('system prompt if there was one'),
            'provider': _str('who served it — openai, anthropic, a vllm host, '
                             'anything; it is recorded, not validated'),
            'max_tokens': _num('the cap that was set (default 512)'),
            'seed': _num('the seed that was passed, if any'),
            'temperature': _num('must be 0 or absent — this is the gate'),
            'top_p': _num('must be 1 or absent'),
            'meta': {'type': 'object', 'description': 'usage, fingerprint, '
                                                      'latency, anything worth '
                                                      'keeping; credential-'
                                                      'looking keys are redacted'},
            'attestation': _str('a mod protocol token over the receipt hash, if '
                                'you signed it yourself'),
            'claim': {'type': 'object', 'description': 'a whole claim or bundle '
                                                       'from another board, '
                                                       'instead of loose fields'},
            'publish': _bool('push to core/store (default true)'),
            'sign': _bool('sign with this box\'s key (default true)')}},
        'handler': _t_post,
    },
    'infer_replicate': {
        'description': 'Run somebody else\'s question again and say whether the '
                       'answer held. This is the tool the board exists for: one '
                       'receipt is a person\'s word, two receipts from two '
                       'signers is a fact about the model. It re-asks the EXACT '
                       'canonical request — same messages, same max_tokens, same '
                       'seed — refuses to file the result if what it asked '
                       'hashes differently, and returns `reproduced` plus, when '
                       'it is false, the character where the two answers parted. '
                       'Pass provider= to replicate through a different endpoint, '
                       'which is how you find out whether a divergence belongs to '
                       'the model or to the router.',
        'inputSchema': {'type': 'object', 'properties': {
            'question': _str('a question hash from the board'),
            'receipt': _str('or one specific receipt id to replicate'),
            'provider': _str('replicate through this provider instead of the '
                             'original one'),
            'api_key': _str('a key for this call only'),
            'publish': _bool('push to core/store (default true)'),
            'sign': _bool('sign it (default true)')}},
        'handler': _t_replicate,
    },
    'infer_verify': {
        'description': 'Recheck a receipt without trusting this module. Every '
                       'check recomputes from the receipt\'s own content: the '
                       'canonical request re-hashed to its request_hash, the '
                       'output bytes re-hashed to their output_hash, the claim '
                       're-canonicalized to the id it is filed under, the '
                       'sampling parameters re-tested against the greedy rule, '
                       'the signing address recovered from the attestation, and '
                       'the published bundle re-fetched from core/store by CID '
                       'and byte-compared. With rerun=true an ONNX receipt is '
                       'executed again and its tensor hash re-checked, which is '
                       'the strongest thing on this board. A pass means the '
                       'bytes are what they say and the signer is who they say — '
                       'it does NOT mean the model would say it again, which is '
                       'what the question verdict is for.',
        'inputSchema': {'type': 'object', 'properties': {
            'receipt': _str('a receipt id, or a hash prefix'),
            'rerun': _bool('actually execute an ONNX receipt again (default false)'),
            'fetch': _bool('re-fetch the published bundle from the store '
                           '(default true)')},
            'required': ['receipt']},
        'handler': _t_verify,
    },
    'infer_question': {
        'description': 'One question, opened up: the canonical request, every '
                       'receipt filed against it with its output hash, signer, '
                       'provider and CID, and the verdict they add up to. When '
                       'the verdict is divergent it also carries the majority '
                       'answer, each variant with its receipt count, and the '
                       'exact character index where the top two answers stopped '
                       'agreeing — which is usually far more informative than '
                       'the fact that they disagree at all.',
        'inputSchema': {'type': 'object', 'properties': {
            'question': _str('the question hash (a prefix is enough)'),
            'full': _bool('include every full output, not just hashes and '
                          'previews')},
            'required': ['question']},
        'handler': _t_question,
    },
    'infer_receipt': {
        'description': 'One receipt exactly as it was published: the claim, its '
                       'hash, the attestation token, the signing address and the '
                       'store CID. This is the bundle those bytes hash to, so it '
                       'is what a third party checks against.',
        'inputSchema': {'type': 'object', 'properties': {
            'receipt': _str('a receipt id or hash prefix')},
            'required': ['receipt']},
        'handler': _t_receipt,
    },
    'infer_diff': {
        'description': 'Two receipts side by side and the exact character they '
                       'disagree on, with the shared prefix that led up to it. '
                       'Greedy answers to one question usually agree for a long '
                       'way and then fork at a single token, and where that '
                       'happens says more than the fact that it did. Says so '
                       'plainly if the two receipts are answers to different '
                       'questions, in which case the difference means nothing.',
        'inputSchema': {'type': 'object', 'properties': {
            'a': _str('a receipt id'), 'b': _str('another receipt id')},
            'required': ['a', 'b']},
        'handler': _t_diff,
    },
    'infer_leaderboard': {
        'description': 'Which models actually hold still at temperature 0, by '
                       'the receipts on this board. reproducibility is the share '
                       'of REPLICATED questions whose receipts were byte-'
                       'identical; a question nobody ran twice is counted and '
                       'never scored, because a model is not reproducible just '
                       'because nobody checked. Expect local onnx rows at 1.0 '
                       'and hosted rows below it: greedy decoding removes the '
                       'sampler, not the batching, the expert routing or the '
                       'float reduction order.',
        'inputSchema': {'type': 'object', 'properties': {
            'runtime': _str('llm or onnx', enum=['llm', 'onnx']),
            'min_receipts': _num('receipts a question needs before it scores '
                                 '(default 2)')}},
        'handler': _t_leaderboard,
    },
    'infer_canon': {
        'description': 'The canonical request bytes and their hash, without '
                       'running anything. Use it to prepare a claim off this box '
                       'and still land on the same question: the rules are JSON '
                       'with sorted keys, no whitespace, UTF-8 with non-ASCII '
                       'left unescaped, sha256 in lowercase hex — reproducible '
                       'in any language. It also says whether that question is '
                       'already on the board, which is worth knowing before you '
                       'spend a call on it.',
        'inputSchema': {'type': 'object', 'properties': {
            'model': _str('the model the question is for'),
            'prompt': _str('what to ask'),
            'messages': _MSGS,
            'system': _str('system prompt'),
            'max_tokens': _num('part of the question (default 512)'),
            'seed': _num('part of the question when the provider takes one'),
            'runtime': _str('llm (default) or onnx', enum=['llm', 'onnx']),
            'batch': _num('runtime=onnx'), 'shapes': {'type': 'object'}},
            'required': ['model']},
        'handler': _t_canon,
    },
    'infer_providers': {
        'description': 'Which endpoints this box can actually reach and which '
                       'have a key, plus how to give one to the rest. Pass base= '
                       'with add= to register any other openai- or anthropic-'
                       'shaped endpoint (a vllm host, an ollama, a gateway). A '
                       'provider you cannot reach is never a wall: infer_post '
                       'takes a receipt run anywhere and the board checks it the '
                       'same way.',
        'inputSchema': {'type': 'object', 'properties': {
            'add': _str('name a new provider to register'),
            'base': _str('its base url, e.g. http://localhost:8000/v1'),
            'style': _str('openai (default) or anthropic',
                          enum=['openai', 'anthropic']),
            'note': _str('anything worth remembering about it')}},
        'handler': _t_providers,
    },
    'infer_set_key': {
        'description': 'Give a provider an API key. It is written 0600 to the '
                       'state directory, off the module tree and out of '
                       'config.json, and it is never copied into a receipt — '
                       'receipts get published to content-addressed storage, '
                       'where a leaked key could not be unpublished. Pass no key '
                       'to forget one.',
        'inputSchema': {'type': 'object', 'properties': {
            'provider': _str('which provider'),
            'key': _str('the key, or omit to delete the stored one')},
            'required': ['provider']},
        'handler': _t_set_key,
    },
    'infer_import': {
        'description': 'Pull in a receipt published from another box, by its '
                       'core/store CID. The CID is the entire handoff — the '
                       'bundle is fetched, its claim re-canonicalized, its hash '
                       'recomputed and its signature re-recovered here before it '
                       'joins the board, so importing is not trusting. This is '
                       'how two boards become one board.',
        'inputSchema': {'type': 'object', 'properties': {
            'cid': _str('the store CID of a published receipt bundle')},
            'required': ['cid']},
        'handler': _t_import,
    },
    'infer_status': {
        'description': 'The board at a glance: receipts, questions, how they '
                       'split across the four verdicts, how many are signed and '
                       'how many made it into the store, this box\'s own signing '
                       'address, whether core/store is reachable, and which '
                       'providers have keys.',
        'inputSchema': {'type': 'object', 'properties': {}},
        'handler': _t_status,
    },
    'infer_health': {
        'description': 'What this box can actually do: onnx and onnxruntime '
                       'versions, which execution providers exist here (CPU '
                       'always, CUDA only if it was built in), which passes are '
                       'available, and how many models are stored. Call it first '
                       'if a pass comes back unavailable.',
        'inputSchema': {'type': 'object', 'properties': {}},
        'handler': _t_health,
    },
    'infer_models': {
        'description': 'Every model in the store, newest first, with size, node '
                       'count, parameter count, guessed architecture, and — for '
                       'anything this module produced — which passes made it and '
                       'what it came from.',
        'inputSchema': {'type': 'object', 'properties': {
            'limit': _num('rows to return (default 200)')}},
        'handler': _t_models,
    },
    'infer_add': {
        'description': 'Put an ONNX model into the store, from base64 bytes '
                       '(data), a path on this box (path), or a URL. It is stored '
                       'under the SHA-256 of its bytes, so the same model added '
                       'twice is one entry and any report can be tied back to the '
                       'exact bytes it was measured on. Rejects anything that '
                       'will not parse as a graph.',
        'inputSchema': {'type': 'object', 'properties': {
            'data': _str('base64-encoded .onnx bytes'),
            'path': _str('a path to an .onnx file on this box'),
            'url': _str('an http(s) URL to fetch the model from'),
            'name': _str('what to call it (defaults to the filename or its id)'),
            'note': _str('anything worth remembering about where it came from')}},
        'handler': _t_add,
    },
    'infer_inspect': {
        'description': 'What a model IS, read off the graph: opset, producer, '
                       'every operator and how many of each, parameter count and '
                       'how many bytes of the file are weights, the declared '
                       'inputs and outputs including which dimensions are '
                       'symbolic, and a guess at the architecture. The symbolic '
                       'dimensions are the ones you have to decide before you can '
                       'benchmark anything.',
        'inputSchema': {'type': 'object', 'properties': {'model': _REF},
                        'required': ['model']},
        'handler': _t_inspect,
    },
    'infer_plan': {
        'description': 'What is worth trying on THIS model and why, in a form you '
                       'can pass straight to infer_optimize. target=local '
                       'optimizes for time on this machine; target=web weighs '
                       'the download instead, because in a browser the bytes '
                       'crossing the network usually cost more than the '
                       'arithmetic — so it reaches for quantization earlier and '
                       'never includes `all`. The `why` list is the reasoning, '
                       'not decoration — read it before overriding the plan.',
        'inputSchema': {'type': 'object', 'properties': {
            'model': _REF,
            'target': _str('local (default) or web', enum=['local', 'web'])},
            'required': ['model']},
        'handler': _t_plan,
    },
    'infer_passes': {
        'description': 'The catalog of optimization passes: what each one does, '
                       'whether it changes the numbers, whether the result still '
                       'runs in a browser, and whether it is available on this '
                       'box. Also the order they compose in.',
        'inputSchema': {'type': 'object', 'properties': {}},
        'handler': _t_passes,
    },
    'infer_optimize': {
        'description': 'The main tool. Runs passes over a model and reports what '
                       'each one bought: node counts before and after with the '
                       'operators that disappeared, file size, p50 latency before '
                       'and after measured in this process on identical inputs, '
                       'how far the outputs moved, and whether the result still '
                       'runs in a browser. Read `verdict` first. If '
                       '`portability_lost` is present the model got faster here '
                       'and stopped being loadable in a browser — in practice '
                       'that means the `all` pass was used. Default passes are '
                       'slim,extended (both lossless, both browser-safe).',
        'inputSchema': {'type': 'object', 'properties': {
            'model': _REF,
            'passes': _str('comma-separated, in order — slim, shapes, basic, '
                           'extended, all, fp16, int8, uint8 (default '
                           '"slim,extended")'),
            'name': _str('what to call the result'),
            'check': _bool('benchmark and compare against the source (default '
                           'true) — turn it off only for a model too big to run '
                           'twice'),
            'tol': _num('absolute error that still counts as agreement (1e-3)'),
            'samples': _num('input sets to compare outputs on (default 4)'),
            'threads': _num('intra-op threads for the benchmark'),
            **_BENCH}, 'required': ['model']},
        'handler': _t_optimize,
    },
    'infer_bench': {
        'description': 'Time one model: p50, p90, p99, min, max, mean and stdev '
                       'in milliseconds over warmed-up runs on random inputs, '
                       'plus throughput. Percentiles rather than a mean because '
                       'the mean of a cold session measures the allocator. '
                       'Graph optimization is disabled in the session on purpose '
                       'so it measures the binary you handed it, not what '
                       'onnxruntime would have done to it at load time.',
        'inputSchema': {'type': 'object', 'properties': {
            'model': _REF,
            'warmup': _num('untimed runs first (default 5)'),
            'threads': _num('intra-op threads — 1 is the honest number for a '
                            'server running many models at once'),
            'provider': _str('execution provider, e.g. CUDAExecutionProvider — '
                             'errors if this box does not have it'),
            **_BENCH}, 'required': ['model']},
        'handler': _t_bench,
    },
    'infer_parity': {
        'description': 'Feed two models the same seeded random inputs and report '
                       'how far apart the outputs are: max absolute error, max '
                       'relative error, worst-case cosine similarity, and how '
                       'often the argmax still agrees. This is the tool that says '
                       'whether an optimization was free or paid for. Fusion '
                       'should come back identical; quantization will not, and '
                       'argmax agreement is usually what you actually care about.',
        'inputSchema': {'type': 'object', 'properties': {
            'a': _str('the original model'), 'b': _str('the optimized one'),
            'samples': _num('input sets to compare (default 8)'),
            'tol': _num('absolute error that still counts as agreement (1e-3)'),
            **_BENCH}, 'required': ['a', 'b']},
        'handler': _t_parity,
    },
    'infer_portable': {
        'description': 'Will these exact bytes run in a browser? Returns three '
                       'answers, not two: `portable` false for operators no '
                       'browser build registers (com.microsoft.nchwc.* from the '
                       '`all` pass, and anyone else\'s custom domain), '
                       '`portable` true with a `cautions` entry for the '
                       'onnxruntime contrib operators that fusion emits — the '
                       'wasm backend does run those, which was measured in a '
                       'browser rather than assumed — and plain true otherwise. '
                       'A static prediction either way; the console proves it '
                       'by actually running the bytes.',
        'inputSchema': {'type': 'object', 'properties': {'model': _REF},
                        'required': ['model']},
        'handler': _t_portable,
    },
    'infer_compare': {
        'description': 'Apply each pass on its own to the same model and put the '
                       'results in one table: size ratio, speedup, output error '
                       'and portability per pass, with the fastest and smallest '
                       'named. Use it when the plan is not obvious — on small '
                       'graphs the answer is regularly "quantization made it '
                       'worse", and this is how you find that out in one call '
                       'instead of four.',
        'inputSchema': {'type': 'object', 'properties': {
            'model': _REF,
            'passes': _str('which to try, comma-separated (default: basic, '
                           'extended, all, fp16, int8 — the five that change '
                           'the shipping decision)'),
            **_BENCH}, 'required': ['model']},
        'handler': _t_compare,
    },
    'infer_export': {
        'description': 'Turn something that is not ONNX yet into the standard '
                       'binary: torchvision:<name> for a stock architecture, a '
                       '.py file that defines `model` (and optionally `example`), '
                       'or a TorchScript .pt with shape= given. The result goes '
                       'straight into the store ready to optimize. Weights are '
                       'random unless you ask for pretrained ones — fine for '
                       'measuring a graph, useless for measuring accuracy.',
        'inputSchema': {'type': 'object', 'properties': {
            'source': _str('torchvision:resnet18, a path to a .py, or a .pt'),
            'name': _str('what to call it'),
            'opset': _num('ONNX opset to target (default 17)'),
            'shape': _str('example input shape, e.g. "1,3,224,224"'),
            'weights': _str('torchvision weights enum, e.g. "DEFAULT", to '
                            'download pretrained parameters')},
            'required': ['source']},
        'handler': _t_export,
    },
    'infer_examples': {
        'description': 'Plant three models to work on: a feed-forward net with '
                       'BatchNorm to fuse, a small CNN, and a transformer block. '
                       'One of each architecture, so the difference between what '
                       'the passes do to a Conv stack and what they do to '
                       'attention is visible in two calls.',
        'inputSchema': {'type': 'object', 'properties': {}},
        'handler': _t_examples,
    },
    'infer_delete': {
        'description': 'Remove a model from the store. The bytes go too, unless '
                       'another entry has the same SHA-256.',
        'inputSchema': {'type': 'object', 'properties': {'model': _REF},
                        'required': ['model']},
        'handler': _t_delete,
    },
}


# ── JSON-RPC ─────────────────────────────────────────────────────

def version():
    try:
        with open(os.path.join(HERE, 'config.json')) as f:
            return json.load(f).get('version') or '0.0.0'
    except Exception:
        return '0.0.0'


def _result(id_, result):
    return {'jsonrpc': '2.0', 'id': id_, 'result': result}


def _error(id_, code, message):
    return {'jsonrpc': '2.0', 'id': id_, 'error': {'code': code, 'message': message}}


def call_tool(name, args):
    """Run one tool by name. Shared with the REST layer, so a route and an MCP
    tools/call cannot drift apart."""
    tool = TOOLS.get(name)
    if not tool:
        raise InferError(f'no tool named {name!r} — {", ".join(TOOLS)}', status=404)
    args = {k: v for k, v in (args or {}).items() if v is not None}
    for required in tool['inputSchema'].get('required', []):
        if args.get(required) in (None, ''):
            raise InferError(f'{name} needs {required}')
    return tool['handler'](args)


def _call(id_, params):
    name = (params or {}).get('name')
    args = (params or {}).get('arguments') or {}
    try:
        out = call_tool(name, args)
        return _result(id_, {'content': [{'type': 'text',
                                          'text': json.dumps(out, default=str,
                                                             indent=2)}],
                             'structuredContent': out if isinstance(out, dict) else None,
                             'isError': False})
    except InferError as e:
        return _result(id_, {'content': [{'type': 'text',
                                          'text': json.dumps(e.dict(), default=str)}],
                             'isError': True})
    except TypeError as e:
        return _result(id_, {'content': [{'type': 'text',
                                          'text': f'bad arguments for {name}: {e}'}],
                             'isError': True})
    except Exception as e:
        return _result(id_, {'content': [{'type': 'text',
                                          'text': f'{type(e).__name__}: {e}'}],
                             'isError': True})


def handle(body):
    """One JSON-RPC message in, one response out (None for notifications)."""
    if not isinstance(body, dict) or not isinstance(body.get('method'), str):
        id_ = body.get('id') if isinstance(body, dict) else None
        return _error(id_, -32600, 'invalid request: expected a JSON-RPC 2.0 object')
    method, id_, params = body['method'], body.get('id'), body.get('params') or {}
    if id_ is None or method.startswith('notifications/'):
        return None
    if method == 'initialize':
        v = str(params.get('protocolVersion') or '')
        return _result(id_, {
            'protocolVersion': v if v in SUPPORTED_PROTOCOL_VERSIONS
            else DEFAULT_PROTOCOL_VERSION,
            'capabilities': {'tools': {}},
            'serverInfo': {'name': 'infer', 'version': version()},
            'instructions': INSTRUCTIONS,
        })
    if method == 'ping':
        return _result(id_, {})
    if method == 'tools/list':
        return _result(id_, {'tools': tool_list()})
    if method == 'tools/call':
        return _call(id_, params)
    return _error(id_, -32601, f'method not found: {method}')


def tool_list():
    return [{'name': n, 'description': t['description'], 'inputSchema': t['inputSchema']}
            for n, t in TOOLS.items()]


def serve_stdio():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            body = json.loads(line)
        except Exception:
            resp = _error(None, -32700, 'parse error: line is not valid JSON')
        else:
            resp = handle(body)
        if resp is not None:
            sys.stdout.write(json.dumps(resp, default=str) + '\n')
            sys.stdout.flush()


if __name__ == '__main__':
    argv = sys.argv[1:]
    if '--http' in argv:
        import api
        i = argv.index('--port') + 1 if '--port' in argv else -1
        api.serve(int(argv[i]) if i > 0 else int(os.environ.get('PORT', 50820)))
    else:
        serve_stdio()
