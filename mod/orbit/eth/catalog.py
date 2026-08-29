"""
The contracts that ship with this module.

Nine self-contained Solidity files under `templates/`, each one a thing people
actually deploy: a token, an NFT, a multisig, an escrow, a splitter, a
key-value registry, a content anchor, a time vault, and a counter to prove the
pipe works. No imports, so they compile with whatever solc this box has and
without a package manager anywhere in the path.

The catalog is read off the files rather than duplicated in a table here — the
title comes from the `@title` natspec line, the summary from `@notice`, and the
constructor signature from an actual compile. A template whose description
disagrees with its code is a template that will be wrong within a month.
"""
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

TEMPLATES = Path(__file__).resolve().parent / 'templates'

# What each one is for, in the words a person would use choosing between them.
USE: Dict[str, str] = {
    'counter': 'the smallest real deploy — proves account, network and receipts work',
    'token': 'an ERC-20 you control the supply of',
    'nft': 'an ERC-721 collection with per-token metadata and an optional public mint',
    'storage': 'a public key→value registry where the first writer keeps the key',
    'multisig': 'n-of-m approval before anything leaves the contract',
    'splitter': 'incoming ETH divided by fixed shares, claimed on demand',
    'escrow': 'buyer funds, seller delivers, an arbiter breaks ties after a deadline',
    'anchor': 'timestamp a CID or hash on chain — cheap proof you had it first',
    'vault': 'ETH you cannot spend until a date you set',
}

ORDER = ['counter', 'token', 'nft', 'storage', 'anchor', 'vault', 'escrow',
         'splitter', 'multisig']


def _natspec(source: str, tag: str) -> Optional[str]:
    """A `/// @tag …` block, joined across its continuation lines."""
    lines = []
    collecting = False
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith(f'/// @{tag}'):
            lines.append(stripped[len(f'/// @{tag}'):].strip())
            collecting = True
        elif collecting and stripped.startswith('///') and not stripped.startswith('/// @'):
            lines.append(stripped[3:].strip())
        elif collecting:
            break
    return ' '.join(l for l in lines if l) or None


CONSTRUCTOR_RE = re.compile(r'constructor\s*\(([^)]*)\)', re.S)


def _constructor(source: str) -> List[Dict[str, str]]:
    """The declared constructor parameters, read from the source.

    A compile gives the same answer more reliably, but the catalog has to be
    listable on a box with no compiler, so this is the cheap path; `describe()`
    replaces it with the compiled truth when it can.
    """
    match = CONSTRUCTOR_RE.search(source)
    if not match or not match.group(1).strip():
        return []
    out = []
    for part in match.group(1).split(','):
        tokens = part.split()
        if len(tokens) >= 2:
            out.append({'type': tokens[0], 'name': tokens[-1]})
    return out


def _contract_name(source_text: str) -> Optional[str]:
    """The declared contract, ignoring the word `contract` inside natspec."""
    for line in source_text.splitlines():
        stripped = line.strip()
        if stripped.startswith(('//', '*', '/*')):
            continue
        match = re.match(r'(?:abstract\s+)?contract\s+(\w+)', stripped)
        if match:
            return match.group(1)
    return None


def names() -> List[str]:
    found = sorted(p.stem for p in TEMPLATES.glob('*.sol'))
    return [n for n in ORDER if n in found] + [n for n in found if n not in ORDER]


def source(name: str) -> str:
    path = TEMPLATES / f'{Path(name).stem}.sol'
    if not path.is_file():
        raise FileNotFoundError(f'no template named {name!r} — have: '
                                f'{", ".join(names())}')
    return path.read_text()


def describe(name: str, compile_it: bool = False) -> Dict[str, Any]:
    text = source(name)
    contract = _contract_name(text)
    out: Dict[str, Any] = {
        'name': Path(name).stem,
        'contract': contract,
        'title': _natspec(text, 'title'),
        'summary': _natspec(text, 'notice'),
        'use': USE.get(Path(name).stem),
        'constructor': _constructor(text),
        'lines': len(text.splitlines()),
        'pragma': (re.search(r'pragma solidity ([^;]+);', text) or [None, None])[1]
        if re.search(r'pragma solidity ([^;]+);', text) else None,
    }
    if compile_it:
        import compiler
        built = compiler.compile_source(text, f'{out["name"]}.sol')
        chosen = next((c for c in built['contracts'] if c['deployable']), None)
        if chosen:
            out.update({'abi': chosen['abi'], 'bytecode_size': chosen['size'],
                        'constructor': chosen['constructor']['inputs'],
                        'compiler': built['compiler'],
                        'methods': sorted(chosen['methods'])})
    return out


def listing(compile_it: bool = False) -> List[Dict[str, Any]]:
    out = []
    for name in names():
        try:
            out.append(describe(name, compile_it))
        except Exception as e:
            out.append({'name': name, 'error': str(e)})
    return out
