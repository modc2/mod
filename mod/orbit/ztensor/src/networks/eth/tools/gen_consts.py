"""Generate the Starknet Poseidon permutation for the EVM: hand-assembled
bytecode (eth/hades.json, test/HadesCode.sol) deployed at a CREATE2 address,
and eth/contract/src/Hades.sol, the library that calls it.

    python3 src/networks/eth/tools/gen_consts.py

Source: lambdaworks-crypto 0.13 `OPTIMIZED_ROUND_CONSTANTS` (107 felts), the
same table near/contract/src/poseidon.rs is generated from (tools/gen_fp.py), checked
against the Cairo fixture (near/tests/fixture.json key ids) before writing.
"""
import glob
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
P = 2 ** 251 + 17 * 2 ** 192 + 1
src = sorted(glob.glob(os.path.expanduser(
    '~/.cargo/registry/src/*/lambdaworks-crypto-0.13.0/src/hash/poseidon/'
    'starknet/parameters.rs')))[0]
params = open(src).read()
i = params.index('const OPTIMIZED_ROUND_CONSTANTS')
RC = [int(h, 16) for h in re.findall(
    r'from_hex_unchecked\(\s*"([0-9a-fA-F]+)"', params[i:params.index('];', i)])]
assert len(RC) == 107


def hades(s):
    k = 0
    def mix():
        t = sum(s) % P
        s[0], s[1], s[2] = (t + 2 * s[0]) % P, (t - 2 * s[1]) % P, (t - 3 * s[2]) % P
    for _ in range(4):
        for j in range(3):
            s[j] = pow((s[j] + RC[k + j]) % P, 3, P)
        mix()
        k += 3
    for _ in range(83):
        s[2] = pow((s[2] + RC[k]) % P, 3, P)
        mix()
        k += 1
    for _ in range(4):
        for j in range(3):
            s[j] = pow((s[j] + RC[k + j]) % P, 3, P)
        mix()
        k += 3


def hash_many(xs):
    v = list(xs) + [1]
    if len(v) % 2:
        v.append(0)
    s = [0, 0, 0]
    for a in range(0, len(v), 2):
        s[0] = (s[0] + v[a]) % P
        s[1] = (s[1] + v[a + 1]) % P
        hades(s)
    return s[0]


def short(t):
    return int.from_bytes(t.encode(), 'big')


if __name__ == '__main__':
    fx = json.load(open(os.path.join(HERE, '..', '..', 'near', 'contract', 'tests', 'fixture.json')))
    for k in fx['keys']:
        assert hash_many([short('zuma-key'), int(k['x'], 16), int(k['y'], 16)]) == int(k['key_id'], 16)
    # the permutation, fully unrolled in Yul with the constants inline:
    # no memory table, no loop, ~3x cheaper than a loop over an mload table
    hp = f'0x{P:x}'
    body = []
    def full(k):
        for j, v in enumerate(('s0', 's1', 's2')):
            body.append(f'            {v} := addmod({v}, 0x{RC[k + j]:x}, p)')
            body.append(f'            {v} := mulmod(mulmod({v}, {v}, p), {v}, p)')
    def partial(k):
        body.append(f'            s2 := addmod(s2, 0x{RC[k]:x}, p)')
        body.append('            s2 := mulmod(mulmod(s2, s2, p), s2, p)')
    def mix():
        body.append('            t := addmod(addmod(s0, s1, p), s2, p)')
        body.append('            s0 := addmod(t, add(s0, s0), p)')
        body.append('            s1 := addmod(t, sub(p2, add(s1, s1)), p)')
        body.append('            s2 := addmod(t, sub(p3, mul(3, s2)), p)')
    k = 0
    for _ in range(4):
        full(k); mix(); k += 3
    for _ in range(83):
        partial(k); mix(); k += 1
    for _ in range(4):
        full(k); mix(); k += 3
    assert k == 107
    # Hand-assembled EVM bytecode. solc cannot emit this compactly: its
    # optimizer re-pushes the 32-byte literal p at each of ~900 uses (39 KB,
    # over the 24 KB code limit), an opaque p sends it into a 15 GB search,
    # and even unoptimized Yul constant-folds p. Here p lives at the bottom
    # of the stack ([p, s0, s1, s2], s2 on top) and every use is a DUP.
    OP = {'STOP': 0, 'ADD': 0x01, 'MUL': 0x02, 'SUB': 0x03, 'ADDMOD': 0x08,
          'MULMOD': 0x09, 'POP': 0x50, 'MSTORE': 0x52, 'CALLDATALOAD': 0x35,
          'RETURN': 0xf3, 'PUSH0': 0x5f, 'CODECOPY': 0x39}
    def push(v):
        if v == 0:
            return bytes([0x5f])
        n = (v.bit_length() + 7) // 8
        return bytes([0x5f + n]) + v.to_bytes(n, 'big')
    def dup(i): return bytes([0x7f + i])
    def swap(i): return bytes([0x8f + i])
    def op(*names): return bytes(OP[n] for n in names)
    def sbox(c):  # top x -> (x + c)^3, p at depth 4
        return (push(c) + dup(5) + swap(2) + op('ADDMOD') +
                dup(4) + dup(2) + dup(1) + op('MULMOD') +
                dup(5) + swap(2) + op('MULMOD'))
    MIX = (  # [p,s0,s1,s2] -> t = s0+s1+s2; (t+2s0, t-2s1, t-3s2)
        dup(4) + dup(3) + dup(5) + op('ADDMOD') +          # u = s0 + s1
        dup(5) + swap(1) + dup(3) + op('ADDMOD') +          # t = u + s2
        dup(5) + dup(2) + dup(4) + dup(3) + op('SUB') + push(3) + op('MUL', 'ADDMOD') +  # s2'
        dup(6) + dup(3) + dup(6) + dup(3) + op('SUB') + dup(1) + op('ADD', 'ADDMOD') +   # s1'
        dup(7) + dup(4) + dup(8) + dup(1) + op('ADD', 'ADDMOD') +                        # s0'
        swap(6) + op('POP') + swap(4) + op('POP') + swap(2) + op('POP', 'POP'))
    code = (push(P) + push(0) + op('CALLDATALOAD') + push(32) + op('CALLDATALOAD') +
            push(64) + op('CALLDATALOAD'))
    def full(k):
        return (sbox(RC[k + 2]) + swap(1) + sbox(RC[k + 1]) + swap(1) +
                swap(2) + sbox(RC[k]) + swap(2) + MIX)
    k = 0
    for _ in range(4):
        code += full(k); k += 3
    for _ in range(83):
        code += sbox(RC[k]) + MIX; k += 1
    for _ in range(4):
        code += full(k); k += 3
    assert k == 107
    code += (push(64) + op('MSTORE') + push(32) + op('MSTORE') + push(0) + op('MSTORE') +
             push(96) + push(0) + op('RETURN'))
    runtime = code
    # init: CODECOPY(0, len(init), len(runtime)); RETURN(0, len(runtime))
    def initcode(off):
        return (push(len(runtime)) + push(off) + push(0) + op('CODECOPY') +
                push(len(runtime)) + push(0) + op('RETURN'))
    off = len(initcode(0xffff))
    pre = initcode(off)
    pre = pre + b'\0' * (off - len(pre))  # pad up to the offset it names
    init = pre + runtime
    eth = os.path.join(HERE, '..', 'contract')
    from eth_hash.auto import keccak
    DEPLOYER = bytes.fromhex('4e59b44847b379578588920ca78fbf26c0b4956c')
    salt = b'\0' * 32
    addr = keccak(b'\xff' + DEPLOYER + salt + keccak(init))[12:]
    # checksum the address (solc insists on it)
    h = keccak(addr.hex().encode()).hex()
    caddr = '0x' + ''.join(c.upper() if c.isalpha() and int(h[i], 16) >= 8 else c
                           for i, c in enumerate(addr.hex()))
    sol = ['// SPDX-License-Identifier: MIT', 'pragma solidity ^0.8.24;', '',
           "/// @notice Starknet's Poseidon permutation, as a call into the Hades",
           '/// contract (hand-assembled, see eth/tools/gen_consts.py) at its CREATE2',
           '/// address: the standard deterministic deployer (0x4e59b448...), salt 0,',
           '/// so it is the same address on every EVM chain. ~16k gas a permutation',
           '/// plus ~400 of call overhead. Reverts if Hades is not deployed.',
           '/// GENERATED by eth/tools/gen_consts.py — do not edit.',
           'library Hades {',
           f'    address internal constant ADDR = {caddr};',
           '',
           '    /// @dev Inputs must be < P.',
           '    function permute(uint256 a, uint256 b, uint256 c) internal view returns (uint256, uint256, uint256) {',
           '        assembly {',
           '            let m := mload(0x40)',
           '            mstore(m, a)',
           '            mstore(add(m, 32), b)',
           '            mstore(add(m, 64), c)',
           f'            if iszero(staticcall(gas(), {caddr}, m, 96, m, 96)) {{ revert(0, 0) }}',
           '            if iszero(eq(returndatasize(), 96)) { revert(0, 0) } // no code there',
           '            a := mload(m)',
           '            b := mload(add(m, 32))',
           '            c := mload(add(m, 64))',
           '        }',
           '        return (a, b, c);',
           '    }',
           '}', '']
    open(os.path.join(eth, 'src', 'Hades.sol'), 'w').write('\n'.join(sol))
    code = ['// SPDX-License-Identifier: MIT', 'pragma solidity ^0.8.24;', '',
            '/// @notice Hades bytecode, for tests (vm.etch) and deploy scripts.',
            '/// GENERATED by eth/tools/gen_consts.py — do not edit.',
            'library HadesCode {',
            f'    bytes internal constant INIT = hex"{init.hex()}";',
            f'    bytes internal constant RUNTIME = hex"{runtime.hex()}";',
            '}', '']
    open(os.path.join(eth, 'test', 'HadesCode.sol'), 'w').write('\n'.join(code))
    json.dump({'address': caddr, 'deployer': '0x' + DEPLOYER.hex(), 'salt': '0x' + salt.hex(),
               'init': '0x' + init.hex(), 'runtime': '0x' + runtime.hex()},
              open(os.path.join(eth, 'hades.json'), 'w'), indent=1)
    print('wrote Hades.sol, test/HadesCode.sol, hades.json;', caddr,
          f'runtime {len(runtime)} B')
