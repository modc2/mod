"""
Solidity in, an ABI and a runtime object out.

This talks to `solc` directly over its **standard JSON** interface rather than
wrapping a framework. Foundry and Hardhat are excellent and neither is
required: a module that can be reached from a browser, a CLI and an MCP tool
should not need a project scaffold on disk to answer "what does this contract
compile to".

Which compiler runs is decided in this order, and reported in every result so
a bytecode hash is reproducible later:

    1. an explicit `version` argument
    2. the `pragma solidity` line in the source
    3. the newest binary already on this box

Binaries are found wherever this box already keeps them — foundry's svm store,
hardhat's cache, `which solc` — before anything is downloaded, because a box
that has compiled Solidity before should not have to do it again. When a
version really is missing it is fetched from binaries.soliditylang.org into
~/.mod/eth/solc and checksummed against the published keccak list; set
ETH_SOLC_DOWNLOAD=0 on a box that should never reach out.

Imports resolve against the sources you pass in the same request, so a
multi-file project works as long as it is self-contained. There is no npm
resolution here on purpose — a compiler that silently pulls a dependency off
the network is a compiler that can be handed a different dependency tomorrow.
"""
import json
import os
import platform
import re
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

STATE = Path(os.path.expanduser(os.environ.get('ETH_DIR', '~/.mod/eth')))
SOLC_DIR = STATE / 'solc'
TIMEOUT = int(os.environ.get('ETH_SOLC_TIMEOUT', 180))
ALLOW_DOWNLOAD = os.environ.get('ETH_SOLC_DOWNLOAD', '1') not in ('0', 'false', 'False')
RELEASES = 'https://binaries.soliditylang.org'

SEARCH = [
    SOLC_DIR,
    Path(os.path.expanduser('~/.local/share/svm')),          # foundry
    Path(os.path.expanduser('~/.svm')),
    Path(os.path.expanduser('~/.cache/hardhat-nodejs/compilers-v2')),
    Path(os.path.expanduser('~/.solcx')),                    # py-solc-x
]


class CompileError(Exception):
    """solc said no, or could not be found."""

    def __init__(self, message: str, errors: Optional[List[dict]] = None):
        super().__init__(message)
        self.errors = errors or []


# ── finding a compiler ───────────────────────────────────────────────

def _platform_dir() -> str:
    system = platform.system().lower()
    machine = platform.machine().lower()
    if system == 'darwin':
        return 'macosx-amd64'
    if system == 'windows':
        return 'windows-amd64'
    return 'linux-aarch64' if machine in ('aarch64', 'arm64') else 'linux-amd64'


VERSION_RE = re.compile(r'(\d+)\.(\d+)\.(\d+)')


def _version_of(path: Path) -> Optional[str]:
    """The version a binary reports. The filename is a hint, not the truth."""
    match = VERSION_RE.search(path.name)
    if match:
        return '.'.join(match.groups())
    try:
        out = subprocess.run([str(path), '--version'], capture_output=True,
                             text=True, timeout=20).stdout
        found = VERSION_RE.search(out.split('Version:')[-1])
        return '.'.join(found.groups()) if found else None
    except Exception:
        return None


def installed() -> Dict[str, str]:
    """version → binary path, for every solc this box already has."""
    found: Dict[str, str] = {}
    for root in SEARCH:
        if not root.is_dir():
            continue
        for path in root.rglob('solc*'):
            if not path.is_file() or not os.access(path, os.X_OK):
                continue
            if path.suffix in ('.json', '.txt', '.lock'):
                continue
            version = _version_of(path)
            if version and version not in found:
                found[version] = str(path)
    which = _which_solc()
    if which:
        version = _version_of(Path(which))
        if version:
            found.setdefault(version, which)
    return found


def _which_solc() -> Optional[str]:
    for directory in (os.environ.get('PATH') or '').split(os.pathsep):
        candidate = Path(directory or '.') / 'solc'
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def _tuple(version: str) -> Tuple[int, int, int]:
    match = VERSION_RE.search(version or '')
    return tuple(int(g) for g in match.groups()) if match else (0, 0, 0)


def newest(versions) -> Optional[str]:
    versions = list(versions)
    return max(versions, key=_tuple) if versions else None


# ── which version the source is asking for ───────────────────────────

PRAGMA_RE = re.compile(r'pragma\s+solidity\s+([^;]+);')


def pragma(source: str) -> Optional[str]:
    match = PRAGMA_RE.search(source or '')
    return match.group(1).strip() if match else None


def satisfies(version: str, constraint: Optional[str]) -> bool:
    """Enough of npm-style ranges to read a real pragma line.

    ^0.8.20 · ~0.8.20 · >=0.8.0 <0.9.0 · 0.8.24 · >0.7 — and an unknown
    operator is treated as "no opinion" rather than silently excluding
    everything, because a pragma this cannot parse should not stop a build.
    """
    if not constraint:
        return True
    got = _tuple(version)
    for clause in re.split(r'\s+|\|\|', constraint.strip()):
        clause = clause.strip()
        if not clause:
            continue
        match = re.match(r'^([\^~><=]*)\s*v?(\d+(?:\.\d+){0,2})$', clause)
        if not match:
            continue
        op, raw = match.groups()
        parts = [int(p) for p in raw.split('.')]
        want = tuple(parts + [0] * (3 - len(parts)))
        if op == '^':
            # 0.x is special in semver: ^0.8.20 allows 0.8.z, not 0.9.0
            if want[0] == 0:
                if not (got[:2] == want[:2] and got >= want):
                    return False
            elif not (got[0] == want[0] and got >= want):
                return False
        elif op == '~':
            if not (got[:2] == want[:2] and got >= want):
                return False
        elif op in ('>=', ''):
            if got < want:
                return False
        elif op == '>':
            if got <= want:
                return False
        elif op == '<=':
            if got > want:
                return False
        elif op == '<':
            if len(parts) < 3:            # `<0.9` means anything below 0.9.0
                want = (want[0], want[1], 0)
            if got >= want:
                return False
        elif op == '=':
            if got != want:
                return False
    return True


# ── getting one ──────────────────────────────────────────────────────

def _release_list() -> List[dict]:
    url = f'{RELEASES}/{_platform_dir()}/list.json'
    with urllib.request.urlopen(url, timeout=60) as response:
        return (json.loads(response.read()) or {}).get('builds', [])


def download(version: str) -> str:
    if not ALLOW_DOWNLOAD:
        raise CompileError(f'solc {version} is not installed and ETH_SOLC_DOWNLOAD '
                           f'is off — installed: {", ".join(sorted(installed())) or "none"}')
    builds = _release_list()
    build = next((b for b in builds if b.get('version') == version), None)
    if build is None:
        # `0.8` is a reasonable thing to ask for; take the newest matching.
        matches = [b for b in builds if satisfies(b.get('version', ''), version)
                   and 'nightly' not in b.get('longVersion', '')]
        build = max(matches, key=lambda b: _tuple(b['version'])) if matches else None
    if build is None:
        raise CompileError(f'no published solc matches {version!r}')
    SOLC_DIR.mkdir(parents=True, exist_ok=True)
    dest = SOLC_DIR / f"solc-{build['version']}"
    url = f"{RELEASES}/{_platform_dir()}/{build['path']}"
    with urllib.request.urlopen(url, timeout=TIMEOUT) as response:
        blob = response.read()
    expected = (build.get('keccak256') or '').lower().removeprefix('0x')
    if expected:
        try:
            from eth_utils import keccak
            got = keccak(blob).hex().removeprefix('0x')
            if got != expected:
                raise CompileError(f'solc {build["version"]} download failed its '
                                   f'checksum — refusing to run it')
        except ImportError:
            pass
    dest.write_bytes(blob)
    os.chmod(dest, 0o755)
    return str(dest)


def resolve(version: Optional[str] = None,
            source: Optional[str] = None) -> Tuple[str, str]:
    """(version, binary path) for this request."""
    have = installed()
    constraint = version or pragma(source or '')
    if version and VERSION_RE.fullmatch(version.strip()):
        exact = version.strip()
        if exact in have:
            return exact, have[exact]
        return exact, download(exact)
    fits = [v for v in have if satisfies(v, constraint)]
    if fits:
        best = newest(fits)
        return best, have[best]
    if constraint:
        path = download(constraint)
        return _version_of(Path(path)) or constraint, path
    if have:
        best = newest(have)
        return best, have[best]
    path = download('0.8.24')
    return _version_of(Path(path)) or '0.8.24', path


# ── compiling ────────────────────────────────────────────────────────

def compile_sources(sources: Dict[str, str], version: Optional[str] = None,
                    optimize: bool = True, runs: int = 200,
                    evm_version: Optional[str] = None,
                    libraries: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """{filename: solidity} → every contract in them, ready to deploy."""
    if not sources:
        raise CompileError('nothing to compile')
    sources = {name: text for name, text in sources.items() if (text or '').strip()}
    if not sources:
        raise CompileError('the source is empty')
    first = next(iter(sources.values()))
    solc_version, binary = resolve(version, first)

    settings: Dict[str, Any] = {
        'optimizer': {'enabled': bool(optimize), 'runs': int(runs)},
        'outputSelection': {'*': {'*': ['abi', 'evm.bytecode.object',
                                        'evm.bytecode.linkReferences',
                                        'evm.deployedBytecode.object',
                                        'evm.methodIdentifiers', 'metadata',
                                        'storageLayout'],
                                  '': ['ast']}},
    }
    if evm_version:
        settings['evmVersion'] = evm_version
    if libraries:
        # solc wants {file: {LibName: address}}; accept the flat form too.
        grouped: Dict[str, Dict[str, str]] = {}
        for key, address in libraries.items():
            file, _, name = key.rpartition(':')
            grouped.setdefault(file or next(iter(sources)), {})[name] = address
        settings['libraries'] = grouped

    payload = {'language': 'Solidity',
               'sources': {name: {'content': text} for name, text in sources.items()},
               'settings': settings}

    try:
        completed = subprocess.run([binary, '--standard-json'],
                                   input=json.dumps(payload), capture_output=True,
                                   text=True, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        raise CompileError(f'solc {solc_version} took longer than {TIMEOUT}s')
    except OSError as e:
        raise CompileError(f'could not run solc at {binary}: {e}')
    if completed.returncode != 0 and not completed.stdout.strip():
        raise CompileError(completed.stderr.strip()[:2000] or 'solc failed silently')

    try:
        output = json.loads(completed.stdout)
    except json.JSONDecodeError:
        raise CompileError(f'solc produced no JSON: {completed.stdout[:500]}')

    diagnostics = output.get('errors', []) or []
    fatal = [d for d in diagnostics if d.get('severity') == 'error']
    if fatal:
        raise CompileError('; '.join(d.get('formattedMessage', d.get('message', ''))
                                     for d in fatal)[:4000], errors=fatal)

    contracts: List[Dict[str, Any]] = []
    for file, entries in (output.get('contracts') or {}).items():
        for name, blob in entries.items():
            evm = blob.get('evm') or {}
            bytecode = (evm.get('bytecode') or {}).get('object') or ''
            deployed = (evm.get('deployedBytecode') or {}).get('object') or ''
            abi = blob.get('abi') or []
            contracts.append({
                'file': file,
                'name': name,
                'abi': abi,
                'bytecode': '0x' + bytecode if bytecode and not bytecode.startswith('0x') else bytecode,
                'deployed_bytecode': ('0x' + deployed if deployed and not deployed.startswith('0x')
                                      else deployed),
                'link_references': (evm.get('bytecode') or {}).get('linkReferences') or {},
                'methods': evm.get('methodIdentifiers') or {},
                'constructor': _constructor(abi),
                'deployable': bool(bytecode),
                'size': len(bytecode) // 2,
            })
    if not contracts:
        raise CompileError('the source compiled but declares no contract')

    return {
        'compiler': {'version': solc_version, 'binary': binary,
                     'optimizer': bool(optimize), 'runs': int(runs),
                     'evm_version': evm_version},
        'contracts': sorted(contracts, key=lambda c: (not c['deployable'], c['name'])),
        'warnings': [d.get('formattedMessage', d.get('message', ''))
                     for d in diagnostics if d.get('severity') != 'error'],
        'sources': list(sources),
    }


def compile_source(source: str, filename: str = 'Contract.sol',
                   **kwargs) -> Dict[str, Any]:
    return compile_sources({filename: source}, **kwargs)


def _constructor(abi: List[dict]) -> Dict[str, Any]:
    for entry in abi or []:
        if entry.get('type') == 'constructor':
            return {'inputs': entry.get('inputs', []),
                    'payable': entry.get('stateMutability') == 'payable'}
    return {'inputs': [], 'payable': False}


def status() -> Dict[str, Any]:
    have = installed()
    return {'installed': sorted(have, key=_tuple, reverse=True),
            'default': newest(have),
            'download': ALLOW_DOWNLOAD,
            'cache': str(SOLC_DIR),
            'platform': _platform_dir()}
