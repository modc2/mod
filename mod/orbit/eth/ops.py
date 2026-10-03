"""
The chain operations themselves — one implementation, three faces.

Everything the REST API, the MCP tools and the CLI can do lives here, so the
console and an agent are provably doing the same thing. The functions take
plain JSON-ish values and return plain JSON-ish values; nothing web3-shaped
escapes (see `jsonable`), because an `AttributeDict` full of `HexBytes` is not
something an MCP client can read.

Three rules the whole file obeys:

**Fees are EIP-1559 where the chain supports it.** `fees()` reads the pending
block's base fee and asks the node for a priority tip, then sizes maxFeePerGas
to survive a few blocks of base-fee growth rather than pinning it to this
block's number — a transaction that is only just affordable now is a
transaction that gets stuck. Chains with no base fee fall back to legacy
gasPrice, and which one happened is in the result.

**Spending on a non-testnet needs `confirm=True`.** Not a config flag, not a
default the environment can flip on — an argument the caller has to pass on the
request that spends the money. `local` and the testnets are free.

**Every send is recorded before it is confirmed.** The hash goes in the ledger
the moment the node accepts it, then the receipt updates the row. A crash
between the two leaves a row that says `pending`, which is true, rather than no
row at all, which is a lost transaction.
"""
import json
import os
import time
from collections.abc import Mapping
from typing import Any, Dict, List, Optional, Union

from eth_account import Account
from hexbytes import HexBytes
from web3 import Web3

import chains
import ledger
import wallet

# Interfaces small enough to carry rather than look up.
ERC20_ABI = json.loads("""[
 {"constant":true,"inputs":[],"name":"name","outputs":[{"name":"","type":"string"}],"stateMutability":"view","type":"function"},
 {"constant":true,"inputs":[],"name":"symbol","outputs":[{"name":"","type":"string"}],"stateMutability":"view","type":"function"},
 {"constant":true,"inputs":[],"name":"decimals","outputs":[{"name":"","type":"uint8"}],"stateMutability":"view","type":"function"},
 {"constant":true,"inputs":[],"name":"totalSupply","outputs":[{"name":"","type":"uint256"}],"stateMutability":"view","type":"function"},
 {"constant":true,"inputs":[{"name":"account","type":"address"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"stateMutability":"view","type":"function"},
 {"constant":true,"inputs":[{"name":"owner","type":"address"},{"name":"spender","type":"address"}],"name":"allowance","outputs":[{"name":"","type":"uint256"}],"stateMutability":"view","type":"function"},
 {"inputs":[{"name":"to","type":"address"},{"name":"amount","type":"uint256"}],"name":"transfer","outputs":[{"name":"","type":"bool"}],"stateMutability":"nonpayable","type":"function"},
 {"inputs":[{"name":"spender","type":"address"},{"name":"amount","type":"uint256"}],"name":"approve","outputs":[{"name":"","type":"bool"}],"stateMutability":"nonpayable","type":"function"},
 {"inputs":[{"name":"from","type":"address"},{"name":"to","type":"address"},{"name":"amount","type":"uint256"}],"name":"transferFrom","outputs":[{"name":"","type":"bool"}],"stateMutability":"nonpayable","type":"function"},
 {"anonymous":false,"inputs":[{"indexed":true,"name":"from","type":"address"},{"indexed":true,"name":"to","type":"address"},{"indexed":false,"name":"value","type":"uint256"}],"name":"Transfer","type":"event"}
]""")

ERC721_ABI = json.loads("""[
 {"inputs":[],"name":"name","outputs":[{"name":"","type":"string"}],"stateMutability":"view","type":"function"},
 {"inputs":[],"name":"symbol","outputs":[{"name":"","type":"string"}],"stateMutability":"view","type":"function"},
 {"inputs":[{"name":"owner","type":"address"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"stateMutability":"view","type":"function"},
 {"inputs":[{"name":"tokenId","type":"uint256"}],"name":"ownerOf","outputs":[{"name":"","type":"address"}],"stateMutability":"view","type":"function"},
 {"inputs":[{"name":"tokenId","type":"uint256"}],"name":"tokenURI","outputs":[{"name":"","type":"string"}],"stateMutability":"view","type":"function"}
]""")

WAIT_TIMEOUT = int(os.environ.get('ETH_WAIT_TIMEOUT', 180))


class OpError(Exception):
    """A request that cannot be carried out as asked."""


# ── shapes ───────────────────────────────────────────────────────────

def jsonable(value: Any) -> Any:
    """web3's AttributeDict/HexBytes tree → something json.dumps accepts.

    `Mapping`, not `dict`: web3's AttributeDict is a Mapping and is *not* a
    dict subclass, so an isinstance(…, dict) test walks straight past a receipt
    log and leaves raw HexBytes in the response.
    """
    if isinstance(value, (HexBytes, bytes, bytearray)):
        raw = value.hex()
        return raw if raw.startswith('0x') else '0x' + raw
    if isinstance(value, Mapping):
        return {k: jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [jsonable(v) for v in value]
    if isinstance(value, int) and abs(value) > 2 ** 53:
        return str(value)             # JS would round it; a wei value must not
    return value


def to_checksum(address: str) -> str:
    try:
        return Web3.to_checksum_address(address)
    except Exception:
        raise OpError(f'{address!r} is not an address')


def parse_amount(value: Union[str, int, float, None], decimals: int = 18) -> int:
    """`"0.1"` is 0.1 ETH; `"100000000000000000wei"` and ints are wei.

    Decimal strings are the human unit because that is what a person types, and
    a bare integer is wei because that is what a machine has. Both are common,
    so both are accepted and the ambiguous middle — a float that happens to be
    whole — resolves as the human unit, which is the safer mistake.
    """
    if value in (None, '', 0, '0'):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value * (10 ** decimals))
    text = str(value).strip().lower().replace('_', '').replace(',', '')
    # longest first: 'gwei' also ends with 'wei'
    for suffix, exponent in (('gwei', 9), ('ether', 18), ('eth', 18), ('wei', 0)):
        if text.endswith(suffix):
            number = text[: -len(suffix)].strip() or '0'
            return int(float(number) * (10 ** exponent)) if '.' in number \
                else int(number) * (10 ** exponent)
    if '.' in text or 'e' in text:
        return int(float(text) * (10 ** decimals))
    return int(text)


def from_units(amount: int, decimals: int = 18) -> str:
    """Wei → a decimal string with no float in the middle of it."""
    amount = int(amount)
    sign = '-' if amount < 0 else ''
    amount = abs(amount)
    whole, fraction = divmod(amount, 10 ** decimals)
    if not fraction:
        return f'{sign}{whole}'
    return f'{sign}{whole}.{str(fraction).rjust(decimals, "0").rstrip("0")}'


# ── who and where ────────────────────────────────────────────────────

def resolve_address(value: str, owner: Optional[str] = None,
                    network: Optional[str] = None) -> str:
    """An address, one of your account names, or an ENS name."""
    value = (value or '').strip()
    if not value:
        raise OpError('no address given')
    if value.startswith('0x') and len(value) == 42:
        return to_checksum(value)
    if owner:
        for row in wallet.listing(owner):
            if row['name'].lower() == value.lower():
                return row['address']
    if '.' in value:
        return resolve_ens(value)
    raise OpError(f'{value!r} is not an address, one of your accounts, or an ENS name')


def resolve_ens(name: str) -> str:
    """ENS lives on mainnet whichever chain you are working on."""
    try:
        w3 = chains.client('mainnet')
        address = w3.ens.address(name)
    except Exception as e:
        raise OpError(f'ENS lookup failed for {name}: {e}')
    if not address:
        raise OpError(f'{name} does not resolve')
    return to_checksum(address)


# The cheapest transaction there is. An account that cannot cover this cannot
# cover anything, whatever the contract would have done.
MIN_GAS = 21_000


def _cannot_pay(error: Exception, w3, sender: str,
                spec: Dict[str, Any]) -> Optional[str]:
    """Is this failure "no money", said in one of the RPCs' several dialects?

    Geth says `insufficient funds`; anvil and several L2 nodes say `gas
    required exceeds allowance (1897)` — a number that looks like a gas limit
    and is actually the caller's whole balance expressed in gas. Both mean the
    same thing and neither says it, so an unfunded testnet account reads as a
    broken contract and sends people off to tune gas.

    The dialect only *suggests* the diagnosis; the balance decides it. A
    reverting call on some nodes produces the same phrasing, and telling
    somebody their funded account is broke would be its own wrong answer.
    """
    text = str(error).lower()
    if not ('insufficient funds' in text or 'exceeds allowance' in text
            or 'exceeds the balance' in text):
        return None
    try:
        wei = w3.eth.get_balance(to_checksum(sender))
        floor = int(w3.eth.gas_price) * MIN_GAS
    except Exception:
        return None                     # cannot check ⇒ do not claim
    if wei >= floor:
        return None                     # it can pay; something else is wrong
    currency = spec.get('currency') or 'ETH'
    fix = ('top it up from a faucet, or send it some from an account that has '
           'any' if spec.get('testnet') else 'it needs funding')
    return (f'{sender} cannot pay for this on {spec["name"]}: it holds '
            f'{from_units(wei)} {currency} and the gas alone costs about '
            f'{from_units(floor)} — {fix}.')


def guard(spec: Dict[str, Any], confirm: bool) -> None:
    """Real money asks once, out loud."""
    if chains.is_mainnet(spec) and not confirm:
        raise OpError(
            f'{spec["name"]} is not a testnet — this spends real funds. '
            f'Send confirm=true to go ahead, or use a testnet '
            f'({", ".join(n["name"] for n in chains.summary() if n.get("testnet"))[:120]}).')


# ── reading ──────────────────────────────────────────────────────────

def balance(address: str, network: Optional[str] = None,
            owner: Optional[str] = None, token: Optional[str] = None) -> Dict[str, Any]:
    spec = chains.resolve(network)
    w3 = chains.client(network)
    who = resolve_address(address, owner, network)
    if token:
        return token_balance(token, who, network, owner)
    raw = w3.eth.get_balance(who)
    return {'address': who, 'network': spec['name'], 'chain_id': spec.get('chain_id'),
            'wei': str(raw), 'balance': from_units(raw),
            'symbol': spec.get('currency', 'ETH'),
            'explorer': chains.explorer_link(spec, 'address', who)}


def nonce(address: str, network: Optional[str] = None,
          owner: Optional[str] = None, pending: bool = True) -> Dict[str, Any]:
    w3 = chains.client(network)
    who = resolve_address(address, owner, network)
    return {'address': who,
            'nonce': w3.eth.get_transaction_count(who, 'pending' if pending else 'latest'),
            'confirmed': w3.eth.get_transaction_count(who, 'latest')}


def code(address: str, network: Optional[str] = None,
         owner: Optional[str] = None) -> Dict[str, Any]:
    w3 = chains.client(network)
    who = resolve_address(address, owner, network)
    raw = w3.eth.get_code(who)
    known = ledger.abi_for(who, chains.resolve(network).get('chain_id'), owner)
    return {'address': who, 'is_contract': len(raw) > 0, 'size': len(raw),
            'code': jsonable(raw)[:2000] + ('…' if len(raw) > 1000 else ''),
            'known_as': (known or {}).get('name'),
            'abi_known': bool(known)}


def block(number: Union[int, str, None] = 'latest', network: Optional[str] = None,
          full: bool = False) -> Dict[str, Any]:
    w3 = chains.client(network)
    spec = chains.resolve(network)
    ident: Any = number if number not in (None, '') else 'latest'
    if isinstance(ident, str) and ident.isdigit():
        ident = int(ident)
    got = w3.eth.get_block(ident, full_transactions=bool(full))
    out = jsonable(dict(got))
    if not full:
        out['transactions'] = [jsonable(t) for t in got.get('transactions', [])]
    out['transaction_count'] = len(got.get('transactions', []))
    out['network'] = spec['name']
    out['explorer'] = chains.explorer_link(spec, 'block', str(got.get('number')))
    out.pop('logsBloom', None)          # 512 bytes nobody reads in a console
    return out


def transaction(hash_: str, network: Optional[str] = None) -> Dict[str, Any]:
    w3 = chains.client(network)
    spec = chains.resolve(network)
    try:
        tx = jsonable(dict(w3.eth.get_transaction(hash_)))
    except Exception as e:
        raise OpError(f'no transaction {hash_} on {spec["name"]}: {e}')
    out = {'transaction': tx, 'network': spec['name'],
           'explorer': chains.explorer_link(spec, 'tx', hash_)}
    try:
        receipt = dict(w3.eth.get_transaction_receipt(hash_))
        receipt.pop('logsBloom', None)
        out['receipt'] = jsonable(receipt)
        out['status'] = 'success' if receipt.get('status') == 1 else 'reverted'
    except Exception:
        out['status'] = 'pending'
    if tx.get('value') not in (None, '0', 0):
        out['value'] = from_units(int(tx['value']))
    return out


def wait(hash_: str, network: Optional[str] = None,
         timeout: int = WAIT_TIMEOUT) -> Dict[str, Any]:
    w3 = chains.client(network)
    spec = chains.resolve(network)
    try:
        receipt = w3.eth.wait_for_transaction_receipt(hash_, timeout=timeout)
    except Exception as e:
        return {'hash': hash_, 'status': 'pending', 'waited': timeout,
                'note': f'not mined within {timeout}s ({type(e).__name__}) — '
                        f'it may still land; check again with /tx',
                'explorer': chains.explorer_link(spec, 'tx', hash_)}
    out = jsonable(dict(receipt))
    out.pop('logsBloom', None)
    status = 'success' if receipt.get('status') == 1 else 'reverted'
    ledger.update_tx(hash_, spec.get('chain_id'), status=status,
                     gas_used=receipt.get('gasUsed'), block=receipt.get('blockNumber'))
    return {'hash': hash_, 'status': status, 'receipt': out,
            'gas_used': receipt.get('gasUsed'),
            'block': receipt.get('blockNumber'),
            'contract_address': out.get('contractAddress'),
            'explorer': chains.explorer_link(spec, 'tx', hash_)}


def fees(network: Optional[str] = None) -> Dict[str, Any]:
    """What a transaction costs right now, and the numbers to put in one."""
    w3 = chains.client(network)
    spec = chains.resolve(network)
    out: Dict[str, Any] = {'network': spec['name'], 'chain_id': spec.get('chain_id')}
    latest = w3.eth.get_block('latest')
    base = latest.get('baseFeePerGas')
    out['block'] = latest.get('number')
    try:
        gas_price = w3.eth.gas_price
        out['gas_price'] = str(gas_price)
        out['gas_price_gwei'] = round(gas_price / 1e9, 4)
    except Exception:
        gas_price = None
    if base is not None:
        try:
            tip = w3.eth.max_priority_fee
        except Exception:
            tip = Web3.to_wei(1.5, 'gwei')
        # Two base fees of headroom: the base fee can rise 12.5% a block, so
        # 2× survives ~6 blocks of a full chain without repricing.
        max_fee = base * 2 + tip
        out.update({'eip1559': True, 'base_fee': str(base),
                    'base_fee_gwei': round(base / 1e9, 4),
                    'max_priority_fee': str(tip),
                    'max_priority_fee_gwei': round(tip / 1e9, 4),
                    'max_fee': str(max_fee),
                    'max_fee_gwei': round(max_fee / 1e9, 4)})
    else:
        out.update({'eip1559': False,
                    'note': 'no base fee on this chain — legacy gasPrice is used'})
    if out.get('gas_price'):
        transfer = int(out.get('max_fee') or gas_price or 0) * 21000
        out['transfer_cost'] = from_units(transfer)
        out['transfer_cost_symbol'] = spec.get('currency', 'ETH')
    return out


def storage_at(address: str, slot: Union[int, str], network: Optional[str] = None,
               owner: Optional[str] = None) -> Dict[str, Any]:
    w3 = chains.client(network)
    who = resolve_address(address, owner, network)
    index = int(str(slot), 16) if str(slot).startswith('0x') else int(slot)
    value = w3.eth.get_storage_at(who, index)
    return {'address': who, 'slot': index, 'value': jsonable(value),
            'as_int': str(int.from_bytes(bytes(value), 'big'))}


def logs(network: Optional[str] = None, address: Optional[str] = None,
         from_block: Union[int, str] = 'latest', to_block: Union[int, str] = 'latest',
         topics: Optional[List[Any]] = None, owner: Optional[str] = None,
         limit: int = 200) -> Dict[str, Any]:
    w3 = chains.client(network)
    spec = chains.resolve(network)
    query: Dict[str, Any] = {'fromBlock': _blockish(from_block),
                             'toBlock': _blockish(to_block)}
    if address:
        query['address'] = resolve_address(address, owner, network)
    if topics:
        query['topics'] = topics
    try:
        found = w3.eth.get_logs(query)
    except Exception as e:
        raise OpError(f'get_logs failed: {e}')
    rows = [jsonable(dict(entry)) for entry in found[:limit]]
    decoded = []
    if address:
        known = ledger.abi_for(query['address'], spec.get('chain_id'), owner)
        if known and known.get('abi'):
            decoded = _decode_logs(w3, query['address'], known['abi'], found[:limit])
    return {'network': spec['name'], 'count': len(found), 'returned': len(rows),
            'logs': rows, 'decoded': decoded,
            'truncated': len(found) > len(rows)}


def _blockish(value: Union[int, str]) -> Any:
    if isinstance(value, int):
        return value
    text = str(value)
    return int(text) if text.lstrip('-').isdigit() else text


def _decode_logs(w3, address: str, abi: List[dict], entries) -> List[Dict[str, Any]]:
    contract = w3.eth.contract(address=to_checksum(address), abi=abi)
    out = []
    for event in [e for e in abi if e.get('type') == 'event']:
        try:
            handler = contract.events[event['name']]()
        except Exception:
            continue
        for entry in entries:
            try:
                parsed = handler.process_log(entry)
            except Exception:
                continue
            out.append({'event': event['name'], 'args': jsonable(dict(parsed['args'])),
                        'block': parsed.get('blockNumber'),
                        'tx': jsonable(parsed.get('transactionHash'))})
    return out


# ── contracts: reading ───────────────────────────────────────────────

def contract_at(address: str, network: Optional[str] = None,
                abi: Optional[Any] = None, owner: Optional[str] = None):
    w3 = chains.client(network)
    spec = chains.resolve(network)
    who = resolve_address(address, owner, network)
    if abi is None:
        known = ledger.abi_for(who, spec.get('chain_id'), owner)
        if not known:
            raise OpError(f'no ABI known for {who} on {spec["name"]} — deploy it '
                          f'here, attach one with /contracts, or pass abi=')
        abi = known['abi']
    if isinstance(abi, str):
        abi = json.loads(abi)
    return w3.eth.contract(address=who, abi=abi), who, spec


def interface(address: str, network: Optional[str] = None,
              abi: Optional[Any] = None, owner: Optional[str] = None) -> Dict[str, Any]:
    """What can be done with this contract, split by what it costs."""
    contract, who, spec = contract_at(address, network, abi, owner)
    reads, writes, events = [], [], []
    for entry in contract.abi:
        kind = entry.get('type')
        if kind == 'function':
            row = {'name': entry.get('name'),
                   'inputs': entry.get('inputs', []),
                   'outputs': entry.get('outputs', []),
                   'mutability': entry.get('stateMutability', 'nonpayable'),
                   'signature': _signature(entry)}
            (reads if row['mutability'] in ('view', 'pure') else writes).append(row)
        elif kind == 'event':
            events.append({'name': entry.get('name'), 'inputs': entry.get('inputs', [])})
    known = ledger.abi_for(who, spec.get('chain_id'), owner)
    return {'address': who, 'network': spec['name'],
            'name': (known or {}).get('name'),
            'reads': reads, 'writes': writes, 'events': events,
            'explorer': chains.explorer_link(spec, 'address', who)}


def _signature(entry: dict) -> str:
    args = ','.join(i.get('type', '') for i in entry.get('inputs', []))
    return f"{entry.get('name')}({args})"


def read(address: str, function: str, args: Optional[List[Any]] = None,
         network: Optional[str] = None, abi: Optional[Any] = None,
         owner: Optional[str] = None, block_number: Optional[Any] = None) -> Dict[str, Any]:
    """A view call. Costs nothing, needs no key, changes nothing."""
    contract, who, spec = contract_at(address, network, abi, owner)
    args = _coerce(contract, function, args or [])
    try:
        fn = contract.functions[function](*args)
    except KeyError:
        raise OpError(f'{function!r} is not in this ABI — '
                      f'{", ".join(sorted(contract.functions))[:200]}')
    try:
        value = fn.call(block_identifier=_blockish(block_number)) if block_number \
            else fn.call()
    except Exception as e:
        raise OpError(f'{function} reverted or is not callable: {e}')
    return {'address': who, 'network': spec['name'], 'function': function,
            'args': jsonable(args), 'result': jsonable(value)}


def _coerce(contract, function: str, args: List[Any]) -> List[Any]:
    """Turn JSON-shaped arguments into what the ABI wants.

    A console and an MCP client both send strings; `uint256` wants an int and
    `address` wants a checksum. Doing it here means every caller gets it right
    instead of every caller getting it right separately.
    """
    entries = [e for e in contract.abi
               if e.get('type') == 'function' and e.get('name') == function]
    if not entries:
        return list(args)
    inputs = max(entries, key=lambda e: len(e.get('inputs', []))).get('inputs', [])
    out = []
    for index, value in enumerate(args):
        spec = inputs[index] if index < len(inputs) else {}
        out.append(_coerce_one(spec.get('type', ''), value, spec))
    return out


def _coerce_one(kind: str, value: Any, spec: Optional[Dict[str, Any]] = None) -> Any:
    if kind.startswith('tuple'):
        return _coerce_tuple(kind, value, spec)
    if kind.endswith(']') and isinstance(value, (list, tuple)):
        inner = kind[:kind.rindex('[')]
        return [_coerce_one(inner, v, spec) for v in value]
    if kind.startswith(('uint', 'int')):
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, str):
            text = value.strip()
            return int(text, 16) if text.startswith('0x') else int(float(text)) \
                if '.' in text else int(text)
        return int(value)
    if kind == 'address':
        return to_checksum(value) if isinstance(value, str) else value
    if kind == 'bool':
        if isinstance(value, str):
            return value.strip().lower() in ('1', 'true', 'yes', 'on')
        return bool(value)
    if kind.startswith('bytes'):
        if isinstance(value, str):
            return HexBytes(value) if value.startswith('0x') else value.encode()
        return value
    return value


def _coerce_tuple(kind: str, value: Any, spec: Optional[Dict[str, Any]]) -> Any:
    """A struct argument, the shape every Uniswap-style router takes.

    Solidity structs arrive as a JSON object (keyed by field name) or as an
    array in field order; either way the components have to be coerced one by
    one, because the numbers inside them are the ones that arrive as strings.
    Without this, `exactInputSingle` is unreachable through this module.
    """
    components = (spec or {}).get('components') or []
    if kind.endswith(']'):
        if not isinstance(value, (list, tuple)):
            return value
        inner = kind[:kind.rindex('[')]
        return [_coerce_one(inner, v, spec) for v in value]
    if isinstance(value, dict):
        return tuple(_coerce_one(c.get('type', ''), value.get(c.get('name')), c)
                     for c in components)
    if isinstance(value, (list, tuple)):
        if components and len(components) != len(value):
            raise OpError(f'that struct wants {len(components)} fields '
                          f'({", ".join(c.get("name", "?") for c in components)}), '
                          f'got {len(value)}')
        return tuple(_coerce_one(c.get('type', ''), v, c)
                     for c, v in zip(components, value))
    return value


# ── the one place a transaction is built and sent ────────────────────

def _fee_fields(w3, spec: Dict[str, Any], gas_price: Optional[Any],
                max_fee: Optional[Any], priority_fee: Optional[Any]) -> Dict[str, Any]:
    if gas_price:
        return {'gasPrice': parse_amount(gas_price, 9) if isinstance(gas_price, str)
                and not gas_price.isdigit() else int(gas_price)}
    base = w3.eth.get_block('latest').get('baseFeePerGas')
    if base is None:
        return {'gasPrice': w3.eth.gas_price}
    try:
        tip = int(parse_amount(priority_fee, 9)) if priority_fee else w3.eth.max_priority_fee
    except Exception:
        tip = Web3.to_wei(1.5, 'gwei')
    ceiling = int(parse_amount(max_fee, 9)) if max_fee else base * 2 + tip
    if ceiling < tip:                      # a nonsensical pair the node rejects
        ceiling = base * 2 + tip
    return {'maxFeePerGas': ceiling, 'maxPriorityFeePerGas': tip}


def _submit(owner: str, account: str, tx: Dict[str, Any], spec: Dict[str, Any],
            password: Optional[str], kind: str, detail: Any = None,
            fn: Optional[str] = None) -> Dict[str, Any]:
    """Sign, send, record. Every write in this module ends up here."""
    w3 = chains.client(spec['name'])
    signer = wallet.signer(owner, account, password)
    tx.setdefault('from', signer.address)
    if to_checksum(tx['from']) != to_checksum(signer.address):
        raise OpError('the `from` address is not the account that would sign')
    tx.setdefault('nonce', w3.eth.get_transaction_count(signer.address, 'pending'))
    chain_id = spec.get('chain_id') or w3.eth.chain_id
    tx.setdefault('chainId', chain_id)
    if 'gas' not in tx:
        try:
            estimated = w3.eth.estimate_gas({k: v for k, v in tx.items()
                                             if k not in ('nonce', 'chainId')})
            tx['gas'] = int(estimated * 1.2) + 5000
        except Exception as e:
            raise OpError(f'gas estimation failed — the transaction would revert: {e}')
    signed = signer.sign_transaction(tx)
    raw = getattr(signed, 'raw_transaction', None) or getattr(signed, 'rawTransaction')
    try:
        hash_ = w3.eth.send_raw_transaction(raw)
    except Exception as e:
        raise OpError(f'the node rejected the transaction: {e}')
    hash_hex = jsonable(hash_)
    # A contract creation carries `to = b''`. Left alone that empty-bytes
    # sentinel travels all the way into a JSON response and fails to encode,
    # so it becomes what it means here: no recipient.
    recipient = tx.get('to') or None
    if isinstance(recipient, (bytes, bytearray)):
        recipient = jsonable(recipient) or None
    cost = int(tx.get('gas', 0)) * int(tx.get('maxFeePerGas') or tx.get('gasPrice') or 0)
    ledger.record_tx(owner, kind=kind, network=spec['name'], chain_id=chain_id,
                     hash=hash_hex, sender=signer.address,
                     recipient=recipient, value=str(tx.get('value', 0)),
                     data_size=len(tx.get('data', '')) // 2, fn=fn,
                     status='pending', detail=detail)
    # jsonable over the whole result rather than field by field: everything
    # here came out of web3, and one stray HexBytes is a 500 at the edge.
    return jsonable({'hash': hash_hex, 'from': signer.address, 'to': recipient,
                     'network': spec['name'], 'chain_id': chain_id,
                     'nonce': tx['nonce'], 'gas': tx.get('gas'),
                     'value': from_units(int(tx.get('value', 0))),
                     'max_cost': from_units(cost),
                     'status': 'pending',
                     'explorer': chains.explorer_link(spec, 'tx', hash_hex)})


def send(owner: str, account: str, to: str, value: Union[str, int] = 0,
         network: Optional[str] = None, password: Optional[str] = None,
         data: Optional[str] = None, gas: Optional[int] = None,
         gas_price: Optional[Any] = None, max_fee: Optional[Any] = None,
         priority_fee: Optional[Any] = None, nonce_: Optional[int] = None,
         confirm: bool = False, wait_for: bool = False) -> Dict[str, Any]:
    """Move the native currency (and optionally carry calldata)."""
    spec = chains.resolve(network)
    guard(spec, confirm)
    w3 = chains.client(network)
    recipient = resolve_address(to, owner, network)
    tx: Dict[str, Any] = {'to': recipient, 'value': parse_amount(value)}
    if data:
        tx['data'] = data if data.startswith('0x') else '0x' + data
    if gas:
        tx['gas'] = int(gas)
    if nonce_ is not None:
        tx['nonce'] = int(nonce_)
    tx.update(_fee_fields(w3, spec, gas_price, max_fee, priority_fee))
    out = _submit(owner, account, tx, spec, password, kind='send')
    if wait_for:
        out.update(wait(out['hash'], spec['name']))
    return jsonable(out)


def send_raw(signed: str, network: Optional[str] = None,
             owner: Optional[str] = None, confirm: bool = False) -> Dict[str, Any]:
    """A transaction signed somewhere else — a hardware wallet, another tool."""
    spec = chains.resolve(network)
    guard(spec, confirm)
    w3 = chains.client(network)
    try:
        hash_ = w3.eth.send_raw_transaction(HexBytes(signed))
    except Exception as e:
        raise OpError(f'the node rejected the raw transaction: {e}')
    hash_hex = jsonable(hash_)
    if owner:
        ledger.record_tx(owner, kind='raw', network=spec['name'],
                         chain_id=spec.get('chain_id'), hash=hash_hex,
                         status='pending')
    return {'hash': hash_hex, 'network': spec['name'], 'status': 'pending',
            'explorer': chains.explorer_link(spec, 'tx', hash_hex)}


def estimate(to: Optional[str] = None, data: Optional[str] = None,
             value: Union[str, int] = 0, network: Optional[str] = None,
             from_: Optional[str] = None, owner: Optional[str] = None) -> Dict[str, Any]:
    w3 = chains.client(network)
    spec = chains.resolve(network)
    tx: Dict[str, Any] = {'value': parse_amount(value)}
    if to:
        tx['to'] = resolve_address(to, owner, network)
    if data:
        tx['data'] = data if data.startswith('0x') else '0x' + data
    if from_:
        tx['from'] = resolve_address(from_, owner, network)
    try:
        gas = w3.eth.estimate_gas(tx)
    except Exception as e:
        raise OpError(f'estimation failed (the call would revert): {e}')
    fee = fees(network)
    price = int(fee.get('max_fee') or fee.get('gas_price') or 0)
    return {'gas': gas, 'network': spec['name'],
            'fee_per_gas': str(price),
            'cost': from_units(gas * price),
            'symbol': spec.get('currency', 'ETH')}


def call_raw(to: str, data: str, network: Optional[str] = None,
             from_: Optional[str] = None, owner: Optional[str] = None) -> Dict[str, Any]:
    """eth_call with hand-made calldata, for when there is no ABI at all."""
    w3 = chains.client(network)
    tx: Dict[str, Any] = {'to': resolve_address(to, owner, network),
                          'data': data if data.startswith('0x') else '0x' + data}
    if from_:
        tx['from'] = resolve_address(from_, owner, network)
    try:
        return {'result': jsonable(w3.eth.call(tx))}
    except Exception as e:
        raise OpError(f'call failed: {e}')


# ── contracts: writing ───────────────────────────────────────────────

def write(owner: str, account: str, address: str, function: str,
          args: Optional[List[Any]] = None, network: Optional[str] = None,
          abi: Optional[Any] = None, value: Union[str, int] = 0,
          password: Optional[str] = None, gas: Optional[int] = None,
          gas_price: Optional[Any] = None, max_fee: Optional[Any] = None,
          priority_fee: Optional[Any] = None, confirm: bool = False,
          wait_for: bool = True) -> Dict[str, Any]:
    """A state-changing contract call."""
    spec = chains.resolve(network)
    guard(spec, confirm)
    w3 = chains.client(network)
    contract, who, _ = contract_at(address, network, abi, owner)
    signer = wallet.signer(owner, account, password)
    coerced = _coerce(contract, function, args or [])
    try:
        bound = contract.functions[function](*coerced)
    except KeyError:
        raise OpError(f'{function!r} is not in this ABI — '
                      f'{", ".join(sorted(contract.functions))[:200]}')
    tx: Dict[str, Any] = {'from': signer.address, 'value': parse_amount(value),
                          'nonce': w3.eth.get_transaction_count(signer.address, 'pending')}
    tx.update(_fee_fields(w3, spec, gas_price, max_fee, priority_fee))
    if gas:
        tx['gas'] = int(gas)
    try:
        built = bound.build_transaction(tx)
    except Exception as e:
        broke = _cannot_pay(e, w3, signer.address, spec)
        raise OpError(broke or f'{function} would revert — not sending it: {e}')
    out = _submit(owner, account, dict(built), spec, password, kind='write',
                  fn=function, detail={'address': who, 'args': jsonable(coerced)})
    out['function'] = function
    out['contract'] = who
    if wait_for:
        out.update(wait(out['hash'], spec['name']))
    return jsonable(out)


def deploy(owner: str, account: str, network: Optional[str] = None,
           source: Optional[str] = None, sources: Optional[Dict[str, str]] = None,
           contract: Optional[str] = None, abi: Optional[Any] = None,
           bytecode: Optional[str] = None, args: Optional[List[Any]] = None,
           value: Union[str, int] = 0, password: Optional[str] = None,
           solc: Optional[str] = None, optimize: bool = True, runs: int = 200,
           evm_version: Optional[str] = None, gas: Optional[int] = None,
           gas_price: Optional[Any] = None, max_fee: Optional[Any] = None,
           priority_fee: Optional[Any] = None, name: Optional[str] = None,
           confirm: bool = False, note: Optional[str] = None,
           wait_for: bool = True) -> Dict[str, Any]:
    """Solidity (or a prebuilt abi+bytecode) to an address on a chain.

    Compiling and deploying are one call because splitting them means the
    caller carries a bytecode blob between two requests and can get them out of
    step. `/compile` still exists for looking before leaping.
    """
    import compiler

    spec = chains.resolve(network)
    guard(spec, confirm)
    w3 = chains.client(network)

    compiled = None
    source_map = sources or ({'Contract.sol': source} if source else None)
    if source_map:
        compiled = compiler.compile_sources(source_map, version=solc,
                                            optimize=optimize, runs=runs,
                                            evm_version=evm_version)
        deployable = [c for c in compiled['contracts'] if c['deployable']]
        if contract:
            chosen = next((c for c in deployable if c['name'] == contract), None)
            if chosen is None:
                raise OpError(f'{contract!r} is not in this source — found '
                              f'{", ".join(c["name"] for c in deployable)}')
        elif len(deployable) == 1:
            chosen = deployable[0]
        else:
            # Solidity puts the entry contract last by convention, but guessing
            # which of several the caller meant is how you deploy the wrong one.
            raise OpError('this source declares several deployable contracts '
                          f'({", ".join(c["name"] for c in deployable)}) — '
                          f'name one with contract=')
        abi, bytecode = chosen['abi'], chosen['bytecode']
        name = name or chosen['name']
    if not abi or not bytecode:
        raise OpError('give me either `source` to compile, or `abi` and `bytecode`')
    if isinstance(abi, str):
        abi = json.loads(abi)
    if not bytecode.startswith('0x'):
        bytecode = '0x' + bytecode
    if set(bytecode[2:]) & set('_$'):
        raise OpError('this bytecode has unlinked libraries in it — pass '
                      'libraries={"Name": "0x…"} when compiling')

    factory = w3.eth.contract(abi=abi, bytecode=bytecode)
    ctor_inputs = next((e.get('inputs', []) for e in abi
                        if e.get('type') == 'constructor'), [])
    supplied = list(args or [])
    if len(supplied) != len(ctor_inputs):
        raise OpError(f'the constructor takes {len(ctor_inputs)} argument(s) '
                      f'({", ".join(i["type"] + " " + (i.get("name") or "") for i in ctor_inputs) or "none"}) '
                      f'and got {len(supplied)}')
    # the spec goes along too: a struct constructor argument (a tuple type)
    # is only coerced field by field when its components are known
    coerced = [_coerce_one(spec_i['type'], value_i, spec_i)
               for spec_i, value_i in zip(ctor_inputs, supplied)]

    signer = wallet.signer(owner, account, password)
    tx: Dict[str, Any] = {'from': signer.address, 'value': parse_amount(value),
                          'nonce': w3.eth.get_transaction_count(signer.address, 'pending')}
    tx.update(_fee_fields(w3, spec, gas_price, max_fee, priority_fee))
    if gas:
        tx['gas'] = int(gas)
    try:
        built = factory.constructor(*coerced).build_transaction(tx)
    except Exception as e:
        broke = _cannot_pay(e, w3, signer.address, spec)
        raise OpError(broke or f'the constructor would revert — not deploying: {e}')

    sent = _submit(owner, account, dict(built), spec, password, kind='deploy',
                   fn='constructor',
                   detail={'contract': name, 'args': jsonable(coerced)})
    out = dict(sent)
    out['contract'] = name
    out['abi'] = abi

    address = None
    if wait_for:
        mined = wait(sent['hash'], spec['name'])
        out.update({k: v for k, v in mined.items() if k != 'receipt'})
        out['receipt'] = mined.get('receipt')
        address = mined.get('contract_address')
        if mined.get('status') == 'reverted':
            out['error'] = 'the deployment transaction reverted — no contract'
            return jsonable(out)
    if address:
        out['address'] = to_checksum(address)
        out['explorer'] = chains.explorer_link(spec, 'address', out['address'])
        out['code_on_chain'] = len(w3.eth.get_code(out['address'])) > 0
        row = ledger.record_deployment(
            owner, name=name or 'Contract', network=spec['name'],
            chain_id=spec.get('chain_id') or w3.eth.chain_id,
            address=out['address'], deployer=signer.address, tx_hash=sent['hash'],
            block=out.get('block'), abi=abi, bytecode=bytecode,
            source=(source_map or {}).get(next(iter(source_map))) if source_map else None,
            source_name=next(iter(source_map)) if source_map else None,
            compiler=(compiled or {}).get('compiler'),
            constructor_args=jsonable(coerced), value=str(parse_amount(value)),
            gas_used=out.get('gas_used'), note=note)
        out['id'] = row['id']
    if compiled:
        out['compiler'] = compiled['compiler']
        out['warnings'] = compiled['warnings']
    return jsonable(out)


# ── tokens ───────────────────────────────────────────────────────────

def token_info(address: str, network: Optional[str] = None,
               owner: Optional[str] = None) -> Dict[str, Any]:
    w3 = chains.client(network)
    spec = chains.resolve(network)
    who = resolve_address(address, owner, network)
    contract = w3.eth.contract(address=who, abi=ERC20_ABI)
    out: Dict[str, Any] = {'address': who, 'network': spec['name'],
                           'explorer': chains.explorer_link(spec, 'token', who)}
    for field in ('name', 'symbol', 'decimals', 'totalSupply'):
        try:
            out[{'totalSupply': 'total_supply'}.get(field, field)] = \
                jsonable(contract.functions[field]().call())
        except Exception:
            out[{'totalSupply': 'total_supply'}.get(field, field)] = None
    if out.get('name') is None and out.get('symbol') is None:
        raise OpError(f'{who} does not answer like an ERC-20 on {spec["name"]}')
    decimals = int(out.get('decimals') or 18)
    if out.get('total_supply') is not None:
        out['supply'] = from_units(int(out['total_supply']), decimals)
    return out


def token_balance(token: str, address: str, network: Optional[str] = None,
                  owner: Optional[str] = None) -> Dict[str, Any]:
    w3 = chains.client(network)
    spec = chains.resolve(network)
    token_address = resolve_address(token, owner, network)
    who = resolve_address(address, owner, network)
    contract = w3.eth.contract(address=token_address, abi=ERC20_ABI)
    raw = contract.functions.balanceOf(who).call()
    try:
        decimals = int(contract.functions.decimals().call())
    except Exception:
        decimals = 18
    try:
        symbol = contract.functions.symbol().call()
    except Exception:
        symbol = '?'
    return {'token': token_address, 'address': who, 'network': spec['name'],
            'raw': str(raw), 'balance': from_units(raw, decimals),
            'decimals': decimals, 'symbol': symbol}


def token_transfer(owner: str, account: str, token: str, to: str,
                   amount: Union[str, int], network: Optional[str] = None,
                   password: Optional[str] = None, confirm: bool = False,
                   wait_for: bool = True) -> Dict[str, Any]:
    w3 = chains.client(network)
    token_address = resolve_address(token, owner, network)
    contract = w3.eth.contract(address=token_address, abi=ERC20_ABI)
    try:
        decimals = int(contract.functions.decimals().call())
    except Exception:
        decimals = 18
    units = parse_amount(amount, decimals)
    out = write(owner, account, token_address, 'transfer',
                [resolve_address(to, owner, network), units], network=network,
                abi=ERC20_ABI, password=password, confirm=confirm, wait_for=wait_for)
    out['amount'] = from_units(units, decimals)
    out['token'] = token_address
    return out


def token_approve(owner: str, account: str, token: str, spender: str,
                  amount: Union[str, int], network: Optional[str] = None,
                  password: Optional[str] = None, confirm: bool = False,
                  wait_for: bool = True) -> Dict[str, Any]:
    w3 = chains.client(network)
    token_address = resolve_address(token, owner, network)
    contract = w3.eth.contract(address=token_address, abi=ERC20_ABI)
    try:
        decimals = int(contract.functions.decimals().call())
    except Exception:
        decimals = 18
    units = (2 ** 256 - 1) if str(amount).lower() in ('max', 'unlimited', 'infinite') \
        else parse_amount(amount, decimals)
    out = write(owner, account, token_address, 'approve',
                [resolve_address(spender, owner, network), units], network=network,
                abi=ERC20_ABI, password=password, confirm=confirm, wait_for=wait_for)
    out['approved'] = 'unlimited' if units > 2 ** 255 else from_units(units, decimals)
    return out


def portfolio(owner: str, address: Optional[str] = None,
              networks: Optional[List[str]] = None) -> Dict[str, Any]:
    """One address across several chains — the answer to "where is my money".

    Chains that do not answer are reported as errors rather than dropped: a
    silently missing chain reads as a zero balance, which is a different and
    much worse claim.
    """
    if address:
        target = resolve_address(address, owner)
        accounts = [{'name': None, 'address': target}]
    else:
        accounts = [{'name': a['name'], 'address': a['address']}
                    for a in wallet.listing(owner)]
    names = networks or [n['name'] for n in chains.summary()
                         if n['name'] in ('local', 'mainnet', 'sepolia', 'base',
                                          'base-sepolia', 'arbitrum', 'optimism')]
    rows = []
    for account in accounts:
        for network in names:
            try:
                got = balance(account['address'], network)
                if got['wei'] != '0' or network == 'local':
                    rows.append({'account': account['name'], **got})
            except Exception as e:
                rows.append({'account': account['name'], 'address': account['address'],
                             'network': network, 'error': f'{type(e).__name__}: {e}'})
    return {'accounts': accounts, 'networks': names, 'balances': rows}
