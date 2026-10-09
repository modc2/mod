"""
openhouse mcp — Model Context Protocol endpoint (Streamable HTTP transport).

POST /mcp speaks JSON-RPC 2.0, so any MCP client (Claude, IDEs, agent
frameworks) can drive the rent-to-own protocol as a set of tools: read the
live deal, quote a payment split, record rent, check a renter's equity,
pull the cap table, and compare OpenHouse against every other on-chain
housing project — without a bespoke SDK.

Self-contained by design: the JSON-RPC handling is hand-rolled (no `mcp`
package) and every tool calls the same Mod instance the REST API serves,
so the numbers a model reads here are the numbers the site shows.

The router is built by ``build_router(get_mod, version)`` rather than
importing api.py — that keeps the dependency pointing one way (api.py →
this module) and lets the tests drive the tools against a Mod of their own.

Auth: none, deliberately. The openhouse REST surface is public and this is
the same surface; every write tool is a testnet bookkeeping entry, not a
signed transaction. Nothing here can move real money.

The file is named mcp_server.py, not mcp.py, so it can never shadow the
`mcp` package on sys.path — api/ is put on the path directly by uvicorn's
--app-dir, which would make a local mcp.py win for the whole process.
"""
import inspect
import json
from typing import Callable, Optional

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

# Echo the client's protocol version when we know it; otherwise pin the
# oldest revision whose feature set (plain-JSON Streamable HTTP, tools)
# this server fully implements.
SUPPORTED_PROTOCOL_VERSIONS = ('2025-06-18', '2025-03-26', '2024-11-05')
DEFAULT_PROTOCOL_VERSION = '2025-03-26'

INSTRUCTIONS = (
    'OpenHouse is rent-to-own housing on-chain: the protocol takes 0-5% '
    '(owner-set, hard-capped in the contract) and the remaining 95-100% of '
    'every payment stays with the property, split between the renter\'s '
    'equity and the owner\'s income by whichever rent-to-own model the '
    'owner picked. Start with openhouse_terms (the live deal) and '
    'openhouse_quote (what one payment actually buys). No auth is needed. '
    'This deployment is on testnet: the tools that write '
    '(openhouse_pay_rent, openhouse_purchase, openhouse_set_terms, '
    'openhouse_claim_owner, openhouse_distribute, openhouse_toggle_active, '
    'openhouse_transfer_authority) record local bookkeeping entries, not signed '
    'on-chain transactions, and no real money or deed is involved. '
    'New here? openhouse_examples lists guided testnet walkthroughs and '
    'openhouse_run_example runs one in a throwaway store and returns every '
    'tool call it made. Banks: openhouse_bank_* connect any bank — a local '
    'sandbox (testnet, open), statement files (camt.053/MT940/OFX/CSV in, '
    'pain.001 out), Open Banking APIs (Berlin Group PSD2, UK OBIE) or a '
    'bank\'s own MCP server — and openhouse_bank_reconcile books every '
    'transfer carrying a renter\'s reference code onto the rent ledger once. '
    'Before passing a manual rate= to openhouse_bank_reconcile, call '
    'openhouse_fx to inspect the live ETH/fiat rate (CoinGecko, 15-min cache; '
    'source field shows whether the answer is live, stale or a fallback). '
    'Anything but the sandbox is a real bank and needs the operator\'s key. '
    'Fee pool: openhouse_pool shows the quarter accruing now; '
    'openhouse_pool_history lists closed quarters; '
    'openhouse_close_quarter freezes a quarter once the 90 days are up; '
    'openhouse_pool_claim pulls an address\'s share; '
    'openhouse_bloctime shows one address\'s locked liquidity and earned weight. '
    'Civic seat: openhouse_civic_charter seats a government authority on the '
    'property, openhouse_civic_override issues pause/unpause/hold/release, '
    'and openhouse_civic_resign vacates the authority seat (lifts any pause '
    'or hold it placed).'
)


class ToolError(Exception):
    """A tool refused the call — reported to the client as isError, not 500."""


def _req(args: dict, key: str) -> str:
    v = str(args.get(key) or '').strip()
    if not v:
        raise ToolError(f'{key} required')
    return v


def _num(args: dict, key: str, default=None) -> float:
    raw = args.get(key)
    if raw is None or raw == '':
        if default is None:
            raise ToolError(f'{key} required')
        return float(default)
    try:
        return float(raw)
    except (TypeError, ValueError):
        raise ToolError(f'{key} must be a number — got {raw!r}')


def _ok(result):
    """Mod methods report failure as {'error': ...}; surface it as a tool error."""
    if isinstance(result, dict) and 'error' in result:
        raise ToolError(str(result['error']))
    return result


# ── tool handlers (thin wrappers over the Mod instance) ──

def _t_status(args, oh):
    return oh.status()


def _t_property(args, oh):
    return oh.property()


def _t_terms(args, oh):
    return oh.terms()


def _t_models(args, oh):
    return oh.models()


def _t_quote(args, oh):
    return _ok(oh.quote(_num(args, 'amount'), kind=str(args.get('kind') or 'rent')))


def _t_rent_stats(args, oh):
    return oh.rent_stats()


def _t_rent_ledger(args, oh):
    ledger = oh.rent_ledger(str(args.get('renter') or ''))
    limit = int(_num(args, 'limit', 100))
    return {'payments': len(ledger), 'ledger': ledger[:max(limit, 1)]}


def _t_equity(args, oh):
    return oh.equity(_req(args, 'address'))


def _t_shareholders(args, oh):
    holders = oh.shareholders()
    return {'count': len(holders), 'shareholders': holders}


def _t_portfolio(args, oh):
    return oh.portfolio(_req(args, 'address'))


def _t_dividends(args, oh):
    history = oh.dividends()
    return {'distributions': len(history), 'history': history}


def _t_civic(args, oh):
    return oh.civic()


def _t_landscape(args, oh):
    return oh.compare(refresh=bool(args.get('refresh')))


def _t_fx(args, oh):
    return oh.fx(refresh=bool(args.get('refresh')))


def _t_source(args, oh):
    """Manifest by default; one file's full text when asked for by name.

    The three source files run to tens of thousands of tokens together, so
    handing back every byte on an unqualified call would blow a context
    window to answer "what's in here?".
    """
    files = oh.source()
    name = str(args.get('name') or '').strip()
    if not name:
        return {'files': [{k: v for k, v in f.items() if k != 'content'} for f in files]}
    for f in files:
        if f['name'] == name or f['name'].endswith('/' + name) or f['name'].split('/')[-1] == name:
            return f
    raise ToolError(f"no such source file: {name} — have "
                    f"{', '.join(f['name'] for f in files)}")


def _t_pay_rent(args, oh):
    return _ok(oh.pay_rent(_req(args, 'renter'), _num(args, 'amount'),
                           kind=str(args.get('kind') or 'rent')))


def _t_distribute(args, oh):
    return _ok(oh.distribute(_num(args, 'total_amount'),
                             owner=str(args.get('owner') or '')))


def _t_purchase(args, oh):
    return _ok(oh.purchase(_req(args, 'buyer'), int(_num(args, 'share_count')),
                           _num(args, 'payment', 0)))


def _t_set_terms(args, oh):
    fields = ('model', 'fee_pct', 'credit_pct', 'option_fee_pct',
              'home_price', 'monthly_rent', 'owner', 'treasury')
    kwargs = {k: args[k] for k in fields if args.get(k) is not None}
    if not kwargs:
        raise ToolError('nothing to set — pass at least one of: ' + ', '.join(fields))
    return _ok(oh.set_terms(**kwargs))


def _t_claim_owner(args, oh):
    return _ok(oh.claim_owner(_req(args, 'address')))


def _t_civic_charter(args, oh):
    return _ok(oh.civic_charter(_req(args, 'key'), name=str(args.get('name') or ''),
                                region=str(args.get('region') or ''),
                                uri=str(args.get('uri') or ''), owner=args.get('owner')))


def _t_civic_override(args, oh):
    return _ok(oh.civic_override(_req(args, 'action'), _req(args, 'key'),
                                 reason=str(args.get('reason') or '')))


def _t_civic_resign(args, oh):
    return _ok(oh.civic_resign(_req(args, 'key')))


def _t_toggle_active(args, oh):
    return _ok(oh.toggle_active(owner=str(args.get('owner') or '')))


def _t_transfer_authority(args, oh):
    return _ok(oh.transfer_authority(_req(args, 'new_authority'),
                                     caller=str(args.get('caller') or '')))


# ── testnet examples ──

def _t_examples(args, oh):
    ex = oh.examples()
    return {'count': len(ex), 'examples': ex}


def _t_run_example(args, oh):
    return _ok(oh.example(_req(args, 'name')))


# ── the bank rail ──
# Every bank tool takes an optional `key`: the sandbox needs none, a real
# bank connection needs the operator bank key and says so when it is missing.

def _key(args):
    return str(args.get('key') or '')


def _t_bank_kinds(args, oh):
    return {'kinds': oh.bank_kinds()}


def _t_bank_status(args, oh):
    return oh.bank_status()


def _t_bank_connect(args, oh):
    cfg = args.get('config') or {}
    if not isinstance(cfg, dict):
        raise ToolError('config must be an object')
    return _ok(oh.bank_connect(str(args.get('kind') or 'sandbox'),
                               name=str(args.get('name') or ''), config=cfg, key=_key(args)))


def _t_bank_disconnect(args, oh):
    return _ok(oh.bank_disconnect(_req(args, 'connection'), key=_key(args)))


def _t_bank_accounts(args, oh):
    return {'accounts': _ok(oh.bank_accounts(str(args.get('connection') or ''), key=_key(args)))}


def _t_bank_transactions(args, oh):
    rows = _ok(oh.bank_transactions(str(args.get('connection') or ''),
                                    account=str(args.get('account') or ''),
                                    since=int(_num(args, 'since', 0)),
                                    limit=int(_num(args, 'limit', 100)), key=_key(args)))
    return {'count': len(rows), 'transactions': rows}


def _t_bank_import(args, oh):
    return _ok(oh.bank_import(_req(args, 'content'), connection=str(args.get('connection') or ''),
                              format=str(args.get('format') or 'auto'), key=_key(args)))


def _t_bank_receive(args, oh):
    return _ok(oh.bank_receive(_num(args, 'amount'), reference=str(args.get('reference') or ''),
                               from_name=str(args.get('from_name') or ''),
                               from_iban=str(args.get('from_iban') or ''),
                               account=str(args.get('account') or ''),
                               connection=str(args.get('connection') or ''),
                               date=int(_num(args, 'date', 0))))


def _t_bank_reference(args, oh):
    return _ok(oh.bank_reference(_req(args, 'address')))


def _t_bank_link(args, oh):
    return _ok(oh.bank_link(_req(args, 'address'), payer_iban=str(args.get('payer_iban') or ''),
                            payer_name=str(args.get('payer_name') or ''),
                            kind=str(args.get('kind') or 'rent'), key=_key(args)))


def _t_bank_unlink(args, oh):
    return _ok(oh.bank_unlink(_req(args, 'address'), key=_key(args)))


def _t_bank_links(args, oh):
    links = oh.bank_links(key=_key(args))
    return {'count': len(links), 'links': links}


def _t_bank_reconcile(args, oh):
    rate = args.get('rate')
    return _ok(oh.bank_reconcile(str(args.get('connection') or ''),
                                 account=str(args.get('account') or ''),
                                 since=int(_num(args, 'since', 0)),
                                 dry_run=bool(args.get('dry_run')),
                                 rate=_num(args, 'rate') if rate not in (None, '') else None,
                                 key=_key(args)))


def _t_bank_pay(args, oh):
    return _ok(oh.bank_pay(_num(args, 'amount'), _req(args, 'to_iban'),
                           to_name=str(args.get('to_name') or ''),
                           account=str(args.get('account') or ''),
                           connection=str(args.get('connection') or ''),
                           currency=str(args.get('currency') or ''),
                           reference=str(args.get('reference') or ''), key=_key(args)))


# ── pool / bloctime handlers ──

def _t_pool(args, oh):
    return oh.pool()


def _t_pool_history(args, oh):
    history = oh.pool_history()
    return {'quarters': len(history), 'history': history}


def _t_close_quarter(args, oh):
    return _ok(oh.close_quarter(
        caller=str(args.get('caller') or ''),
        force=bool(args.get('force'))))


def _t_pool_claim(args, oh):
    raw = args.get('quarter')
    quarter = int(_num(args, 'quarter')) if raw not in (None, '') else None
    return _ok(oh.pool_claim(_req(args, 'address'), quarter))


def _t_bloctime(args, oh):
    return oh.bloctime(_req(args, 'address'))


_KEY = {'type': 'string', 'description': 'operator bank key — required for any connection that is not the sandbox'}
_CONN = {'type': 'string', 'description': 'connection id (default: the only one)'}


TOOLS = {
    'openhouse_terms': {
        'description': 'The live deal: rent-to-own model, protocol fee, the '
                       'share of each payment credited as renter equity vs '
                       'owner income, home price, monthly payment and the '
                       '0-5% fee band the contract enforces. Start here.',
        'inputSchema': {'type': 'object', 'properties': {}},
        'handler': _t_terms,
    },
    'openhouse_quote': {
        'description': 'Split a payment the way the contract would, without '
                       "recording it: protocol fee, renter equity, owner "
                       "income. kind='option' credits the whole net amount "
                       'as equity. Equity is clamped so it never runs past '
                       'the home price.',
        'inputSchema': {'type': 'object', 'properties': {
            'amount': {'type': 'number', 'description': 'payment amount in ETH'},
            'kind': {'type': 'string', 'enum': ['rent', 'option'], 'description': "'rent' splits by the model, 'option' is all equity (default rent)"},
        }, 'required': ['amount']},
        'handler': _t_quote,
    },
    'openhouse_models': {
        'description': 'The rent-to-own presets an owner can start from '
                       '(full credit / hybrid / classic / lease), the 0-5% '
                       'protocol fee band, and the published take rates of '
                       'the platforms OpenHouse is measured against.',
        'inputSchema': {'type': 'object', 'properties': {}},
        'handler': _t_models,
    },
    'openhouse_status': {
        'description': 'Everything at a glance: whether a property is '
                       'deployed, the live terms, aggregate rent, cap-table '
                       'size, shares sold vs available, and dividends paid.',
        'inputSchema': {'type': 'object', 'properties': {}},
        'handler': _t_status,
    },
    'openhouse_property': {
        'description': 'The property itself: description, total shares, '
                       'share price, shares still available, active flag and '
                       'contract address. Reports deployed=false honestly '
                       'when nothing has been fractionalized yet.',
        'inputSchema': {'type': 'object', 'properties': {}},
        'handler': _t_property,
    },
    'openhouse_rent_stats': {
        'description': 'Where the rent actually went across every recorded '
                       'payment: gross, protocol fees, renter equity, owner '
                       'income, the percentage that stayed with the property '
                       'and how much of the home is paid off.',
        'inputSchema': {'type': 'object', 'properties': {}},
        'handler': _t_rent_stats,
    },
    'openhouse_rent_ledger': {
        'description': 'Individual rent payments, newest first, each with its '
                       'three-way split. Filter to one renter with `renter`.',
        'inputSchema': {'type': 'object', 'properties': {
            'renter': {'type': 'string', 'description': '0x address to filter by (default: everyone)'},
            'limit': {'type': 'integer', 'description': 'max payments returned (default 100)'},
        }},
        'handler': _t_rent_ledger,
    },
    'openhouse_equity': {
        'description': "A renter's stake: payments made, rent paid, principal "
                       'credited, fees paid, percent of the home owned and '
                       'what is left to own outright.',
        'inputSchema': {'type': 'object', 'properties': {
            'address': {'type': 'string', 'description': '0x renter address'},
        }, 'required': ['address']},
        'handler': _t_equity,
    },
    'openhouse_shareholders': {
        'description': 'The public cap table: every holder with share count, '
                       'contribution, ownership percentage and dividends '
                       'claimed.',
        'inputSchema': {'type': 'object', 'properties': {}},
        'handler': _t_shareholders,
    },
    'openhouse_portfolio': {
        'description': "One address's full position: shares, ownership percent, "
                       'contribution, dividends claimed and current value at '
                       'the live share price; plus rent-equity data (principal '
                       'credited, equity percent, amount remaining, fully_owned) '
                       'under the rent_equity key — one call covers both share '
                       'holders and rent-to-own renters.',
        'inputSchema': {'type': 'object', 'properties': {
            'address': {'type': 'string', 'description': '0x holder address'},
        }, 'required': ['address']},
        'handler': _t_portfolio,
    },
    'openhouse_dividends': {
        'description': 'Distribution history: when rent was redistributed to '
                       'holders, how much, per share, and to how many.',
        'inputSchema': {'type': 'object', 'properties': {}},
        'handler': _t_dividends,
    },
    'openhouse_distribute': {
        'description': 'WRITES. Redistribute income to all shareholders in '
                       'proportion to their shares. `owner` must match the '
                       'recorded owner address once one is set — the call is '
                       'rejected otherwise. Testnet bookkeeping — no funds move.',
        'inputSchema': {'type': 'object', 'properties': {
            'total_amount': {'type': 'number', 'description': 'total amount to distribute (must be > 0)'},
            'owner': {'type': 'string', 'description': '0x owner address — required once an owner is recorded'},
        }, 'required': ['total_amount']},
        'handler': _t_distribute,
    },
    'openhouse_civic': {
        'description': 'The civic seat on this property: whether a government '
                       '(a city housing authority, a state) is chartered, '
                       'whether its pause or foreclosure hold stands, and '
                       'every override it has issued from its own servers. '
                       'Governments verify and override via the server in '
                       'civic/server.py; a city-owned rent-to-own program is '
                       'the same machinery with the city as owner too.',
        'inputSchema': {'type': 'object', 'properties': {}},
        'handler': _t_civic,
    },
    'openhouse_landscape': {
        'description': 'OpenHouse against every other on-chain housing '
                       'project, sorted by who ends up owning the house — '
                       'with live token/property numbers from public '
                       'endpoints, the sourced evidence, and an honest list '
                       'of where the field is ahead of us. Cached; pass '
                       'refresh=true to re-fetch (slower, hits third-party APIs).',
        'inputSchema': {'type': 'object', 'properties': {
            'refresh': {'type': 'boolean', 'description': 'bypass the cache and re-fetch live numbers (default false)'},
        }},
        'handler': _t_landscape,
    },
    'openhouse_fx': {
        'description': 'ETH/fiat exchange rates: ETH priced in a dozen fiat '
                       'currencies (USD, EUR, GBP, JPY, …) from CoinGecko with '
                       'a 15-minute cache. The `source` field marks where the '
                       'answer came from: "coingecko" (live), "cache" (stale '
                       'but fresh enough), or "fallback" (baked-in last-resort '
                       'when the network is unreachable). Call this before '
                       'passing a manual rate= override to '
                       'openhouse_bank_reconcile so you know what the live '
                       'rate is. Pass refresh=true to bypass the cache.',
        'inputSchema': {'type': 'object', 'properties': {
            'refresh': {'type': 'boolean', 'description': 'bypass the cache and re-fetch from CoinGecko (default false)'},
        }},
        'handler': _t_fx,
    },
    'openhouse_source': {
        'description': 'Read the actual implementation. With no arguments '
                       'returns the manifest of readable files (name, '
                       'language, size); with `name` returns that one file in '
                       'full — including the Solidity that holds the shares.',
        'inputSchema': {'type': 'object', 'properties': {
            'name': {'type': 'string', 'description': 'file to read in full, e.g. OpenHouse.sol (default: list the manifest)'},
        }},
        'handler': _t_source,
    },
    'openhouse_pay_rent': {
        'description': 'WRITES. Record a rent payment and split it into '
                       'protocol fee, renter equity and owner income, then '
                       "return the renter's updated equity. kind='option' "
                       'credits the whole net payment as equity. Testnet '
                       'bookkeeping — no funds move.',
        'inputSchema': {'type': 'object', 'properties': {
            'renter': {'type': 'string', 'description': '0x paying address'},
            'amount': {'type': 'number', 'description': 'payment amount in ETH'},
            'kind': {'type': 'string', 'enum': ['rent', 'option'], 'description': "'rent' (default) or 'option'"},
        }, 'required': ['renter', 'amount']},
        'handler': _t_pay_rent,
    },
    'openhouse_purchase': {
        'description': 'WRITES. Buy shares in the deployed property; payment '
                       'is computed from the live share price when omitted. '
                       'Fails if no property is deployed or too few shares '
                       'remain. Testnet bookkeeping — no funds move.',
        'inputSchema': {'type': 'object', 'properties': {
            'buyer': {'type': 'string', 'description': '0x buyer address'},
            'share_count': {'type': 'integer', 'description': 'number of shares to buy'},
            'payment': {'type': 'number', 'description': 'payment amount (default: share_count x share price)'},
        }, 'required': ['buyer', 'share_count']},
        'handler': _t_purchase,
    },
    'openhouse_set_terms': {
        'description': 'WRITES. Set the deal: pick a model preset and/or tune '
                       'the dials. fee_pct is rejected outside the 0-5% band '
                       'and credit_pct outside 0-100. Once an owner address '
                       'is recorded, only that address may change the terms — '
                       'pass it as `owner`.',
        'inputSchema': {'type': 'object', 'properties': {
            'model': {'type': 'string', 'enum': ['full_credit', 'hybrid', 'classic', 'lease'], 'description': 'preset to start from'},
            'fee_pct': {'type': 'number', 'description': 'protocol take, 0-5 — 0 means take nothing'},
            'credit_pct': {'type': 'number', 'description': 'share of the post-fee payment credited as equity, 0-100'},
            'option_fee_pct': {'type': 'number', 'description': 'upfront option fee, % of home price'},
            'home_price': {'type': 'number', 'description': 'price to own outright, ETH'},
            'monthly_rent': {'type': 'number', 'description': 'scheduled monthly payment, ETH'},
            'owner': {'type': 'string', 'description': '0x address making the change — required once an owner is recorded'},
            'treasury': {'type': 'string', 'description': '0x protocol fee sink'},
        }},
        'handler': _t_set_terms,
    },
    'openhouse_claim_owner': {
        'description': 'WRITES. Claim the owner seat while it is still empty '
                       '(first writer wins). After this, only that address '
                       'can change the terms.',
        'inputSchema': {'type': 'object', 'properties': {
            'address': {'type': 'string', 'description': '0x address to record as owner'},
        }, 'required': ['address']},
        'handler': _t_claim_owner,
    },
    'openhouse_toggle_active': {
        'description': 'WRITES. Toggle the property active/inactive. When '
                       'inactive, pay_rent and purchase are blocked. '
                       'Owner-only once an owner is recorded.',
        'inputSchema': {'type': 'object', 'properties': {
            'owner': {'type': 'string', 'description': '0x owner address — required once an owner is recorded'},
        }},
        'handler': _t_toggle_active,
    },
    'openhouse_transfer_authority': {
        'description': 'WRITES. Transfer the owner seat to a new address. '
                       'The current owner must pass their address as `caller`. '
                       'After this only the new address can change the terms.',
        'inputSchema': {'type': 'object', 'properties': {
            'new_authority': {'type': 'string', 'description': '0x address to receive the owner seat'},
            'caller': {'type': 'string', 'description': '0x current owner address'},
        }, 'required': ['new_authority']},
        'handler': _t_transfer_authority,
    },
    'openhouse_civic_charter': {
        'description': 'WRITES. The owner charters a government (city housing '
                       'authority, state) into the empty civic seat. Once '
                       'seated only the authority can leave; the owner cannot '
                       'remove it or clear its pause.',
        'inputSchema': {'type': 'object', 'properties': {
            'key': {'type': 'string', 'description': "the government server's key (civic/server.py GET /city)"},
            'name': {'type': 'string', 'description': 'e.g. "Cleveland Housing Authority"'},
            'region': {'type': 'string', 'description': 'ISO 3166-2, e.g. US-OH'},
            'uri': {'type': 'string', 'description': 'the .gov URL that publishes the key'},
            'owner': {'type': 'string', 'description': '0x owner address — required once an owner is recorded'},
        }, 'required': ['key']},
        'handler': _t_civic_charter,
    },
    'openhouse_civic_override': {
        'description': 'WRITES. The chartered authority acts: pause/unpause '
                       'freezes or thaws payments (bank transfers are held, '
                       'not lost), hold/release blocks or permits a taking.',
        'inputSchema': {'type': 'object', 'properties': {
            'action': {'type': 'string', 'enum': ['pause', 'unpause', 'hold', 'release']},
            'key': {'type': 'string', 'description': "the chartered authority's key"},
            'reason': {'type': 'string', 'description': 'on the record, next to the action'},
        }, 'required': ['action', 'key']},
        'handler': _t_civic_override,
    },
    'openhouse_civic_resign': {
        'description': 'WRITES. The chartered authority vacates its seat — '
                       'the only way to empty it. Any civic pause or hold it '
                       'placed lifts immediately. Only the authority itself '
                       'can call this (verified by key).',
        'inputSchema': {'type': 'object', 'properties': {
            'key': {'type': 'string', 'description': "the chartered authority's key"},
        }, 'required': ['key']},
        'handler': _t_civic_resign,
    },
    'openhouse_examples': {
        'description': 'Guided testnet walkthroughs: a first rent payment, a '
                       'zero-fee owner, a lease-option, paying a home off, '
                       'rent paid by bank transfer, importing a real bank '
                       'statement, a city freezing payments. Lists name, '
                       'title and what each one shows. Run one with '
                       'openhouse_run_example.',
        'inputSchema': {'type': 'object', 'properties': {}},
        'handler': _t_examples,
    },
    'openhouse_run_example': {
        'description': 'Run one walkthrough end to end in a THROWAWAY store '
                       '(the live testnet node is untouched) and return the '
                       'transcript: every step is a real openhouse_* tool call '
                       'with its arguments and its actual result, so you can '
                       'replay any step against this server.',
        'inputSchema': {'type': 'object', 'properties': {
            'name': {'type': 'string', 'description': 'example name from openhouse_examples'},
        }, 'required': ['name']},
        'handler': _t_run_example,
    },
    'openhouse_bank_kinds': {
        'description': 'Every bank this node can connect to and the fields '
                       'each needs: sandbox (local testnet bank), statement '
                       '(camt.053 / MT940 / OFX / CSV in, ISO 20022 pain.001 '
                       'out — works with any bank), openbanking (Berlin Group '
                       'PSD2 or UK OBIE API) and mcp (a bank that runs its own '
                       'MCP server).',
        'inputSchema': {'type': 'object', 'properties': {}},
        'handler': _t_bank_kinds,
    },
    'openhouse_bank_status': {
        'description': 'Bank connections (no credentials), how many renters '
                       'are linked and how many transfers have been booked.',
        'inputSchema': {'type': 'object', 'properties': {}},
        'handler': _t_bank_status,
    },
    'openhouse_bank_connect': {
        'description': 'WRITES. Connect a bank. kind=sandbox needs nothing '
                       '(config.currency / holder / opening_balance optional). '
                       'Real kinds need key and their config fields — see '
                       'openhouse_bank_kinds. Credentials are stored 0600 off '
                       'the repo and only ever read back redacted.',
        'inputSchema': {'type': 'object', 'properties': {
            'kind': {'type': 'string', 'enum': ['sandbox', 'statement', 'openbanking', 'mcp']},
            'name': {'type': 'string', 'description': 'a label, becomes the connection id'},
            'config': {'type': 'object', 'description': 'the kind\'s fields, e.g. {"base_url":…,"access_token":…}'},
            'key': _KEY,
        }, 'required': ['kind']},
        'handler': _t_bank_connect,
    },
    'openhouse_bank_disconnect': {
        'description': 'WRITES. Forget a bank connection and its credentials.',
        'inputSchema': {'type': 'object', 'properties': {
            'connection': {'type': 'string'}, 'key': _KEY,
        }, 'required': ['connection']},
        'handler': _t_bank_disconnect,
    },
    'openhouse_bank_accounts': {
        'description': 'Accounts on a bank connection with currency, IBAN and balance.',
        'inputSchema': {'type': 'object', 'properties': {'connection': _CONN, 'key': _KEY}},
        'handler': _t_bank_accounts,
    },
    'openhouse_bank_transactions': {
        'description': 'Transactions on a bank connection, newest first, in '
                       'one shape whatever the bank: amount is signed (+ is '
                       'money in), plus counterparty, IBAN and reference.',
        'inputSchema': {'type': 'object', 'properties': {
            'connection': _CONN,
            'account': {'type': 'string', 'description': 'account id (default: all)'},
            'since': {'type': 'integer', 'description': 'unix seconds'},
            'limit': {'type': 'integer', 'description': 'max rows (default 100)'},
            'key': _KEY,
        }},
        'handler': _t_bank_transactions,
    },
    'openhouse_bank_import': {
        'description': 'WRITES. Import a downloaded bank statement into a '
                       'statement connection — camt.053 XML, MT940, OFX/QFX '
                       'or CSV, auto-detected. Overlapping re-imports are '
                       'de-duplicated.',
        'inputSchema': {'type': 'object', 'properties': {
            'content': {'type': 'string', 'description': 'the statement file\'s text'},
            'format': {'type': 'string', 'enum': ['auto', 'camt053', 'mt940', 'ofx', 'csv']},
            'connection': _CONN, 'key': _KEY,
        }, 'required': ['content']},
        'handler': _t_bank_import,
    },
    'openhouse_bank_receive': {
        'description': 'WRITES. Sandbox only: simulate a renter paying by bank '
                       'transfer: books an incoming transfer on the sandbox '
                       'bank. Put their code from openhouse_bank_reference in '
                       '`reference`, then run openhouse_bank_reconcile.',
        'inputSchema': {'type': 'object', 'properties': {
            'amount': {'type': 'number', 'description': 'amount in the account currency'},
            'reference': {'type': 'string', 'description': 'transfer memo, e.g. "Rent OH-9B3CD4"'},
            'from_name': {'type': 'string'}, 'from_iban': {'type': 'string'},
            'account': {'type': 'string'}, 'connection': _CONN,
            'date': {'type': 'integer', 'description': 'unix seconds (default now)'},
        }, 'required': ['amount']},
        'handler': _t_bank_receive,
    },
    'openhouse_bank_reference': {
        'description': 'The reference code a renter writes in their bank '
                       'transfer memo (OH-XXXXXX) so the payment is credited '
                       'to them — works on any rail: SEPA, ACH, Faster '
                       'Payments, Zelle, wire.',
        'inputSchema': {'type': 'object', 'properties': {
            'address': {'type': 'string', 'description': '0x renter address'},
        }, 'required': ['address']},
        'handler': _t_bank_reference,
    },
    'openhouse_bank_link': {
        'description': 'WRITES. Link a renter to their bank payments: their '
                       'reference code always matches; payer_iban / payer_name '
                       'catch transfers sent without the code.',
        'inputSchema': {'type': 'object', 'properties': {
            'address': {'type': 'string', 'description': '0x renter address'},
            'payer_iban': {'type': 'string'}, 'payer_name': {'type': 'string'},
            'kind': {'type': 'string', 'enum': ['rent', 'option'], 'description': 'how their transfers are booked (default rent)'},
            'key': _KEY,
        }, 'required': ['address']},
        'handler': _t_bank_link,
    },
    'openhouse_bank_unlink': {
        'description': 'WRITES. Remove a renter\'s bank link — their reference '
                       'code and any IBAN/name match. Real banks need key.',
        'inputSchema': {'type': 'object', 'properties': {
            'address': {'type': 'string', 'description': '0x renter address to unlink'},
            'key': _KEY,
        }, 'required': ['address']},
        'handler': _t_bank_unlink,
    },
    'openhouse_bank_links': {
        'description': 'Linked renters with their reference codes. Payer IBAN '
                       'and name are masked without the key when a real bank '
                       'is connected.',
        'inputSchema': {'type': 'object', 'properties': {'key': _KEY}},
        'handler': _t_bank_links,
    },
    'openhouse_bank_reconcile': {
        'description': 'WRITES. Book every linked incoming bank transfer onto '
                       'the rent ledger exactly once, converted to Ξ (rate = '
                       'fiat per Ξ, default the live fx rail) and stamped with '
                       'the transfer it came from. Returns booked, held '
                       '(ledger refused, e.g. civic pause — retried next run) '
                       'and unmatched credits. dry_run=true previews.',
        'inputSchema': {'type': 'object', 'properties': {
            'connection': _CONN,
            'account': {'type': 'string'},
            'since': {'type': 'integer', 'description': 'unix seconds'},
            'dry_run': {'type': 'boolean'},
            'rate': {'type': 'number', 'description': 'fiat per 1 Ξ (default: live fx)'},
            'key': _KEY,
        }},
        'handler': _t_bank_reconcile,
    },
    'openhouse_bank_pay': {
        'description': 'WRITES. Send money out of a bank connection. sandbox '
                       'debits its fake account; statement returns an ISO '
                       '20022 pain.001 file to upload at the bank; '
                       'openbanking initiates a SEPA transfer the account '
                       'holder approves at the bank; mcp calls the bank\'s '
                       'payment tool. Real banks need key.',
        'inputSchema': {'type': 'object', 'properties': {
            'amount': {'type': 'number'},
            'to_iban': {'type': 'string'}, 'to_name': {'type': 'string'},
            'account': {'type': 'string', 'description': 'paying account id / IBAN'},
            'currency': {'type': 'string'}, 'reference': {'type': 'string'},
            'connection': _CONN, 'key': _KEY,
        }, 'required': ['amount', 'to_iban']},
        'handler': _t_bank_pay,
    },
    'openhouse_pool': {
        'description': 'The quarter now accruing: pool balance, bloctime '
                       'earned per address, projected shares at close, and '
                       'when the quarter window ends. Start here before '
                       'calling openhouse_close_quarter.',
        'inputSchema': {'type': 'object', 'properties': {}},
        'handler': _t_pool,
    },
    'openhouse_pool_history': {
        'description': 'Every closed quarter, newest first: pool amount, '
                       'total weight, per-address allocations and whether '
                       'each was claimed.',
        'inputSchema': {'type': 'object', 'properties': {}},
        'handler': _t_pool_history,
    },
    'openhouse_close_quarter': {
        'description': 'WRITES. Freeze the current quarter and lock in the '
                       'bloctime split. Permissionless once the 90-day window '
                       'has elapsed — anyone can call it. force=true cuts a '
                       'quarter short (owner only, pass caller). Testnet '
                       'bookkeeping — no funds move.',
        'inputSchema': {'type': 'object', 'properties': {
            'caller': {'type': 'string', 'description': '0x address closing the quarter (required to force)'},
            'force': {'type': 'boolean', 'description': 'cut the quarter short before 90 days — owner only (default false)'},
        }},
        'handler': _t_close_quarter,
    },
    'openhouse_pool_claim': {
        'description': "WRITES. Claim an address's share of one closed "
                       'quarter or all unclaimed quarters. Pull model — '
                       'nothing is distributed until this is called. Testnet '
                       'bookkeeping — no funds move.',
        'inputSchema': {'type': 'object', 'properties': {
            'address': {'type': 'string', 'description': '0x address claiming their share'},
            'quarter': {'type': 'integer', 'description': 'quarter index to claim (default: all unclaimed)'},
        }, 'required': ['address']},
        'handler': _t_pool_claim,
    },
    'openhouse_bloctime': {
        'description': "One address's locked liquidity and earned bloctime: "
                       'current-quarter weight, projected payout at next '
                       'close, lifetime weight, and a record of every '
                       'quarter earned.',
        'inputSchema': {'type': 'object', 'properties': {
            'address': {'type': 'string', 'description': '0x address to look up'},
        }, 'required': ['address']},
        'handler': _t_bloctime,
    },
}


def _rpc_result(id_, result) -> JSONResponse:
    return JSONResponse({'jsonrpc': '2.0', 'id': id_, 'result': result})


def _rpc_error(id_, code: int, message: str, status: int = 200) -> JSONResponse:
    return JSONResponse({'jsonrpc': '2.0', 'id': id_,
                         'error': {'code': code, 'message': message}},
                        status_code=status)


def _tool_error(id_, message: str) -> JSONResponse:
    # Tool failures (bad args, a rejected fee, a home already paid off) are
    # *successful* JSON-RPC responses carrying isError — per MCP spec — so
    # the client model can read the message and correct course.
    return _rpc_result(id_, {'content': [{'type': 'text', 'text': message}],
                             'isError': True})


async def _call_tool(id_, params: dict, get_mod: Callable) -> JSONResponse:
    name = str(params.get('name') or '')
    tool = TOOLS.get(name)
    if not tool:
        return _rpc_error(id_, -32602, f'unknown tool: {name}')
    args = params.get('arguments') or {}
    if not isinstance(args, dict):
        return _rpc_error(id_, -32602, 'arguments must be an object')
    try:
        result = tool['handler'](args, get_mod())
        if inspect.isawaitable(result):
            result = await result
    except ToolError as e:
        return _tool_error(id_, f'{name}: {e}')
    except Exception as e:
        return _tool_error(id_, f'{name} failed: {type(e).__name__}: {e}')
    out = {'content': [{'type': 'text',
                        'text': json.dumps(result, indent=2, default=str)}],
           'isError': False}
    if isinstance(result, dict):
        out['structuredContent'] = result
    return _rpc_result(id_, out)


def build_router(get_mod: Callable, version: str = '2.1.0') -> APIRouter:
    """Mount the MCP endpoint over a Mod accessor.

    Args:
        get_mod: zero-arg callable returning the openhouse Mod instance —
                 called per tool call so the lazy singleton stays lazy.
        version: reported to clients as serverInfo.version.
    """
    router = APIRouter()

    @router.get('/mcp')
    def mcp_get():
        """Streamable HTTP without SSE: nothing to GET — clients must POST."""
        return Response(status_code=405, media_type='text/plain',
                        content='POST JSON-RPC 2.0 messages to this endpoint')

    @router.post('/mcp')
    async def mcp_post(request: Request):
        """MCP Streamable HTTP endpoint: one JSON-RPC message per POST."""
        try:
            body = json.loads((await request.body()) or b'')
        except Exception:
            return _rpc_error(None, -32700,
                              'parse error: body is not valid JSON', status=400)
        if not isinstance(body, dict) or not isinstance(body.get('method'), str):
            id_ = body.get('id') if isinstance(body, dict) else None
            return _rpc_error(id_, -32600,
                              'invalid request: expected a JSON-RPC 2.0 object '
                              'with a method', status=400)
        method, id_ = body['method'], body.get('id')
        params = body.get('params') or {}
        # Notifications (no id, or notifications/*) get an empty 202 per spec.
        if id_ is None or method.startswith('notifications/'):
            return Response(status_code=202)
        if method == 'initialize':
            client_ver = str(params.get('protocolVersion') or '')
            return _rpc_result(id_, {
                'protocolVersion': client_ver if client_ver in SUPPORTED_PROTOCOL_VERSIONS
                else DEFAULT_PROTOCOL_VERSION,
                'capabilities': {'tools': {}},
                'serverInfo': {'name': 'openhouse', 'version': version},
                'instructions': INSTRUCTIONS,
            })
        if method == 'ping':
            return _rpc_result(id_, {})
        if method == 'tools/list':
            return _rpc_result(id_, {'tools': [
                {'name': n, 'description': t['description'],
                 'inputSchema': t['inputSchema']} for n, t in TOOLS.items()]})
        if method == 'tools/call':
            return await _call_tool(id_, params, get_mod)
        return _rpc_error(id_, -32601, f'method not found: {method}')

    return router
