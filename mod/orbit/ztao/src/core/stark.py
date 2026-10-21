"""stark.py — the STARK curve, Starknet Poseidon and ztao's LSAG in pure
Python. Used by the Solana driver to compute verification HINTS from a
signature the Cairo prover made (and so a fourth, independent check that
the signature is valid: if the challenge chain does not close here, no hint
set can make the Solana program accept it).

Field p = 2^251 + 17*2^192 + 1, curve y^2 = x^3 + x + BETA, Poseidon =
Hades width 3, 4 + 83 + 4 rounds (the 107 optimized round constants of
lambdaworks-crypto 0.13, the table near/contract/src/poseidon.rs and eth/ use).
"""

P = 2 ** 251 + 17 * 2 ** 192 + 1
BETA = 0x6f21413efbe40de150e596d72f7a8c5609ad26c15c915c1f4cdfcb99cee9e89
G = (0x1ef15c18599971b7beced415a40f0c7deacfd9b0d1819e03d723d8bc943cfca,
     0x5668060aa49730b7be4801df46ec62de53ecd11abe43a32873000c36e8dc1f)
INF = None


def short(s):
    return int.from_bytes(s.encode(), 'big')


TAG_KEY, TAG_H2P = short('zuma-key'), short('zuma-h2p')
TAG_MSG, TAG_CHAL = short('zuma-msg'), short('zuma-chal')

RC = [
    0x6861759ea556a2339dd92f9562a30b9e58e2ad98109ae4780b7fd8eac77fe6f, 0x3827681995d5af9ffc8397a3d00425a3da43f76abf28a64e4ab1a22f27508c4, 0x3a3956d2fad44d0e7f760a2277dc7cb2cac75dc279b2d687a0dbe17704a8309,
    0x626c47a7d421fe1f13c4282214aa759291c78f926a2d1c6882031afe67ef4cd, 0x78985f8e16505035bd6df5518cfd41f2d327fcc948d772cadfe17baca05d6a6, 0x5427f10867514a3204c659875341243c6e26a68b456dc1d142dcf34341696ff,
    0x5af083f36e4c729454361733f0883c5847cd2c5d9d4cb8b0465e60edce699d7, 0x7d71701bde3d06d54fa3f74f7b352a52d3975f92ff84b1ac77e709bfd388882, 0x603da06882019009c26f8a6320a1c5eac1b64f699ffea44e39584467a6b1d3e,
    0x4332a6f6bde2f288e79ce13f47ad1cdeebd8870fd13a36b613b9721f6453a5d, 0x53d0ebf61664c685310a04c4dec2e7e4b9a813aaeff60d6c9e8caeb5cba78e7, 0x5346a68894845835ae5ebcb88028d2a6c82f99f928494ee1bfc2d15eaabfebc,
    0x4b085eb1df4258c3453cc97445954bf3433b6ab9dd5a99592864c00f54a3f9a, 0x731cfd19d508285965f12a079b2a169fdfe0a8e610e6f2d5ca5d7b0961f6d96, 0x217d08b5339852bcc6f7a774936b3e72ecd9e1f9a73d743f8079c1e3587eeaa,
    0xc935dd633b0fd63599b13c850dab3cb966ba510c81b20959e267008518c6e, 0x52af8d378dd6772ee187ed23f79a7d98cf5a0a387103971467fe940e7b8b2be, 0x294851c98b2682f1ec9918b9f12fcceaa6e28a7b79b2e506362cda595f8ab75,
    0x11b59990bacc280824d1021418d4f589da8c30063471494c204b169ab086064, 0x4b4df56e3d7753f91960d59ae099b9beb2ce690e6bbdcd0b599d49ceb2acd6a, 0x5eecfa15a757dc3ecae9fbd8ff06e466243534f30629fc5f1cf09eb5161ac4,
    0x680bfdd8b9680e04659227634a1ec5282e5a7cef81b15677f8448bda4279059, 0x1d0bf8fab0a1a7a14e2930794f7a3065c17e10b1cedd791b8877d97acd85053, 0x2c2c8c79f808ace54ba207053c0d412c0fc11a610f14c48876701a37e32f464,
    0x354ec9ed01d20ec52aae19a9b858d3474d8234c11ad7bce630ad56c54afa562, 0x30df20fcf6427bac38bb5d1a42287f4e4136ac5892340e994e6ea28deec1e55, 0x528cf329c64e7ee3040bafbdeff61e241d99b424091e31472eda296fc9c6778,
    0x40416f24f623534634789660df5435ebf0c3e0c69e6c5b5ff6e757930bd1960, 0x380c8f936e2ed9fd488ae3bac7dce315ba21b11e88339cd5444435ccc9ea38, 0x1cc4f5d5603d176f1a8e344392efd2d03ad0541832829d245e0e2291f255b75,
    0x5728917af5da91f9539310d99f5d142e011d6c8e015ea5423c502aa99c09752, 0xefb450a9e86e1a46e295a348f0f23590925107d17c56d7c788fecc17219aa1, 0x2020d74d36c421ae1a025616b342d0784b8fcd977de6c53a6c26693774dca99,
    0x7cfb309b75fd3bf2705558ae511dc82335050969f4bf84fa2b7b4f583989287, 0x4651e48b2e9349a5365e009ece626809d7b7d02a617eb98c785a784812d75e9, 0xd77627b270f65122d0269719da923ccae822d9aad0f0947a3b5c8f71c0dcc7,
    0x199ad3d641b54c4d571b3fe37773a8b82b003377f0dd8b7d3b7758c32908ea8, 0x44f33640a8ecfd3973e2e9172a7333482b2d297be2da289319e72d137cdfe6e, 0x7e4adf9894d964189d00a02dcf1e6be7f801234f5216eab6b6f366b6701abf7,
    0x3641fa5b3c90452f5ff808f8a9817eda7c6aecfb5471dfdca559fb4e711ee90, 0x3de5729efd2fcbd897a49a78fa923fc306df32e6e2f0e02d0eee2c2cc3f3533, 0x62691891a3fc1e27f622966ca0be20c06563500c8f06c9bdb77bd2882d6c994,
    0x6608d3bf11c18e4688739f72205763d1590cc4f9885ae1d86e96e0604baa0be, 0x11c9c9b39cac71e3419726ce779116d07249f51cbdda4fd98c25cbbf593a316, 0x61e23b58203269caef0850f74da27b9748e3312ea40c6844dd68c557c462ad7,
    0x4182cd9ab1d9488f870a572010bc2a3d9878440b25951e4ce010855cf83bdc8, 0x520fe6c4a096793f9055e6823116d15f1df2fe89d306f9965f6a59f4f3ecb71, 0x346b2b2d6e5810129e093093dcd3dfa99ed6d71f47723ea3fbe4d4e2fd4afa1,
    0x1359ca923e7f1448ec1dd2a3684bee4e8b682c8e8e973acea72877ce9f7e6cf, 0x47c655f55cf307800dfefdad24de86fde9deadab145a1b392420f37b95d9675, 0x4ab291f16555fa8a968cd7c9c285a9598efd925f2d58b7aa38ad87dca8441a8,
    0x39f409c7c782101223d1f6f7d86c21a22c44ef959510e392c9c7c5d17c629c5, 0x44be36b782f882ad86eecb0cd6beb02e1a2f9fb5587a3babfacead0cafb6052, 0x50a1dfde9b504ad2906db6eb5b507203cd1ceb394c52ce7107679a53a0d538b,
    0x5c753c14da89e287b181c0dd11ac6c3680bdd7f1017dae083e7aebbeab183ab, 0x2cf6306ed32232106c8015a3b180f386eee93e15f7b4f4fa57746525fc0520c, 0x2c2014634d52e27420873cf347429091dfc6380689bd4f54d7d8e502c1c3a09,
    0x3cfb9c5bd93e02b2fdacde2058e33e5975c446345f010d850fc09cdf86ed8a1, 0x363fa71a383cf3897933f1411fc5f806e311e84f72cb50a9ea4e1281f6b0299, 0x728199657067ee16947b3fc76271676b4901b2a3686cffebcb960da91b05df8,
    0x3fdfbd47d27f3d34f0723b728e8921dc9bde34a9872df5a652a078d7e4ee021, 0x7f241379440cacd7dc0efbe7858eb7de53cc02ca7d24197945c453398eff449, 0x5b2e8771ea9a0004e3bf056f3727797cbb457a27574d5f104354e52a5c25f0b,
    0xa8ddbce708de44a7e0b3b0333146e1e910245be6bf822ea057a081bda2e23e, 0x2d521e0daca24e431aa47cd90a0f551c12270e533835613edce2e19aa9b0f61, 0x6cdbc0f2aa54d2cf7d5ac3b93f855af03eef7b07aaee00341a6266c30e08ae6,
    0x3dd96a17111ec8f4c5da3ad6794c0961ceee452cbe92c7a0941112b36ed9bf3, 0x5eafb1edeedc5c07ac07fdd06159344a2cfb92196a65d9ec0c5e732c36687dc, 0x4ab038d7b09eda9324577b260feaebdbcec5a7b7c7f449b312cfcd065c207e6,
    0x4ca71981e4df6b505d2b0d94e235608463c58052570f68e495fc80c7fdef220, 0x6dee9c6da4617e32aa419899c8ea8137e9b59d7e2759ffe573c15b77e413d2f, 0x58f9e60b34ddab84dcbe2396065a4305b4a795a4770e4541e625d0460c6f186,
    0x47b7b4a802a10c1e6c9c735db6c34042d290906f274bea8fcecef17fc9af632, 0x1849bcdb9ad7171096ecc936a186774084a074be0bfc0fbb9463a06a2bd430c, 0x41870fbe04438348af5767bddaecd8aea3b49b4217547dec4d699b1466736cc,
    0x226c04e598076a9fa02aa64557daf28c0ec42e3d4da68d1965029d284738b07, 0x1f0e971f0485a5b42eb92d6655c3ddb475cec4371f269a95335b2a7d6dac0fb, 0x9f31cc2907dccbf994d35aa47ee3f4ebdf3703f795047a7b40dd3926431563,
    0x4b40cce78f3b641e31ce4df58ce5a42c22cfbc198c84451ffe8cca4c64bd7d2, 0x191660489e4bd8a3e4563173de4a226f3ac736962fdfb70f72cb93ce50f8b9f, 0x18c0919618db971f74eb01f293f2daea814b475103373dc7ed8dd4c7b467410,
    0x35b60253848530e845c8753121577d0ef37002e941c3dc1fb240bd57eadc803, 0x1ae99db1575ae91c8b43a9f71a5f362581ad9b413d97fa6fd029134957451d5, 0x3e6e1d0f3f8a0f728148ebcbd5d7d337d7cb8feb58a37d2d1dfb357e172647b,
    0x18bc36dffa8f96a659e1a171b55d2706ee3e9ad619e16f5c38dd1f4a209b8f3, 0x2c7a3ef1afb6a302b54afc3a107ff9199a16efe9a1cc3ab83fa5b64893de4ed, 0x53a7bd889bed07bf5e27dd8e92f6ae85e4fe4e84b0c6dde9856e94469de4bd7,
    0x4d383ff7ffc6318fda704aca35995f86bec5a02ce9a0bf9d3cc0cc2f03ccea9, 0x4667b6762fb8ad53d07ef7e8a65b21ca96e0b3503037710d1292519c326f5cd, 0x2cc8b43e75cf0b42a93c39ea98bcd46055dccc9589f02eb7fb536422e5921f,
    0x6b32ee98680871d38751447bfd76086ba4df0e7be59c55f4b2ce25582bf9c60, 0x3e907927c7182faaa3b3c81358b82e734efac1f0609f0862d635cb1387102a3, 0x3f3a5057b3a08975f0253728e512af78d2f437973f6a93793ea5e8424fbc6ea,
    0x14b491d73724779f8aa74b3fd8aa5821c21e1017224726a7a946bb6ca68d8f5, 0x5c8278c7bbfc30ae7f60e514fe3b9367aca84c54ad1373861695ea4abb814ef, 0x64851937f9836ee5a08a7dde65e44b467018a82ba3bf99bba0b4502755c8074,
    0x6a9ac84251294769eca450ffb52b441882be77cb85f422ff9ea5e73f1d971dc, 0x37ec35b710b0d04c9a2b71f2f7bd098c6a81d991d27f0fc1884f5ca545064de, 0x5334f75b052c0235119816883040da72c6d0a61538bdfff46d6a242bfeb7a1,
    0x5d0af4fcbd9e056c1020cca9d871ae68f80ee4af2ec6547cd49d6dca50aa431, 0x30131bce2fba5694114a19c46d24e00b4699dc00f1d53ba5ab99537901b1e65, 0x5646a95a7c1ae86b34c0750ed2e641c538f93f13161be3c4957660f2e788965,
    0x4b9f291d7b430c79fac36230a11f43e78581f5259692b52c90df47b7d4ec01a, 0x5006d393d3480f41a98f19127072dc83e00becf6ceb4d73d890e74abae01a13, 0x62c9d42199f3b260e7cb8a115143106acf4f702e6b346fd202dc3b26a679d80,
    0x51274d092db5099f180b1a8a13b7f2c7606836eabd8af54bf1d9ac2dc5717a5, 0x61fc552b8eb75e17ad0fb7aaa4ca528f415e14f0d9cdbed861a8db0bfff0c5b,
]


def hades(s):
    s = list(s)
    k = 0

    def mix():
        t = (s[0] + s[1] + s[2]) % P
        s[0], s[1], s[2] = (t + 2 * s[0]) % P, (t - 2 * s[1]) % P, (t - 3 * s[2]) % P
    for r in range(91):
        if r < 4 or r >= 87:
            for j in range(3):
                s[j] = pow((s[j] + RC[k + j]) % P, 3, P)
            k += 3
        else:
            s[2] = pow((s[2] + RC[k]) % P, 3, P)
            k += 1
        mix()
    return s


def blocks(xs):
    """poseidon_hash_span padding: append 1, then 0 if that left it odd."""
    v = [x % P for x in xs] + [1]
    if len(v) % 2:
        v.append(0)
    return [(v[i], v[i + 1]) for i in range(0, len(v), 2)]


def sponge(xs, every=None):
    """Hash xs; with every=k also return the state after each k blocks."""
    s, states = [0, 0, 0], []
    bl = blocks(xs)
    for i, (a, b) in enumerate(bl):
        s = hades([(s[0] + a) % P, (s[1] + b) % P, s[2]])
        if every and (i + 1) % every == 0 and i + 1 < len(bl):
            states.append(tuple(s))
    return s[0], states


def h(*xs):
    return sponge(xs)[0]


# ---- the curve (affine, None = infinity) -----------------------------------------

def add(a, b):
    if a is None:
        return b
    if b is None:
        return a
    if a[0] == b[0]:
        if (a[1] + b[1]) % P == 0:
            return None
        lam = (3 * a[0] * a[0] + 1) * pow(2 * a[1], P - 2, P) % P
    else:
        lam = (b[1] - a[1]) * pow(b[0] - a[0], P - 2, P) % P
    x = (lam * lam - a[0] - b[0]) % P
    return (x, (lam * (a[0] - x) - a[1]) % P)


def mul(k, pt):
    out = None
    while k:
        if k & 1:
            out = add(out, pt)
        pt = add(pt, pt)
        k >>= 1
    return out


def on_curve(pt):
    return pt is not None and pt[1] * pt[1] % P == (pt[0] ** 3 + pt[0] + BETA) % P


def key_id(pt):
    return h(TAG_KEY, pt[0], pt[1])


def is_square(x):
    return pow(x, (P - 1) // 2, P) == 1


def sqrt(a):
    """Tonelli-Shanks; p - 1 = 2^192 * q."""
    if a == 0:
        return 0
    if not is_square(a):
        return None
    q, s = (P - 1) >> 192, 192
    z = 3
    while is_square(z):
        z += 1
    m, c, t, r = s, pow(z, q, P), pow(a, q, P), pow(a, (q + 1) // 2, P)
    while t != 1:
        i, tt = 0, t
        while tt != 1:
            tt = tt * tt % P
            i += 1
        b = pow(c, 1 << (m - i - 1), P)
        m, c, t, r = i, b * b % P, t * b * b % P, r * b % P
    return r


def hash_to_point(scope):
    """(Hp, ctr): try-and-increment, y = the smaller root."""
    ctr = 0
    while True:
        x = h(TAG_H2P, scope, ctr)
        r = sqrt((x ** 3 + x + BETA) % P)
        if r is not None:
            return (x, min(r, P - r)), ctr
        ctr += 1


# ---- LSAG verification with every intermediate value -------------------------------

def nib(k, i):
    """Nibble i (0 = most significant) of k as a 32-byte big-endian number."""
    return (k >> (4 * (63 - i))) & 15


# Jacobian (X, Y, Z), Z = 0 is infinity: no inversion per group op
JINF = (1, 1, 0)


def jdbl(p):
    X, Y, Z = p
    if Z == 0:
        return p
    XX, YY, ZZ = X * X % P, Y * Y % P, Z * Z % P
    S = 4 * X * YY % P
    M = (3 * XX + ZZ * ZZ) % P
    X3 = (M * M - 2 * S) % P
    return (X3, (M * (S - X3) - 8 * YY * YY) % P, 2 * Y * Z % P)


def jadd_aff(p, q):
    if q is None:
        return p
    X1, Y1, Z1 = p
    if Z1 == 0:
        return (q[0], q[1], 1)
    Z1Z1 = Z1 * Z1 % P
    H = (q[0] * Z1Z1 - X1) % P
    r = (q[1] * Z1 * Z1Z1 - Y1) % P
    if H == 0:
        return jdbl(p) if r == 0 else JINF
    HH = H * H % P
    HHH = H * HH % P
    V = X1 * HH % P
    X3 = (r * r - HHH - 2 * V) % P
    return (X3, (r * (V - X3) - Y1 * HHH) % P, Z1 * H % P)


def to_aff(p):
    X, Y, Z = p
    if Z == 0:
        return None
    zi = pow(Z, P - 2, P)
    return (X * zi * zi % P, Y * zi * zi * zi % P)


def straus(a, A, b, B, every=16):
    """a*A + b*B by 4-bit windows, the way the verifiers walk it; returns
    the affine accumulator after every `every` windows (the last = result)."""
    ta, tb = [None], [None]
    for _ in range(15):
        ta.append(add(ta[-1], A))
        tb.append(add(tb[-1], B))
    acc, out = JINF, []
    for i in range(64):
        for _ in range(4):
            acc = jdbl(acc)
        acc = jadd_aff(jadd_aff(acc, ta[nib(a, i)]), tb[nib(b, i)])
        if (i + 1) % every == 0:
            out.append(to_aff(acc))
    return out


def trace(ring, scope, msg, c0, s, image, segs=4, digest_every=4):
    """Everything the Solana verifier units check, or ValueError if the
    signature does not verify. ring = sorted [(x, y)], s = [int]."""
    n = len(ring)
    hp, ctr = hash_to_point(scope)
    xs = [TAG_MSG, scope, msg, image[0], image[1], n]
    for p in ring:
        xs += list(p)
    d, states = sponge(xs, every=digest_every)
    c = [c0]
    L, R, mids = [], [], []
    w = 64 // segs
    for i in range(n):
        lw = straus(s[i], G, c[i], ring[i], w)
        rw = straus(s[i], hp, c[i], image, w)
        li, ri = lw[-1], rw[-1]
        L.append(li)
        R.append(ri)
        mids.append([lw[:-1], rw[:-1]])
        z = lambda q: q if q is not None else (0, 0)
        nxt = h(TAG_CHAL, d, *z(li), *z(ri))
        c.append(nxt)
    if c[n] != c0:
        raise ValueError('signature does not verify (challenge chain open)')
    return {'hp': hp, 'hp_ctr': ctr, 'd': d, 'sponge': states,
            'c': c[:n], 'L': L, 'R': R, 'mids': mids,
            'digest_blocks': len(blocks(xs))}
