#!/usr/bin/env python3
"""near abi — infer a contract's interface from whatever the chain gives up.

NEAR keeps no ABI on chain. What it does keep is enough to reconstruct one:

1. **Embedded ABI.** Contracts built with `cargo near build` export a
   `__contract_abi` view method that returns a zstd-compressed near-abi JSON
   document — every function, its kind (view/call), modifiers (init, payable,
   private), each argument's JSON schema, the result type. When it is there
   the interface is exact and this module simply decodes it.

2. **Probing.** Every method is an exported WASM function, and a view call is
   free, so each one gets called with `{}` and the panic text read back:
   - `ProhibitedInView { method_name }` — the method touches storage or the
     predecessor: it is a change method.
   - `missing field `x`` — a required argument named x. Fill it with `null`
     and serde says what it wanted: `invalid type: null, expected a string`.
     Repeat until the method stops asking.
   - `Requires attached deposit` — payable; "exactly 1 yoctoNEAR" is the
     NEP-141 transfer convention.
   - `already been initialized` — the init method.
   - a clean result — a view method, and the sample is kept.

3. **The standards.** NEP-141/145/148/171/177/178/181/245/330 fix the names,
   argument shapes and deposits of the methods everyone uses, so a method
   called `ft_transfer` is known before any probe; the probe then confirms.

4. **History.** The indexer's recent receipts to the contract carry the real
   JSON args of real calls — examples, and argument names the probe cannot
   reach (a change method's own fields, or contracts whose panics do not name
   them). Best-effort: the indexer is rate-limited and sometimes absent.

The result is one spec per contract, cached under ~/.mod/near/abi.{network}.json
by code hash, that a generic page can turn into forms — which is the point:
one HTML that interfaces with any NEAR contract.
"""

import base64
import json
import os
import re
import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from chain import NearError

HOME = os.path.expanduser(os.environ.get('NEAR_HOME') or '~/.mod/near')
MAX_CALLS = int(os.environ.get('NEAR_ABI_MAX_CALLS', 400))   # floor; grows 5×methods
ROUNDS = int(os.environ.get('NEAR_ABI_ROUNDS', 12))
WORKERS = int(os.environ.get('NEAR_ABI_WORKERS', 4))
HISTORY_ROWS = 25

_lock = threading.Lock()
_store = {}          # network -> {account_id: spec}
_loaded = set()


# ── the standards: what the names already tell us ────────────────

_ACCT = {'type': 'account_id', 'example': 'root.near'}
_U128 = {'type': 'u128', 'example': '1000000000000000000000000',
         'note': 'yocto units as a decimal string'}
_STR = {'type': 'string'}
_OPT_STR = {'type': 'string', 'required': False}
_OPT_ACCT = {'type': 'account_id', 'required': False, 'example': 'root.near'}
_U64 = {'type': 'number', 'example': 0}
_OPT_U64 = {'type': 'number', 'required': False}


def _std(standard, kind, args=(), deposit=None, result=None, note=None):
    out = {'standard': standard, 'kind': kind,
           'args': [{'name': n, 'required': True, **t} for n, t in args]}
    if deposit:
        out['deposit'] = deposit
    if result:
        out['result'] = result
    if note:
        out['note'] = note
    return out


NEP = {
    # NEP-141 fungible token core
    'ft_transfer': _std('nep141', 'change',
                        [('receiver_id', _ACCT), ('amount', _U128), ('memo', _OPT_STR)],
                        deposit='1 yocto'),
    'ft_transfer_call': _std('nep141', 'change',
                             [('receiver_id', _ACCT), ('amount', _U128),
                              ('msg', {'type': 'string', 'example': ''}),
                              ('memo', _OPT_STR)], deposit='1 yocto',
                             result='PromiseOrValue<U128>'),
    'ft_total_supply': _std('nep141', 'view', result='U128'),
    'ft_balance_of': _std('nep141', 'view', [('account_id', _ACCT)], result='U128'),
    'ft_on_transfer': _std('nep141', 'change',
                           [('sender_id', _ACCT), ('amount', _U128), ('msg', _STR)],
                           note='receiver-side hook — called by the token, not by you'),
    'ft_resolve_transfer': _std('nep141', 'change',
                                [('sender_id', _ACCT), ('receiver_id', _ACCT),
                                 ('amount', _U128)],
                                note='callback — private, the contract calls itself'),
    # NEP-148 FT metadata
    'ft_metadata': _std('nep148', 'view', result='FungibleTokenMetadata'),
    # NEP-145 storage management
    'storage_deposit': _std('nep145', 'change',
                            [('account_id', _OPT_ACCT),
                             ('registration_only', {'type': 'boolean', 'required': False})],
                            deposit='positive', result='StorageBalance',
                            note='attach at least storage_balance_bounds.min'),
    'storage_withdraw': _std('nep145', 'change',
                             [('amount', {**_U128, 'required': False})],
                             deposit='1 yocto', result='StorageBalance'),
    'storage_unregister': _std('nep145', 'change',
                               [('force', {'type': 'boolean', 'required': False})],
                               deposit='1 yocto', result='bool'),
    'storage_balance_bounds': _std('nep145', 'view', result='StorageBalanceBounds'),
    'storage_balance_of': _std('nep145', 'view', [('account_id', _ACCT)],
                               result='StorageBalance|null'),
    'storage_minimum_balance': _std('nep145', 'view', result='U128',
                                    note='pre-NEP-145 name for storage_balance_bounds.min'),
    # NEP-171 NFT core
    'nft_transfer': _std('nep171', 'change',
                         [('receiver_id', _ACCT), ('token_id', {'type': 'string', 'example': '1'}),
                          ('approval_id', _OPT_U64), ('memo', _OPT_STR)], deposit='1 yocto'),
    'nft_transfer_call': _std('nep171', 'change',
                              [('receiver_id', _ACCT), ('token_id', _STR),
                               ('msg', _STR), ('approval_id', _OPT_U64),
                               ('memo', _OPT_STR)], deposit='1 yocto'),
    'nft_token': _std('nep171', 'view', [('token_id', {'type': 'string', 'example': '1'})],
                      result='Token|null'),
    'nft_on_transfer': _std('nep171', 'change',
                            [('sender_id', _ACCT), ('previous_owner_id', _ACCT),
                             ('token_id', _STR), ('msg', _STR)],
                            note='receiver-side hook'),
    'nft_resolve_transfer': _std('nep171', 'change',
                                 [('previous_owner_id', _ACCT), ('receiver_id', _ACCT),
                                  ('token_id', _STR)], note='callback — private'),
    # NEP-177 NFT metadata
    'nft_metadata': _std('nep177', 'view', result='NFTContractMetadata'),
    # NEP-178 approvals
    'nft_approve': _std('nep178', 'change',
                        [('token_id', _STR), ('account_id', _ACCT), ('msg', _OPT_STR)],
                        deposit='positive'),
    'nft_revoke': _std('nep178', 'change', [('token_id', _STR), ('account_id', _ACCT)],
                       deposit='1 yocto'),
    'nft_revoke_all': _std('nep178', 'change', [('token_id', _STR)], deposit='1 yocto'),
    'nft_is_approved': _std('nep178', 'view',
                            [('token_id', _STR), ('approved_account_id', _ACCT),
                             ('approval_id', _OPT_U64)], result='bool'),
    # NEP-181 enumeration
    'nft_total_supply': _std('nep181', 'view', result='U128'),
    'nft_tokens': _std('nep181', 'view',
                       [('from_index', {**_U128, 'required': False, 'example': '0'}),
                        ('limit', {**_OPT_U64, 'example': 20})], result='Token[]'),
    'nft_supply_for_owner': _std('nep181', 'view', [('account_id', _ACCT)], result='U128'),
    'nft_tokens_for_owner': _std('nep181', 'view',
                                 [('account_id', _ACCT),
                                  ('from_index', {**_U128, 'required': False, 'example': '0'}),
                                  ('limit', {**_OPT_U64, 'example': 20})], result='Token[]'),
    # NEP-245 multi token
    'mt_transfer': _std('nep245', 'change',
                        [('receiver_id', _ACCT), ('token_id', _STR), ('amount', _U128),
                         ('approval', {'type': 'json', 'required': False}),
                         ('memo', _OPT_STR)], deposit='1 yocto'),
    'mt_batch_transfer': _std('nep245', 'change',
                              [('receiver_id', _ACCT),
                               ('token_ids', {'type': 'array', 'example': ['1']}),
                               ('amounts', {'type': 'array', 'example': ['1']}),
                               ('approvals', {'type': 'json', 'required': False}),
                               ('memo', _OPT_STR)], deposit='1 yocto'),
    'mt_balance_of': _std('nep245', 'view', [('account_id', _ACCT), ('token_id', _STR)],
                          result='U128'),
    'mt_batch_balance_of': _std('nep245', 'view',
                                [('account_id', _ACCT),
                                 ('token_ids', {'type': 'array', 'example': ['1']})],
                                result='U128[]'),
    'mt_supply': _std('nep245', 'view', [('token_id', _STR)], result='U128|null'),
    'mt_batch_supply': _std('nep245', 'view',
                            [('token_ids', {'type': 'array', 'example': ['1']})]),
    'mt_metadata_contract': _std('nep245', 'view'),
    'mt_tokens': _std('nep245', 'view',
                      [('from_index', {**_U128, 'required': False}),
                       ('limit', _OPT_U64)]),
    'mt_tokens_for_owner': _std('nep245', 'view',
                                [('account_id', _ACCT),
                                 ('from_index', {**_U128, 'required': False}),
                                 ('limit', _OPT_U64)]),
    # NEP-330 source metadata
    'contract_source_metadata': _std('nep330', 'view', result='ContractSourceMetadata'),
    # the init convention
    'new': _std('init', 'init', note='constructor — runs once, at deploy'),
    'new_default_meta': _std('init', 'init', [('owner_id', _ACCT)]),
    'init': _std('init', 'init'),
    'migrate': _std('init', 'change', note='state migration after an upgrade — owner only'),
}

# Name shapes that lean one way when nothing else is known.
_VIEW_PREFIX = ('get_', 'is_', 'has_', 'view_', 'list_', 'check_', 'calc_',
                'compute_', 'total_', 'num_', 'count_', 'find_', 'query_', 'read_')
_CHANGE_PREFIX = ('set_', 'add_', 'remove_', 'update_', 'delete_', 'create_',
                  'register_', 'withdraw', 'deposit', 'claim', 'mint', 'burn',
                  'stake', 'unstake', 'swap', 'transfer', 'approve', 'revoke',
                  'pause', 'unpause', 'execute', 'submit', 'vote', 'propose',
                  'cancel', 'finalize', 'resolve', 'on_', 'callback', 'do_',
                  'sign', 'upgrade', 'store', 'write', 'push', 'pop', 'clear',
                  'reset', 'init', 'migrate', 'rollback', 'lock', 'unlock')


def _name_hint(name):
    if name.startswith(_VIEW_PREFIX):
        return 'view'
    if name.startswith(_CHANGE_PREFIX) or any(
            name.startswith(p) for p in ('ft_', 'nft_', 'mt_')) and name.endswith('_call'):
        return 'change'
    return None


# ── reading the panics ───────────────────────────────────────────

_RE_PROHIBITED = re.compile(r'ProhibitedInView \{ method_name: "([^"]+)" \}')
_RE_MISSING = re.compile(r'missing field `([^`]+)`')
_RE_INVALID_TYPE = re.compile(r'invalid type: ([^,]+), expected (.+?)(?:",|`|$| at line)')
_RE_INVALID_VALUE = re.compile(r'invalid value: ([^,]+), expected (.+?)(?:",|`|$| at line)')
# near-account-id's own message has no "expected" clause:
#   invalid value: "x", the Account ID is too short at line 1 column 13
_RE_INVALID_VALUE2 = re.compile(r'invalid value: ([^,]+), (.+?)(?:",|`|$| at line| line:)')
_RE_COLUMN = re.compile(r'column:? (\d+)')
_RE_UNKNOWN_FIELD = re.compile(r'unknown field `([^`]+)`(?:, expected (?:one of )?(.+?))?(?:",|`|$| at line)')
_RE_UNKNOWN_VARIANT = re.compile(r'unknown variant `([^`]+)`, expected (?:one of )?(.+?)(?:",|`|$| at line)')
_RE_PANIC = re.compile(r'panic_msg: "(.*)" \}\)?\s*$', re.S)
_RE_DEPOSIT_1 = re.compile(r'exactly 1 ?yocto', re.I)
_RE_DEPOSIT = re.compile(r'attached[ _]deposit|requires? (?:positive|a) deposit|deposit (?:is )?required|insufficient deposit', re.I)
_RE_INIT = re.compile(r'already (?:been )?initiali[sz]ed', re.I)
_RE_NOT_INIT = re.compile(r'not (?:been )?initiali[sz]ed', re.I)
_RE_PRIVATE = re.compile(r'(?:method|function) [\w`"]+ is private|is a private method|private method|only callable by the contract', re.I)
_RE_OWNER = re.compile(r'(?:only|must be) (?:the )?(?:owner|admin|contract owner|operator|controller)|unauthori[sz]ed|not allowed|not authori[sz]ed|permission denied|no permission', re.I)
_RE_SERDE = re.compile(r'(?:Failed to deserialize|deserialize input|Cannot deserialize|JSON)', re.I)
_RE_EXPECTED_INPUT = re.compile(r'Expected input since method has arguments', re.I)
_RE_NEAR_SDK_JS = re.compile(r'Cannot read propert(?:y|ies) of undefined \(reading \'([^\']+)\'\)|Cannot destructure property \'([^\']+)\'', re.I)


def _panic_text(error):
    """The human part of an RPC call_function error string."""
    if not error:
        return ''
    m = _RE_PANIC.search(error)
    text = m.group(1) if m else error
    return (text.replace('\\"', '"').replace('\\n', '\n')
            .replace('\\\\', '\\').strip())


def _serde_type(expected):
    """serde's "expected ..." phrase → a form-friendly type label."""
    e = (expected or '').strip().strip('`"').lower()
    if not e:
        return 'json'
    if 'borrowed string' in e or e in ('a string', 'string', 'a str', 'str'):
        return 'string'
    if 'boolean' in e or e == 'bool':
        return 'boolean'
    if re.match(r'^(u|i)(8|16|32|64|128|size)$', e) or 'integer' in e or e in ('a number', 'number', 'f32', 'f64', 'a float'):
        return 'number'
    if 'sequence' in e or 'array' in e or 'tuple' in e:
        return 'array'
    if 'map' in e or 'struct' in e or 'object' in e or 'enum' in e:
        return 'object'
    if 'one of' in e or e.startswith('`'):
        return 'string'
    return 'json'


def _sample(arg_type, name=''):
    """A value of the inferred type that will get past serde — so the next
    probe round can reveal the next field, not the same one again."""
    n = name.lower()
    if arg_type == 'account_id' or n.endswith('_id') and 'token' not in n or n in ('account', 'owner', 'receiver', 'sender', 'token'):
        return 'root.near' if arg_type in ('account_id', 'string', 'json') else _sample(arg_type)
    if arg_type in ('u128', 'string'):
        return '0' if n in ('amount', 'from_index', 'token_id') or n.endswith('amount') else 'x'
    if arg_type == 'number':
        return 0
    if arg_type == 'boolean':
        return False
    if arg_type == 'array':
        return []
    if arg_type == 'object':
        return {}
    return None


_GUESS = [
    (re.compile(r'(^|_)(account|owner|receiver|sender|signer|predecessor|contract|'
                r'token_contract|user|to|from|beneficiary|operator|admin|'
                r'validator|delegator|recipient)(_id)?$|_id$|_account$'),
     ('account_id', 'root.near')),
    (re.compile(r'(^|_)(amount|balance|supply|price|from_index|deposit|fee|'
                r'min_amount|max_amount|value|shares|stake|yocto)(_[a-z]+)?$|_amount$'),
     ('u128', '0')),
    (re.compile(r'^(limit|skip|page|offset|index|count|n|decimals|nonce|'
                r'approval_id|block_height|timestamp|duration|epoch|gas|'
                r'id|num|size|level|round|weight|ratio|bps|pips)$|_(index|count|len|id|height|num|ms|ns|sec)$'),
     ('number', 0)),
    (re.compile(r'^(force|registration_only|only|enabled|active|paused|is_[a-z_]+|'
                r'should_[a-z_]+|has_[a-z_]+|allow_[a-z_]+)$|_(enabled|only|flag)$'),
     ('boolean', False)),
    (re.compile(r'^(memo|msg|message|name|symbol|text|role|title|uri|url|'
                r'description|reference|icon|key|value|label|note|comment|'
                r'public_key|signature|hash|data|method|method_name|version|'
                r'token_id|intent|standard|spec)$|_(id|name|key|hash|uri|url|msg|str|text)$'),
     ('string', 'x')),
    (re.compile(r'(ids|keys|tokens|amounts|accounts|roles|args|list|items|'
                r'signed|intents|actions|proposals|values|members|entries)$'),
     ('array', [])),
    (re.compile(r'^(config|metadata|params|options|opts|settings|policy|'
                r'args|payload|message_json|data_json)$'),
     ('object', {})),
]


def _guess(name):
    """A first guess at a field's type from its name, so the probe can skip
    a round. Wrong guesses cost one call: serde names the column, and the
    field is refilled with what it asked for."""
    n = name.lower()
    if n == 'token_id':
        return 'string', 'x'
    for rx, hit in _GUESS:
        if rx.search(n):
            return hit
    return None, None


def read_panic(error):
    """One call_function error → what it says about the method.
    Pure: covered by the offline tests, no chain involved."""
    out = {'kind': None, 'missing': None, 'type_for': None, 'notes': []}
    if not error:
        out['kind'] = 'view'
        return out
    text = _panic_text(error)
    out['text'] = text
    if 'MethodNotFound' in error:
        out['kind'] = 'missing'
        return out
    m = _RE_PROHIBITED.search(error)
    if m:
        out['kind'] = 'change'
        out['host_fn'] = m.group(1)
        return out
    m = _RE_MISSING.search(text)
    if m:
        out['missing'] = m.group(1)
        return out
    col = _RE_COLUMN.search(text)
    if col:
        out['column'] = int(col.group(1))
    m = _RE_INVALID_TYPE.search(text) or _RE_INVALID_VALUE.search(text)
    if m:
        out['type_for'] = {'given': m.group(1).strip(), 'expected': m.group(2).strip(),
                           'type': _serde_type(m.group(2))}
        return out
    m = _RE_INVALID_VALUE2.search(text)
    if m:
        expected = m.group(2).strip()
        t = 'account_id' if 'account id' in expected.lower() else _serde_type(expected)
        out['type_for'] = {'given': m.group(1).strip(), 'expected': expected, 'type': t}
        return out
    m = _RE_UNKNOWN_VARIANT.search(text)
    if m:
        out['type_for'] = {'given': m.group(1), 'expected': m.group(2).strip(),
                           'type': 'string',
                           'options': [x.strip(' `') for x in m.group(2).split(',')]}
        return out
    if _RE_EXPECTED_INPUT.search(text):
        out['needs_input'] = True
        return out
    m = _RE_NEAR_SDK_JS.search(text)
    if m:
        out['missing'] = m.group(1) or m.group(2)
        out['notes'].append('near-sdk-js')
        return out
    if _RE_INIT.search(text):
        out['kind'] = 'init'
        return out
    if _RE_NOT_INIT.search(text):
        out['kind'] = 'unknown'
        out['notes'].append('contract state is not initialized')
        return out
    if _RE_DEPOSIT_1.search(text):
        out['kind'] = 'change'
        out['deposit'] = '1 yocto'
        return out
    if _RE_DEPOSIT.search(text):
        out['kind'] = 'change'
        out['deposit'] = 'positive'
        return out
    if _RE_PRIVATE.search(text):
        out['kind'] = 'change'
        out['private'] = True
        return out
    if _RE_OWNER.search(text):
        out['kind'] = 'change'
        out['restricted'] = True
        return out
    if _RE_SERDE.search(text):
        # older near-account-id: Error("The account ID is invalid", line: 1,
        # column: 44) — no "expected" clause, but the column names the field
        if out.get('column') and re.search(r'account id', text, re.I):
            out['type_for'] = {'given': '?', 'expected': 'an account id',
                               'type': 'account_id'}
            return out
        out['needs_input'] = True
        return out
    # The method ran business logic and panicked without touching anything a
    # view cannot — that is what a view method does with bad input.
    out['kind'] = 'view?'
    return out


# ── the embedded near-abi document ───────────────────────────────

def _zstd(blob):
    """Decompress zstd bytes with whatever this host has: the stdlib (3.14+),
    the zstandard package, or the zstd binary. None = no decoder here."""
    try:
        from compression import zstd as z          # Python 3.14
        return z.decompress(blob)
    except Exception:
        pass
    try:
        import zstandard
        return zstandard.ZstdDecompressor().decompressobj().decompress(blob)
    except Exception:
        pass
    exe = shutil.which('zstd')
    if exe:
        p = subprocess.run([exe, '-d', '-c'], input=blob, capture_output=True, timeout=20)
        if p.returncode == 0:
            return p.stdout
    return None


def _deref(schema, defs, depth=0):
    """Follow $ref once so a type label can be read; keep the raw schema.
    JSON Schema lets a node be a bare `true`/`false` — those read as {}."""
    if not isinstance(schema, dict):
        return {}, None
    if depth > 4:
        return schema, None
    ref = schema.get('$ref')
    if ref and ref.startswith('#/definitions/'):
        name = ref[len('#/definitions/'):]
        inner, _ = _deref(defs.get(name) or {}, defs, depth + 1)
        return inner, name
    if 'allOf' in schema and len(schema['allOf']) == 1:
        return _deref(schema['allOf'][0], defs, depth + 1)
    if 'anyOf' in schema:
        # Option<T> is anyOf[T, null]
        parts = [p for p in schema['anyOf']
                 if not (isinstance(p, dict) and p.get('type') == 'null')]
        if len(parts) == 1:
            inner, name = _deref(parts[0], defs, depth + 1)
            return inner, name
    return schema, None


def _schema_type(schema, defs):
    inner, name = _deref(schema, defs)
    desc = (inner or {}).get('description') or '' if isinstance(inner, dict) else ''
    if name in ('AccountId', 'AccountIdRef') or desc.startswith('NEAR Account Identifier'):
        return 'account_id', name or 'AccountId'
    if desc.startswith('String representation of') and 'u128' in desc:
        return 'u128', name or 'U128'
    if name in ('U128', 'U64', 'NearToken', 'Balance', 'Gas'):
        return 'u128', name
    t = (inner or {}).get('type')
    if isinstance(t, list):
        t = [x for x in t if x != 'null'][:1] or ['json']
        t = t[0]
    if name == 'Base64VecU8':
        return 'string', name
    if t == 'string':
        return ('string' if not (inner or {}).get('enum') else 'string'), name
    if t in ('integer', 'number'):
        return 'number', name
    if t == 'boolean':
        return 'boolean', name
    if t == 'array':
        return 'array', name
    if t == 'object' or 'properties' in (inner or {}):
        return 'object', name
    if (inner or {}).get('oneOf') or (inner or {}).get('enum'):
        return 'string' if (inner or {}).get('enum') else 'json', name
    return 'json', name


def _arg_example(arg_type, name, inner):
    enum = (inner or {}).get('enum') if isinstance(inner, dict) else None
    if enum:
        return enum[0]
    if arg_type == 'account_id':
        return 'root.near'
    if arg_type == 'u128':
        return '0'
    if arg_type == 'number':
        return 0
    if arg_type == 'boolean':
        return False
    if arg_type == 'array':
        return []
    if arg_type == 'object':
        props = (inner or {}).get('properties') if isinstance(inner, dict) else None
        if props:
            return {k: None for k in list(props)[:12]}
        return {}
    return ''


def parse_near_abi(doc):
    """A near-abi JSON document → this module's method list."""
    body = doc.get('body') or {}
    defs = (body.get('root_schema') or {}).get('definitions') or {}
    methods = []
    for f in body.get('functions') or []:
        mods = f.get('modifiers') or []
        kind = 'view' if f.get('kind') == 'view' else 'change'
        if 'init' in mods:
            kind = 'init'
        args = []
        params = f.get('params') or {}
        for a in params.get('args') or []:
            schema = a.get('type_schema') or {}
            t, named = _schema_type(schema, defs)
            inner, _ = _deref(schema, defs)
            optional = isinstance(schema, dict) and (
                'null' in (schema.get('type') if isinstance(schema.get('type'), list) else [])
                or any(isinstance(p, dict) and p.get('type') == 'null'
                       for p in schema.get('anyOf') or []))
            arg = {'name': a.get('name'), 'type': t, 'required': not optional,
                   'example': _arg_example(t, a.get('name'), inner)}
            if named:
                arg['named_type'] = named
            if isinstance(inner, dict) and inner.get('enum'):
                arg['options'] = inner['enum']
            if isinstance(inner, dict) and inner.get('description') and not named:
                arg['description'] = inner['description'][:160]
            arg['schema'] = schema
            args.append(arg)
        m = {'name': f.get('name'), 'kind': kind, 'args': args, 'source': 'abi',
             'confidence': 1.0, 'payable': 'payable' in mods,
             'private': 'private' in mods}
        if 'payable' in mods:
            m['deposit'] = 'positive'
        if params.get('serialization_type') == 'borsh':
            m['serialization'] = 'borsh'
            m['note'] = 'borsh-serialized params — the JSON form cannot encode these'
        res = f.get('result') or {}
        if res.get('type_schema') is not None:
            rt, rname = _schema_type(res['type_schema'], defs)
            m['result'] = rname or rt
        if f.get('doc'):
            m['doc'] = str(f['doc'])[:400]
        methods.append(m)
    meta = doc.get('metadata') or {}
    return {'methods': methods, 'types': defs,
            'metadata': {'name': meta.get('name'), 'version': meta.get('version'),
                         'build': meta.get('build'), 'schema_version': doc.get('schema_version')}}


# ── probing ──────────────────────────────────────────────────────

class _Budget:
    def __init__(self, n):
        self.left = n
        self.lock = threading.Lock()

    def take(self):
        with self.lock:
            if self.left <= 0:
                return False
            self.left -= 1
            return True


def _field_at(args, column):
    """Which top-level field a serde column number points into. The probe
    serialises args with json.dumps defaults, so the layout is known."""
    if not args or not column:
        return None
    s, spans = '{', []
    for i, (k, v) in enumerate(args.items()):
        if i:
            s += ', '
        s += json.dumps(k) + ': '
        start = len(s)
        s += json.dumps(v)
        spans.append((k, start, len(s)))
    for k, start, end in spans:
        if start < column <= end + 1:
            return k
    before = [k for k, start, _ in spans if start < column]
    return before[-1] if before else None


def _probe_one(client, account_id, name, budget, deep=True, seed=None,
               resume=None, rounds=None):
    """Call one method as a view, repeatedly, until it stops asking for
    fields. Returns {kind, args, ...} for the method. resume= continues an
    earlier shallow result instead of starting from {} again."""
    args = dict(seed or {})
    found = {}        # name -> {'type':..., ...} in order of discovery
    order = []
    out = {'name': name, 'source': 'probe', 'calls': 0}
    pending_type = None      # field we set to null to learn its type
    if resume:
        args = dict(resume.get('_args') or {})
        for a in resume.get('args') or []:
            found[a['name']] = dict(a)
            order.append(a['name'])
        out['calls'] = resume.get('calls', 0)
        pending_type = resume.get('_pending')
    done = False
    for _ in range(rounds or (ROUNDS if deep else 1)):
        if not budget.take():
            out['note'] = 'probe budget exhausted'
            break
        out['calls'] += 1
        err, result = client.view_raw(account_id, name, args)
        p = read_panic(err)
        if p.get('missing'):
            f = p['missing']
            if f in found:                  # same field twice — give up on it
                done = True
                break
            gt, gv = _guess(f)
            found[f] = {'name': f, 'type': gt or 'json', 'required': True}
            order.append(f)
            if gt:
                found[f]['guessed'] = True
                pending_type = None
                args[f] = gv               # a telling name — try it directly
            else:
                pending_type = f
                args[f] = None             # ask serde what it wants
            continue
        if p.get('type_for') and pending_type:
            t = p['type_for']
            found[pending_type]['type'] = t['type']
            found[pending_type]['expected'] = t['expected']
            if t.get('options'):
                found[pending_type]['options'] = t['options']
            n = pending_type
            if t['type'] == 'string' and (n.endswith('_id') or n in ('account', 'owner', 'receiver', 'sender')) \
                    and 'token' not in n:
                found[n]['type'] = 'account_id'
            args[n] = found[n].get('options', [None])[0] if found[n].get('options') \
                else _sample(found[n]['type'], n)
            pending_type = None
            continue
        if p.get('type_for'):
            # a type complaint about a field we filled — the column says
            # which; give it the shape serde asked for and go again
            t = p['type_for']
            broken = _field_at(args, p.get('column')) or \
                next((k for k in reversed(order) if k in args), None)
            if broken:
                found.setdefault(broken, {'name': broken, 'type': 'json', 'required': True})
                if broken not in order:
                    order.append(broken)
                tries = found[broken].get('retried', 0)
                if tries < 2 and (t['type'] != found[broken].get('type') or tries == 0):
                    found[broken]['retried'] = tries + 1
                    found[broken]['type'] = t['type']
                    found[broken]['expected'] = t['expected']
                    args[broken] = _sample(t['type'], broken)
                    continue
            out['note'] = f'args shape unclear: {t["given"]} vs {t["expected"]}'
            done = True
            break
        if p.get('needs_input'):
            out['needs_input'] = True
            out['panic'] = (p.get('text') or '')[:200]
            if 'near-sdk-js' in p['notes']:
                out['note'] = 'near-sdk-js contract — argument names come from history'
            done = True
            break
        kind = p.get('kind')
        if kind == 'view' and err is None:
            out['kind'] = 'view'
            out['sample'] = _trim(result)
            done = True
            break
        if kind:
            done = True
            out['kind'] = kind
            for k in ('deposit', 'private', 'restricted', 'host_fn'):
                if p.get(k) is not None:
                    out[k] = p[k]
            if p.get('text') and kind in ('view?', 'unknown'):
                out['panic'] = p['text'][:200]
            if p.get('notes'):
                out['note'] = '; '.join(p['notes'])
            break
    out['args'] = [dict(found[n]) for n in order]
    if 'kind' not in out:
        out['kind'] = 'unknown'
        if not done:
            out['_args'] = args          # so a deeper pass can pick up here
            out['_pending'] = pending_type
            out['unfinished'] = True
            if out['calls'] >= ROUNDS:
                out['note'] = f'still asking for fields after {ROUNDS} probes'
    return out


def _trim(v, n=400):
    s = json.dumps(v, default=str)
    if len(s) <= n:
        return v
    return {'_truncated': s[:n] + '…'}


# ── history: real args from real calls ───────────────────────────

def _history_examples(client, account_id):
    """{method: {'args': {...}, 'deposit_yocto': int, 'seen': n}} from the
    indexer's recent receipts to this contract. Empty on any trouble."""
    try:
        r = client._indexer(f'/v1/account/{account_id}/txns?per_page={HISTORY_ROWS}',
                            f'history for {account_id}')
    except Exception:
        return {}
    out = {}
    for row in r.get('txns') or []:
        if row.get('receiver_account_id') != account_id:
            continue
        for a in row.get('actions') or []:
            if a.get('action') != 'FUNCTION_CALL' or not a.get('method'):
                continue
            m = a['method']
            e = out.setdefault(m, {'seen': 0, 'args': None, 'deposit_yocto': 0,
                                   'callers': set()})
            e['seen'] += 1
            e['callers'].add(row.get('predecessor_account_id'))
            try:
                dep = int(a.get('deposit') or 0)
            except (TypeError, ValueError):
                dep = 0
            e['deposit_yocto'] = max(e['deposit_yocto'], dep)
            if e['args'] is None:
                try:
                    parsed = json.loads(a.get('args') or '{}')
                    if isinstance(parsed, dict):
                        e['args'] = parsed
                except Exception:
                    pass
    for e in out.values():
        callers = e.pop('callers')
        e['self_called'] = callers == {account_id}
    return out


def _json_type(v):
    if isinstance(v, bool):
        return 'boolean'
    if isinstance(v, (int, float)):
        return 'number'
    if isinstance(v, str):
        return 'u128' if v.isdigit() and len(v) > 12 else 'string'
    if isinstance(v, list):
        return 'array'
    if isinstance(v, dict):
        return 'object'
    return 'json'


# ── merge ────────────────────────────────────────────────────────

def _merge(name, abi_m, probe_m, std_m, hist_m):
    """One method from up to four opinions, best evidence first."""
    m = {'name': name, 'args': [], 'kind': 'unknown', 'confidence': 0.0,
         'source': []}
    if abi_m:
        m.update({k: v for k, v in abi_m.items() if k != 'source'})
        m['source'] = ['abi']
        m['confidence'] = 1.0
    elif probe_m and probe_m.get('kind') not in (None, 'unknown', 'view?'):
        m['kind'] = probe_m['kind']
        m['confidence'] = 0.9
        m['source'] = ['probe']
    elif std_m:
        m['kind'] = std_m['kind']
        m['confidence'] = 0.8
        m['source'] = ['standard']
    elif probe_m and probe_m.get('kind') == 'view?':
        if _name_hint(name) == 'change':
            m['kind'] = 'change'
            m['confidence'] = 0.5
            m['source'] = ['probe', 'name']
            m['note'] = 'panicked on input before touching state; the name says change'
        else:
            m['kind'] = 'view'
            m['confidence'] = 0.6
            m['source'] = ['probe']
            m['note'] = 'ran as a view and panicked on input — no state touched, so view'
    else:
        hint = _name_hint(name)
        if hint:
            m['kind'] = hint
            m['confidence'] = 0.4
            m['source'] = ['name']
        elif hist_m and hist_m.get('self_called'):
            m['kind'] = 'change'
            m['confidence'] = 0.4
            m['source'] = ['history']
            m['note'] = 'only ever called by the contract itself — a callback'
    if std_m:
        m['standard'] = std_m['standard']
        if 'standard' not in m['source']:
            m['source'].append('standard')
        if std_m.get('result') and not m.get('result'):
            m['result'] = std_m['result']
        if std_m.get('note') and not m.get('note'):
            m['note'] = std_m['note']
        if std_m.get('deposit') and not m.get('deposit'):
            m['deposit'] = std_m['deposit']
    if probe_m:
        if 'probe' not in m['source']:
            m['source'].append('probe')
        for k in ('deposit', 'private', 'restricted', 'host_fn', 'sample', 'panic'):
            if probe_m.get(k) is not None and m.get(k) is None:
                m[k] = probe_m[k]
        m['probe_calls'] = probe_m.get('calls', 0)
        if probe_m.get('note') and not m.get('note'):
            m['note'] = probe_m['note']

    # args: abi > standard > probe, then history fills names and examples
    args = {a['name']: dict(a) for a in m.get('args') or []}
    order = list(args)
    if not args and std_m:
        for a in std_m['args']:
            args[a['name']] = dict(a)
            order.append(a['name'])
    if probe_m:
        for a in probe_m.get('args') or []:
            if a['name'] in args:
                cur = args[a['name']]
                if cur.get('type') in (None, 'json') and a.get('type'):
                    cur['type'] = a['type']
                if a.get('options') and not cur.get('options'):
                    cur['options'] = a['options']
                cur['required'] = True
            else:
                args[a['name']] = {k: v for k, v in a.items() if k != 'retried'}
                order.append(a['name'])
    if hist_m and hist_m.get('args'):
        for k, v in hist_m['args'].items():
            if k not in args:
                args[k] = {'name': k, 'type': _json_type(v), 'required': v is not None,
                           'from': 'history'}
                order.append(k)
            if v not in (None, ''):          # a real call beats a placeholder
                args[k]['example'] = v
            if args[k].get('type') in (None, 'json'):
                args[k]['type'] = _json_type(v)
        m['seen'] = hist_m['seen']
        if hist_m.get('deposit_yocto', 0) > 0:
            m['deposit_seen_yocto'] = str(hist_m['deposit_yocto'])
            if not m.get('deposit'):
                m['deposit'] = '1 yocto' if hist_m['deposit_yocto'] == 1 else 'positive'
        if 'history' not in m['source']:
            m['source'].append('history')
    for a in args.values():
        a.setdefault('type', 'json')
        a.setdefault('required', True)
        if a.get('example') is None:
            a['example'] = _arg_example(a['type'], a['name'], None)
    m['args'] = [args[n] for n in order]
    m['payable'] = bool(m.get('deposit')) or bool(m.get('payable'))
    if m['kind'] == 'init':
        m['payable'] = m.get('payable', False)
    m['source'] = list(dict.fromkeys(m['source']))
    return m


# ── the store ────────────────────────────────────────────────────

def _path(network):
    return os.path.join(HOME, f'abi.{network}.json')


def _load(network):
    with _lock:
        if network in _loaded:
            return _store.setdefault(network, {})
        _loaded.add(network)
        try:
            with open(_path(network)) as f:
                _store[network] = json.load(f)
        except Exception:
            _store[network] = {}
        return _store[network]


def _save(network):
    with _lock:
        data = _store.get(network) or {}
        try:
            os.makedirs(HOME, exist_ok=True)
            tmp = _path(network) + '.tmp'
            with open(tmp, 'w') as f:
                json.dump(data, f)
            os.replace(tmp, _path(network))
        except Exception:
            pass


def cached(network, account_id):
    return _load(network).get(account_id)


# ── the entry point ──────────────────────────────────────────────

def infer(client, account_id, refresh=False, deep=True, history=True):
    """The interface of one contract, from every source available, cached by
    code hash. deep=False does one probe per method (kind only, first field);
    history=False skips the indexer."""
    account_id = str(account_id).strip().lower()
    t0 = time.time()
    info = client.contract(account_id)
    if not info.get('is_contract'):
        return info
    store = _load(client.network)
    hit = store.get(account_id)
    if hit and not refresh and hit.get('code_hash') == info.get('code_hash') \
            and (hit.get('deep') or not deep):
        return {**hit, 'cached': True}

    exports = info['methods']
    spec = {'account_id': account_id, 'network': client.network,
            'code_hash': info.get('code_hash'), 'code_bytes': info.get('code_bytes'),
            'deep': deep, 'sources': [], 'methods': [], 'standards': [],
            'metadata': {}}

    # 1. embedded ABI
    abi_methods = {}
    if info.get('abi_embedded'):
        err, raw = client.view_raw(account_id, '__contract_abi', {}, raw=True)
        if not err and raw:
            plain = _zstd(raw) if raw[:4] == b'\x28\xb5\x2f\xfd' else raw
            if plain:
                try:
                    parsed = parse_near_abi(json.loads(plain))
                    abi_methods = {m['name']: m for m in parsed['methods']}
                    spec['types'] = parsed['types']
                    spec['metadata'] = parsed['metadata']
                    spec['sources'].append('abi')
                except Exception as e:
                    spec['abi_error'] = f'embedded ABI unreadable: {e}'
            else:
                spec['abi_error'] = ('embedded ABI is zstd-compressed and this host '
                                     'has no decoder — pip install zstandard, or '
                                     'apt install zstd')
        elif err:
            spec['abi_error'] = _panic_text(err)[:160]

    # 2. NEP-330 source metadata, standards
    if 'contract_source_metadata' in exports:
        err, meta = client.view_raw(account_id, 'contract_source_metadata', {})
        if not err and isinstance(meta, dict):
            spec['metadata'].update({k: meta.get(k) for k in
                                     ('version', 'link', 'build_info') if meta.get(k)})
            for s in meta.get('standards') or []:
                if isinstance(s, dict) and s.get('standard'):
                    spec['standards'].append(f"{s['standard']}{'-' + str(s.get('version')) if s.get('version') else ''}")
            spec['sources'].append('nep330')

    # 3. probe whatever the ABI did not settle
    to_probe = [n for n in exports if n not in abi_methods]
    # a method needs ~1 call per argument plus one for the verdict, so big
    # contracts get a budget that grows with them instead of starving
    total_budget = max(MAX_CALLS, 5 * len(to_probe))
    budget = _Budget(total_budget)
    probed = {}
    if to_probe:
        seeds = {n: {a['name']: a.get('example') for a in NEP[n]['args']
                     if a.get('required', True)}
                 for n in to_probe if n in NEP}
        with ThreadPoolExecutor(max_workers=WORKERS) as ex:
            # pass 1: one call each, so a big contract spends its budget
            # evenly — every method gets a verdict or its first field
            for r in ex.map(lambda n: _probe_one(client, account_id, n, budget,
                                                 deep=False, seed=seeds.get(n)),
                            to_probe):
                probed[r['name']] = r
            # pass 2: keep asking the ones that asked for more, round-robin
            # one call at a time until they settle or the budget is gone
            if deep:
                open_ = [n for n in to_probe if probed[n].get('unfinished')]
                for _ in range(ROUNDS):
                    if not open_ or budget.left <= 0:
                        break
                    for r in ex.map(lambda n: _probe_one(client, account_id, n, budget,
                                                         resume=probed[n], rounds=1),
                                    open_):
                        probed[r['name']] = r
                    open_ = [n for n in open_ if probed[n].get('unfinished')
                             and probed[n]['calls'] < ROUNDS]
        for r in probed.values():
            r.pop('_args', None)
            r.pop('_pending', None)
        spec['sources'].append('probe')
        spec['probe_calls'] = total_budget - budget.left

    # 4. history
    hist = _history_examples(client, account_id) if history else {}
    if hist:
        spec['sources'].append('history')

    # merge
    for name in exports:
        spec['methods'].append(_merge(name, abi_methods.get(name), probed.get(name),
                                      NEP.get(name), hist.get(name)))
    seen_std = {m['standard'] for m in spec['methods'] if m.get('standard')}
    for s in sorted(seen_std - {'init'}):
        if not any(x.startswith(s) for x in spec['standards']):
            spec['standards'].append(s)
    counts = {}
    for m in spec['methods']:
        counts[m['kind']] = counts.get(m['kind'], 0) + 1
    spec['kinds'] = counts
    spec['method_count'] = len(spec['methods'])
    spec['inferred_at'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    spec['took_s'] = round(time.time() - t0, 2)
    spec['note'] = ('kind: view = free read; change = signed call; init = runs once at '
                    'deploy. confidence 1.0 = embedded ABI, 0.9 = the chain said so '
                    'when probed, 0.8 = a NEP standard, lower = a guess from the name.')
    try:
        import directory
        directory.note(client.network, account_id, code_hash=spec['code_hash'],
                       methods=exports)
    except Exception:
        pass
    store[account_id] = spec
    _save(client.network)
    return spec


def template(spec, method):
    """{arg: example} for one method — what a form or a CLI starts from."""
    m = next((x for x in spec.get('methods') or [] if x['name'] == method), None)
    if not m:
        raise NearError(f'{spec.get("account_id")} has no method {method!r}')
    return {a['name']: a.get('example') for a in m['args']}
