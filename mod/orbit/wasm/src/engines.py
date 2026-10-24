"""
Compute types.

wasm is what runs today. It is not what will run tomorrow, and the whole point
of this file is that nothing above it needs to know which is which: the
marketplace, the receipts, the ledger and the console all talk to an Engine,
never to WebAssembly.

An engine answers four questions:

    what is an artifact here?   bytes, and how to read them (inspect)
    where can it run?           browser, server, or somewhere else entirely
    how is a run made repeatable?  seeded, attested, or not at all
    how is a run verified?      replay it, or trust hardware that signed it

Those four are what verification needs, so a compute type that answers them
plugs in whole. `wasm` and `js` are live. The rest are declared with the same
fields and `status='planned'` — a planned engine is listed, queryable, and
refuses to run with a message naming exactly what is missing. That is
deliberately not the same as being absent: it is how the shape of the thing
gets fixed before the implementation exists, and how a listing can say "this
will need a GPU" today.

VERIFICATION IS NOT ONE THING
    replay      run it again somewhere else and compare the output hash. Only
                honest for engines whose determinism is 'seeded'.
    attestation the hardware signs what it ran. For TEEs, where the point is
                that nobody — including us — can replay it.
    consensus   run it N times on N machines and take the majority. For
                engines that are nearly-but-not-bitwise deterministic, which
                is the honest description of most GPU work.
"""
import struct
from typing import Any, Callable, Dict, List, Optional

from . import sandbox

# ── reading a wasm binary ────────────────────────────────────────────
#
# The registry reads the artifact rather than trusting the uploader: what it
# imports, what it exports, how much memory it wants. Publishing a game is
# therefore uploading a module — there is nothing to declare and nothing to
# get wrong.

KIND = {0: 'function', 1: 'table', 2: 'memory', 3: 'global'}
GAME_ABI = ('game_init', 'game_view', 'game_step', 'game_done', 'game_result')


def _leb(data: bytes, pos: int) -> tuple:
    """One unsigned LEB128 → (value, next position)."""
    result = shift = 0
    while True:
        if pos >= len(data):
            raise ValueError('truncated LEB128 — this is not a whole wasm module')
        byte = data[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, pos
        shift += 7
        if shift > 63:
            raise ValueError('LEB128 too long')


def _name(data: bytes, pos: int) -> tuple:
    length, pos = _leb(data, pos)
    return data[pos:pos + length].decode('utf-8', 'replace'), pos + length


def read_wasm(data: bytes) -> Dict[str, Any]:
    """Imports, exports, memory and role, read off the bytes themselves."""
    if len(data) < 8 or data[:4] != b'\x00asm':
        raise ValueError('not a wasm module (missing the \\0asm magic)')
    version = struct.unpack('<I', data[4:8])[0]
    imports: List[Dict[str, str]] = []
    exports: List[Dict[str, str]] = []
    memory: Optional[Dict[str, int]] = None
    pos = 8

    while pos < len(data):
        section, pos = _leb(data, pos)
        size, pos = _leb(data, pos)
        end = pos + size
        if end > len(data):
            raise ValueError('section runs past the end of the module')
        cursor = pos
        if section == 2:                                  # imports
            count, cursor = _leb(data, cursor)
            for _ in range(count):
                mod, cursor = _name(data, cursor)
                field, cursor = _name(data, cursor)
                kind = data[cursor]
                cursor += 1
                imports.append({'module': mod, 'name': field,
                                'kind': KIND.get(kind, str(kind))})
                if kind == 0:                             # type index
                    _, cursor = _leb(data, cursor)
                elif kind == 1:                           # table
                    cursor += 1
                    flags, cursor = _leb(data, cursor)
                    _, cursor = _leb(data, cursor)
                    if flags & 1:
                        _, cursor = _leb(data, cursor)
                elif kind == 2:                           # memory
                    flags, cursor = _leb(data, cursor)
                    _, cursor = _leb(data, cursor)
                    if flags & 1:
                        _, cursor = _leb(data, cursor)
                elif kind == 3:                           # global
                    cursor += 2
        elif section == 5:                                # memory
            count, cursor = _leb(data, cursor)
            if count:
                flags, cursor = _leb(data, cursor)
                initial, cursor = _leb(data, cursor)
                maximum = None
                if flags & 1:
                    maximum, cursor = _leb(data, cursor)
                memory = {'initial_pages': initial, 'max_pages': maximum}
        elif section == 7:                                # exports
            count, cursor = _leb(data, cursor)
            for _ in range(count):
                field, cursor = _name(data, cursor)
                kind = data[cursor]
                cursor += 1
                _, cursor = _leb(data, cursor)
                exports.append({'name': field, 'kind': KIND.get(kind, str(kind))})
        pos = end

    names = [e['name'] for e in exports]
    return {
        'format': 'wasm',
        'version': version,
        'imports': imports,
        'exports': exports,
        'memory': memory,
        'role': wasm_role(names),
        'entries': [e['name'] for e in exports if e['kind'] == 'function'],
    }


def wasm_role(exports: List[str]) -> str:
    """What a module *is*, decided by what it exports. See runtime/abi.mjs."""
    if all(fn in exports for fn in GAME_ABI):
        return 'game'
    if 'play' in exports:
        return 'player'
    if 'run' in exports:
        return 'function'
    if '_start' in exports:
        return 'command'
    return 'module'


def read_js(data: bytes) -> Dict[str, Any]:
    """A js artifact is source. Its exports are the functions it defines."""
    import re
    source = data.decode('utf-8', 'replace')
    names = re.findall(r'^\s*(?:async\s+)?function\s+([A-Za-z_$][\w$]*)',
                       source, re.MULTILINE)
    return {
        'format': 'js',
        'imports': [],
        'exports': [{'name': n, 'kind': 'function'} for n in names],
        'entries': names,
        'role': wasm_role(names),
        'lines': source.count('\n') + 1,
    }


# ── the engines ──────────────────────────────────────────────────────

class Engine:
    """One compute type. Live ones override `execute`."""

    id = ''
    name = ''
    status = 'planned'
    summary = ''
    artifact = ''
    media_type = 'application/octet-stream'
    extensions: tuple = ()
    venues: tuple = ()
    determinism = 'none'      # seeded | attested | none
    verify = 'replay'         # replay | attestation | consensus
    default_entry = 'run'
    needs = ''                # what a planned engine is waiting on

    def descriptor(self) -> Dict[str, Any]:
        return {
            'id': self.id, 'name': self.name, 'status': self.status,
            'summary': self.summary, 'artifact': self.artifact,
            'media_type': self.media_type, 'extensions': list(self.extensions),
            'venues': list(self.venues), 'determinism': self.determinism,
            'verify': self.verify, 'default_entry': self.default_entry,
            **({'needs': self.needs} if self.status != 'live' else {}),
        }

    def inspect(self, data: bytes) -> Dict[str, Any]:
        return {'format': self.id, 'bytes': len(data), 'role': 'module',
                'imports': [], 'exports': [], 'entries': []}

    def execute(self, data: bytes, **job) -> Dict[str, Any]:
        raise NotImplementedError(
            f"the '{self.id}' compute type is declared but not implemented here"
            + (f' — it needs {self.needs}' if self.needs else '')
            + f". Live compute types: {', '.join(e.id for e in live())}.")


class WasmEngine(Engine):
    id = 'wasm'
    name = 'WebAssembly'
    status = 'live'
    summary = ('Any wasm module. The host seeds the clock and the PRNG and '
               'offers no sockets and no filesystem, so a run is a function '
               'of (module, input, seed) and nothing else.')
    artifact = 'a .wasm binary'
    media_type = 'application/wasm'
    extensions = ('.wasm',)
    venues = ('browser', 'server')
    determinism = 'seeded'
    verify = 'replay'

    def inspect(self, data: bytes) -> Dict[str, Any]:
        out = read_wasm(data)
        out['bytes'] = len(data)
        return out

    def execute(self, data: bytes, **job) -> Dict[str, Any]:
        import base64
        return sandbox.run({
            'engine': 'wasm',
            'artifact_b64': base64.b64encode(data).decode(),
            'entry': job.get('entry') or 'run',
            'input': job.get('input') or '',
            'seed': int(job.get('seed') or 0),
        }, limits=job.get('limits'))


class JsEngine(Engine):
    id = 'js'
    name = 'JavaScript'
    status = 'live'
    summary = ('Source that defines run(input, ctx). Math.random and Date are '
               'replaced by the seeded host, and the nondeterministic globals '
               'are shadowed out — same seed, same bytes.')
    artifact = 'a .js source file defining run(input, ctx)'
    media_type = 'text/javascript'
    extensions = ('.js', '.mjs')
    venues = ('browser', 'server')
    determinism = 'seeded'
    verify = 'replay'

    def inspect(self, data: bytes) -> Dict[str, Any]:
        out = read_js(data)
        out['bytes'] = len(data)
        return out

    def execute(self, data: bytes, **job) -> Dict[str, Any]:
        return sandbox.run({
            'engine': 'js',
            'artifact': data.decode('utf-8', 'replace'),
            'entry': job.get('entry') or 'run',
            'input': job.get('input') or '',
            'seed': int(job.get('seed') or 0),
        }, limits=job.get('limits'))


class PythonEngine(Engine):
    id = 'python'
    name = 'Python'
    status = 'planned'
    summary = ('Python source, in Pyodide in the tab and in a locked-down '
               'interpreter on the server. Determinism needs the same '
               'treatment the js engine gets: seeded random, frozen time, and '
               'an import allowlist.')
    artifact = 'a .py source file defining run(input, ctx)'
    media_type = 'text/x-python'
    extensions = ('.py',)
    venues = ('browser', 'server')
    determinism = 'seeded'
    verify = 'replay'
    needs = ('a Pyodide bundle for the browser venue and a seeded, '
             'import-restricted interpreter for the server venue')


class ContainerEngine(Engine):
    id = 'container'
    name = 'OCI container'
    status = 'planned'
    summary = ('An image reference, run on the server only. Not seeded — a '
               'container can read the real clock — so a replay proves much '
               'less than for wasm, and the honest verification here is '
               'consensus across independent runners.')
    artifact = 'an OCI image reference, digest-pinned'
    media_type = 'application/vnd.oci.image.manifest.v1+json'
    venues = ('server',)
    determinism = 'none'
    verify = 'consensus'
    needs = 'a container runtime on the box and a policy for what it may reach'


class TeeEngine(Engine):
    id = 'tee'
    name = 'Confidential compute'
    status = 'planned'
    summary = ('A workload inside attested hardware. The point is that nobody '
               'can replay it — including us — so the receipt carries the '
               "hardware's own signature over what ran instead of a second "
               'run that agreed.')
    artifact = 'an image plus the evidence policy it must satisfy'
    venues = ('server',)
    determinism = 'attested'
    verify = 'attestation'
    needs = ("a provider — this fleet's cathedral module already speaks to "
             'attested TDX and confidential GPUs and returns signed receipts')


class GpuEngine(Engine):
    id = 'gpu'
    name = 'GPU kernel'
    status = 'planned'
    summary = ('Kernels on rented GPUs. Floating-point reduction order is not '
               'guaranteed across devices, so bitwise replay is the wrong '
               'test: the verification is N independent runs agreeing within '
               'a published tolerance.')
    artifact = 'a kernel plus its launch parameters'
    venues = ('server',)
    determinism = 'none'
    verify = 'consensus'
    needs = ('a GPU provider — the lium and targon modules already rent '
             'Bittensor GPUs — and a numeric tolerance per listing')


REGISTRY: Dict[str, Engine] = {
    e.id: e for e in (WasmEngine(), JsEngine(), PythonEngine(),
                      ContainerEngine(), TeeEngine(), GpuEngine())
}


def get(engine_id: str) -> Engine:
    engine = REGISTRY.get((engine_id or '').lower())
    if not engine:
        raise ValueError(f'unknown compute type {engine_id!r} — '
                         f'this box carries: {", ".join(REGISTRY)}')
    return engine


def live() -> List[Engine]:
    return [e for e in REGISTRY.values() if e.status == 'live']


def descriptors() -> List[Dict[str, Any]]:
    return [e.descriptor() for e in REGISTRY.values()]


def guess(filename: str, data: bytes = b'') -> str:
    """The compute type an upload most likely is, from its bytes then its name."""
    if data[:4] == b'\x00asm':
        return 'wasm'
    lowered = (filename or '').lower()
    for engine in REGISTRY.values():
        if any(lowered.endswith(ext) for ext in engine.extensions):
            return engine.id
    return 'wasm'


def inspect(engine_id: str, data: bytes) -> Dict[str, Any]:
    return get(engine_id).inspect(data)


def execute(engine_id: str, data: bytes, **job) -> Dict[str, Any]:
    return get(engine_id).execute(data, **job)
