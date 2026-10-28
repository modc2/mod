"""A small WebAssembly text (WAT) virtual machine, dependency-free.

The paper pool's distribution contract ships as WASM text so the rules of
who-gets-paid are a portable artifact anyone can run in any conformant
runtime — but this host should not need wasmtime, a package index, or
anything beyond the standard library to enforce its own contract. So this
module executes the folded-form WAT subset the contract uses directly:
i32/i64 arithmetic, locals, linear memory, block/loop/br/br_if/if/return.

Semantics follow the spec where the subset touches it: integers wrap at
their width, division is by-value unsigned/signed as named, div-by-zero
traps (raises), `br` to a block exits it while `br` to a loop repeats it.
It is not a general wasm engine — unknown instructions raise, which for a
consensus kernel is the right failure mode.
"""

import re
from typing import Dict, List, Optional

M32 = 0xFFFFFFFF
M64 = 0xFFFFFFFFFFFFFFFF
PAGE = 65536


class WatError(Exception):
    pass


class Trap(Exception):
    """A wasm trap — the instruction's defined failure, e.g. div by zero."""


# ── Parsing ──────────────────────────────────────────────────────────

_TOKEN = re.compile(r'\(;.*?;\)|;;[^\n]*|[()]|"(?:\\.|[^"\\])*"|[^\s()";]+',
                    re.DOTALL)


def parse(text: str) -> List:
    """WAT source → nested lists of atoms (comments dropped)."""
    stack: List[List] = [[]]
    for tok in _TOKEN.findall(text):
        if tok.startswith(';;') or tok.startswith('(;'):
            continue
        if tok == '(':
            stack.append([])
        elif tok == ')':
            done = stack.pop()
            if not stack:
                raise WatError('unbalanced )')
            stack[-1].append(done)
        else:
            stack[-1].append(tok)
    if len(stack) != 1:
        raise WatError('unbalanced (')
    return stack[0]


def _int(atom: str) -> int:
    atom = atom.replace('_', '')
    neg = atom.startswith('-')
    body = atom[1:] if neg else atom
    value = int(body, 16) if body.lower().startswith('0x') else int(body)
    return -value if neg else value


# ── Signedness helpers ───────────────────────────────────────────────

def _u(x: int, mask: int) -> int:
    return x & mask


def _s(x: int, mask: int) -> int:
    x &= mask
    sign = (mask >> 1) + 1
    return x - mask - 1 if x & sign else x


def _div_u(a, b, mask):
    if b & mask == 0:
        raise Trap('integer divide by zero')
    return (a & mask) // (b & mask)


def _rem_u(a, b, mask):
    if b & mask == 0:
        raise Trap('integer divide by zero')
    return (a & mask) % (b & mask)


def _binop(name):
    """The arithmetic/comparison table, shared by i32.* and i64.*."""
    return {
        'add': lambda a, b, m: (a + b) & m,
        'sub': lambda a, b, m: (a - b) & m,
        'mul': lambda a, b, m: (a * b) & m,
        'div_u': lambda a, b, m: _div_u(a, b, m),
        'rem_u': lambda a, b, m: _rem_u(a, b, m),
        'and': lambda a, b, m: (a & b) & m,
        'or': lambda a, b, m: (a | b) & m,
        'xor': lambda a, b, m: (a ^ b) & m,
        'shl': lambda a, b, m: (a << (b & (63 if m == M64 else 31))) & m,
        'shr_u': lambda a, b, m: (a & m) >> (b & (63 if m == M64 else 31)),
        'eq': lambda a, b, m: 1 if (a & m) == (b & m) else 0,
        'ne': lambda a, b, m: 1 if (a & m) != (b & m) else 0,
        'lt_u': lambda a, b, m: 1 if (a & m) < (b & m) else 0,
        'gt_u': lambda a, b, m: 1 if (a & m) > (b & m) else 0,
        'le_u': lambda a, b, m: 1 if (a & m) <= (b & m) else 0,
        'ge_u': lambda a, b, m: 1 if (a & m) >= (b & m) else 0,
        'lt_s': lambda a, b, m: 1 if _s(a, m) < _s(b, m) else 0,
        'gt_s': lambda a, b, m: 1 if _s(a, m) > _s(b, m) else 0,
        'le_s': lambda a, b, m: 1 if _s(a, m) <= _s(b, m) else 0,
        'ge_s': lambda a, b, m: 1 if _s(a, m) >= _s(b, m) else 0,
    }[name]


# ── Control flow via exceptions ──────────────────────────────────────

class _Branch(Exception):
    def __init__(self, label):
        self.label = label


class _Return(Exception):
    def __init__(self, value):
        self.value = value


class Func:
    def __init__(self, node: List):
        self.export: Optional[str] = None
        self.params: List[str] = []
        self.locals: List[str] = []
        self.results = 0
        self.body: List = []
        for item in node[1:]:
            if isinstance(item, str):        # a $name for the func itself
                continue
            head = item[0] if item else None
            if head == 'export':
                self.export = item[1].strip('"')
            elif head == 'param':
                self.params += [a for a in item[1:] if a.startswith('$')]
            elif head == 'local':
                self.locals += [a for a in item[1:] if a.startswith('$')]
            elif head == 'result':
                self.results = len(item) - 1
            else:
                self.body.append(item)


class Module:
    """One parsed WAT module: functions by export name + linear memory."""

    def __init__(self, text: str):
        top = parse(text)
        if not top or top[0][0] != 'module':
            raise WatError('not a (module ...)')
        self.funcs: Dict[str, Func] = {}
        pages = 1
        for item in top[0][1:]:
            if not isinstance(item, list):
                continue
            if item[0] == 'memory':
                nums = [a for a in item[1:] if isinstance(a, str)
                        and not a.startswith('$')]
                if nums:
                    pages = _int(nums[0])
            elif item[0] == 'func':
                fn = Func(item)
                if fn.export:
                    self.funcs[fn.export] = fn
        self.memory = bytearray(pages * PAGE)

    # -- host-side memory access --------------------------------------

    def _bounds(self, addr: int, size: int):
        if addr < 0 or addr + size > len(self.memory):
            raise Trap(f'out of bounds memory access at {addr}')

    def read_u64(self, addr: int) -> int:
        self._bounds(addr, 8)
        return int.from_bytes(self.memory[addr:addr + 8], 'little')

    def write_u64(self, addr: int, value: int):
        self._bounds(addr, 8)
        self.memory[addr:addr + 8] = (value & M64).to_bytes(8, 'little')

    # -- execution -----------------------------------------------------

    def invoke(self, name: str, *args) -> Optional[int]:
        fn = self.funcs.get(name)
        if fn is None:
            raise WatError(f'no export {name!r}')
        if len(args) != len(fn.params):
            raise WatError(f'{name} takes {len(fn.params)} args')
        frame = {p: int(a) for p, a in zip(fn.params, args)}
        frame.update({l: 0 for l in fn.locals})
        try:
            result = self._block(fn.body, frame)
        except _Return as ret:
            result = ret.value
        except _Branch:
            raise WatError('branch escaped function body')
        return result if fn.results else None

    def _block(self, instrs: List, frame: Dict) -> Optional[int]:
        value = None
        for instr in instrs:
            value = self._eval(instr, frame)
        return value

    def _eval(self, node, frame) -> Optional[int]:
        op = node[0]

        if op == 'local.get':
            return frame[node[1]]
        if op == 'local.set':
            frame[node[1]] = self._eval(node[2], frame)
            return None
        if op == 'local.tee':
            frame[node[1]] = self._eval(node[2], frame)
            return frame[node[1]]
        if op in ('i32.const', 'i64.const'):
            return _int(node[1])
        if op in ('i64.eqz', 'i32.eqz'):
            mask = M64 if op[1] == '6' else M32
            return 1 if self._eval(node[1], frame) & mask == 0 else 0
        if op in ('i64.load', 'i32.load'):
            addr = self._eval(node[1], frame) & M32
            size = 8 if op[1] == '6' else 4
            self._bounds(addr, size)
            return int.from_bytes(self.memory[addr:addr + size], 'little')
        if op in ('i64.store', 'i32.store'):
            addr = self._eval(node[1], frame) & M32
            value = self._eval(node[2], frame)
            size = 8 if op[1] == '6' else 4
            mask = M64 if size == 8 else M32
            self._bounds(addr, size)
            self.memory[addr:addr + size] = (value & mask).to_bytes(size,
                                                                    'little')
            return None
        if op == 'i32.wrap_i64':
            return self._eval(node[1], frame) & M32
        if op in ('i64.extend_i32_u',):
            return self._eval(node[1], frame) & M32

        if op == 'block':
            label, body = self._labeled(node)
            try:
                return self._block(body, frame)
            except _Branch as br:
                if br.label == label:
                    return None                # br to a block = exit it
                raise
        if op == 'loop':
            label, body = self._labeled(node)
            while True:
                try:
                    return self._block(body, frame)  # fall-through = exit
                except _Branch as br:
                    if br.label != label:
                        raise                  # br to a loop = repeat it
        if op == 'br':
            raise _Branch(node[1])
        if op == 'br_if':
            if self._eval(node[2], frame):
                raise _Branch(node[1])
            return None
        if op == 'if':
            cond = self._eval(node[1], frame)
            for arm in node[2:]:
                if arm[0] == ('then' if cond else 'else'):
                    return self._block(arm[1:], frame)
            return None
        if op == 'return':
            raise _Return(self._eval(node[1], frame) if len(node) > 1
                          else None)
        if op == 'nop':
            return None

        if '.' in op:
            prefix, name = op.split('.', 1)
            if prefix in ('i32', 'i64'):
                mask = M64 if prefix == 'i64' else M32
                try:
                    fn = _binop(name)
                except KeyError:
                    raise WatError(f'unsupported instruction {op!r}')
                a = self._eval(node[1], frame)
                b = self._eval(node[2], frame)
                return fn(a, b, mask)

        raise WatError(f'unsupported instruction {op!r}')

    @staticmethod
    def _labeled(node):
        if len(node) > 1 and isinstance(node[1], str):
            return node[1], node[2:]
        return None, node[1:]
