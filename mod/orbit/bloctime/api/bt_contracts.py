"""
Deploy-anything store — compile Solidity and remember what you deployed.

BlocTime ships two contracts of its own, but the console is a perfectly good
place to put *any* contract on chain: paste Solidity, compile it with the
module's solc, deploy it from your wallet (or the server signer), and it
joins the CONTRACTS playground for reads and writes.

Compilation shells out to scripts/compile.js so imports resolve exactly the
way hardhat resolves them here (node_modules + contracts/).

Each deployment is one entry:
    { id, name, address, chainId, rpc, deployer, txHash, abi, source,
      filename, solc, explorer, createdAt }

Entries live off-tree in ~/.mod/bloctime/contracts.json — deployments are
per-box state, not something to commit.
"""

import json
import os
import re
import subprocess
import time
from pathlib import Path

from bt_registry import explorer_for, slugify

MODULE_DIR = Path(__file__).parent.parent
COMPILER = MODULE_DIR / 'scripts' / 'compile.js'
STORE_DIR = Path(os.path.expanduser('~/.mod/bloctime'))
STORE_PATH = STORE_DIR / 'contracts.json'


# ── Compile ──────────────────────────────────────────────────────────────

def compile_source(source, filename='Contract.sol', optimize=True, runs=200):
    """Compile Solidity → [{name, abi, bytecode, constructor}]. Raises ValueError."""
    if not str(source or '').strip():
        raise ValueError("No Solidity source given")
    if not re.match(r'^[\w.\-]+\.sol$', filename or ''):
        raise ValueError("filename must be a plain *.sol name")

    req = json.dumps({
        'source': source, 'filename': filename,
        'optimize': bool(optimize), 'runs': int(runs),
    })
    try:
        proc = subprocess.run(
            ['node', str(COMPILER)], input=req,
            capture_output=True, text=True, timeout=120,
        )
    except FileNotFoundError:
        raise ValueError("node is not installed — cannot compile")
    except subprocess.TimeoutExpired:
        raise ValueError("Compile timed out after 120s")
    if proc.returncode != 0:
        raise ValueError(f"Compiler crashed: {proc.stderr.strip()[-500:]}")

    payload = json.loads(proc.stdout)
    out = payload['output']
    messages = out.get('errors', [])
    errors = [m for m in messages if m.get('severity') == 'error']
    if errors:
        raise ValueError('\n'.join(m.get('formattedMessage', m.get('message', '')) for m in errors))

    contracts = []
    # Only contracts written in the user's file — not the imported library tree.
    for name, c in (out.get('contracts', {}).get(filename, {}) or {}).items():
        abi = c.get('abi', [])
        ctor = next((e for e in abi if e.get('type') == 'constructor'), None)
        contracts.append({
            'name': name,
            'abi': abi,
            'bytecode': '0x' + c['evm']['bytecode']['object'].removeprefix('0x'),
            'constructor': ctor.get('inputs', []) if ctor else [],
            'deployable': bool(c['evm']['bytecode']['object']),  # false for interfaces/abstract
        })
    if not contracts:
        raise ValueError(f"No contract defined in {filename}")
    return {
        'solc': payload['version'],
        'filename': filename,
        'contracts': contracts,
        'warnings': [m.get('formattedMessage', '') for m in messages if m.get('severity') != 'error'],
    }


# ── Store ────────────────────────────────────────────────────────────────

def _load():
    if STORE_PATH.exists():
        with open(STORE_PATH) as f:
            return json.load(f)
    return []


def _save(entries):
    STORE_DIR.mkdir(parents=True, exist_ok=True)
    with open(STORE_PATH, 'w') as f:
        json.dump(entries, f, indent=2)


def list_deployments():
    return _load()


def get_deployment(deployment_id):
    for e in _load():
        if e['id'] == deployment_id:
            return e
    return None


def add_deployment(name, address, abi, rpc, chain_id='', deployer='', tx_hash='',
                   source='', filename='', solc='', verify=True):
    """Record a deployed contract. Verified by checking there is code at address."""
    from web3 import Web3

    if not name or not address or abi is None:
        raise ValueError("name, address and abi are required")
    address = Web3.to_checksum_address(address)

    if verify:
        if not rpc:
            raise ValueError("rpc is required to verify the deployment")
        w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={'timeout': 10}))
        if not w3.is_connected():
            raise ValueError(f"RPC unreachable: {rpc}")
        if w3.eth.get_code(address) in (b'', b'\x00'):
            raise ValueError(f"No contract code at {address} — is it on this RPC's chain?")
        chain_id = str(w3.eth.chain_id)

    entries = _load()
    for e in entries:
        if e['address'].lower() == address.lower() and str(e['chainId']) == str(chain_id):
            raise ValueError(f"Already recorded as '{e['id']}'")

    base = slugify(name)
    taken = {e['id'] for e in entries}
    slug, n = base, 2
    while slug in taken:
        slug, n = f"{base}-{n}", n + 1

    entry = {
        'id': slug,
        'name': str(name),
        'address': address,
        'chainId': str(chain_id or ''),
        'rpc': rpc or '',
        'deployer': deployer or '',
        'txHash': tx_hash or '',
        'abi': abi if isinstance(abi, list) else json.loads(abi),
        'source': source or '',
        'filename': filename or '',
        'solc': solc or '',
        'explorer': explorer_for(chain_id, address),
        'createdAt': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
    }
    entries.append(entry)
    _save(entries)
    return entry


def remove_deployment(deployment_id):
    entries = _load()
    kept = [e for e in entries if e['id'] != deployment_id]
    if len(kept) == len(entries):
        raise ValueError(f"Unknown deployment '{deployment_id}'")
    _save(kept)
    return {'removed': deployment_id}


# ── Server-side deploy (CLI path — the app deploys from the wallet) ──────

def deploy(abi, bytecode, args=None, rpc=None, private_key=None, gas=None):
    """Deploy compiled bytecode with the server signer. Returns deploy facts."""
    from web3 import Web3

    rpc = rpc or os.environ.get('BASE_TESTNET_RPC_URL', 'https://sepolia.base.org')
    private_key = private_key or os.environ.get('PRIVATE_KEY')
    if not private_key:
        raise ValueError("PRIVATE_KEY env var required to deploy from the server")

    w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={'timeout': 30}))
    if not w3.is_connected():
        raise ValueError(f"RPC unreachable: {rpc}")
    account = w3.eth.account.from_key(private_key)
    factory = w3.eth.contract(abi=abi, bytecode=bytecode)

    ctor = factory.constructor(*(args or []))
    tx = ctor.build_transaction({
        'from': account.address,
        'nonce': w3.eth.get_transaction_count(account.address),
    })
    tx['gas'] = int(gas) if gas else int(w3.eth.estimate_gas(tx) * 1.2)
    signed = account.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=180)
    if receipt.status != 1:
        raise ValueError(f"Deploy transaction reverted: 0x{tx_hash.hex().removeprefix('0x')}")
    return {
        'address': receipt.contractAddress,
        'txHash': '0x' + tx_hash.hex().removeprefix('0x'),
        'deployer': account.address,
        'chainId': str(w3.eth.chain_id),
        'rpc': rpc,
        'gasUsed': receipt.gasUsed,
        'block': receipt.blockNumber,
    }


def coerce_args(inputs, args):
    """String/JSON constructor args → what web3.py expects, by ABI type."""
    from web3 import Web3

    def one(value, abi_type):
        if abi_type.endswith(']'):
            base = abi_type[: abi_type.rfind('[')]
            if isinstance(value, str):
                value = json.loads(value)
            return [one(v, base) for v in value]
        if abi_type == 'address':
            return Web3.to_checksum_address(value)
        if abi_type.startswith(('uint', 'int')):
            return int(str(value).strip(), 0)
        if abi_type == 'bool':
            return value if isinstance(value, bool) else str(value).strip().lower() in ('1', 'true', 'yes')
        if abi_type.startswith('bytes'):
            s = str(value)
            return Web3.to_bytes(hexstr=s) if s.startswith('0x') else Web3.to_bytes(text=s)
        return str(value) if abi_type == 'string' else value

    if len(args or []) != len(inputs):
        raise ValueError(f"Constructor expects {len(inputs)} arg(s), got {len(args or [])}")
    return [one(a, i['type']) for a, i in zip(args or [], inputs)]
