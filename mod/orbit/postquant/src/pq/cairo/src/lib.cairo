// CAIRO-MSS-SHA256 verifier — the Cairo twin of src/pq/cairomss.py.
//
// Input: one Array<u32> = pk (16 words: pub_seed | root)
//                       | mhash (8 words: SHA-256 of the ctx-framed message)
//                       | sig (2344 words: R | 4 x (67 chains | 6 auth nodes))
// Output: 1 if the signature verifies, 0 otherwise. Every hash input is
// whole 32-bit words, so core::sha256 runs it with no byte handling.
// Cairo programs are what STARKs prove: this file is the statement a
// hash-based proof of "this witness is valid" would be about.

use core::sha256::compute_sha256_u32_array;

const NW: usize = 8;      // 32-byte hash = 8 words
const W: u32 = 16;
const LEN: usize = 67;
const H: usize = 6;
const D: usize = 4;
const PK_W: usize = 16;
const SIG_W: usize = 2344;   // 9376 bytes

fn push_span(ref out: Array<u32>, s: Span<u32>) {
    let mut i = 0;
    while i < s.len() {
        out.append(*s.at(i));
        i += 1;
    };
}

fn sha(input: Array<u32>) -> Span<u32> {
    let [a, b, c, d, e, f, g, h] = compute_sha256_u32_array(input, 0, 0);
    array![a, b, c, d, e, f, g, h].span()
}

fn adrs(ref out: Array<u32>, layer: u32, tree: u32, typ: u32, kp: u32, chain: u32, hsh: u32) {
    out.append(layer);
    out.append(0);
    out.append(tree);
    out.append(typ);
    out.append(kp);
    out.append(chain);
    out.append(hsh);
    out.append(0);
}

// T(adrs, a | b) = SHA-256(pub_seed | adrs | a | b)
fn t(ps: Span<u32>, layer: u32, tree: u32, typ: u32, kp: u32, chain: u32, hsh: u32,
     a: Span<u32>, b: Span<u32>) -> Span<u32> {
    let mut buf: Array<u32> = array![];
    push_span(ref buf, ps);
    adrs(ref buf, layer, tree, typ, kp, chain, hsh);
    push_span(ref buf, a);
    push_span(ref buf, b);
    sha(buf)
}

fn pow2(e: usize) -> u32 {
    let mut r: u32 = 1;
    let mut i = 0;
    while i < e {
        r *= 2;
        i += 1;
    };
    r
}

// 64 message nibbles (big-endian within each word) + 3 checksum nibbles
fn base_w(m: Span<u32>) -> Array<u32> {
    let mut out: Array<u32> = array![];
    let mut csum: u32 = 0;
    let mut i = 0;
    while i < NW {
        let w = *m.at(i);
        let mut k: u32 = 8;
        while k > 0 {
            k -= 1;
            let v = (w / pow2((k * 4).try_into().unwrap())) % W;
            out.append(v);
            csum += W - 1 - v;
        };
        i += 1;
    };
    out.append((csum / 256) % W);
    out.append((csum / 16) % W);
    out.append(csum % W);
    out
}

pub fn verify(input: Span<u32>) -> bool {
    if input.len() != PK_W + NW + SIG_W {
        return false;
    }
    let ps = input.slice(0, NW);
    let root = input.slice(NW, NW);
    let pk = input.slice(0, PK_W);
    let mhash = input.slice(PK_W, NW);
    let sig = input.slice(PK_W + NW, SIG_W);
    let r = sig.slice(0, NW);

    // dg = SHA-256(R | pk | mhash); leaf index = its first 3 bytes
    let mut buf: Array<u32> = array![];
    push_span(ref buf, r);
    push_span(ref buf, pk);
    push_span(ref buf, mhash);
    let dg = sha(buf);
    let idx: u32 = *dg.at(0) / 256;

    let mut m = dg;
    let mut off: usize = NW;
    let mut layer: usize = 0;
    while layer < D {
        let lu: u32 = layer.try_into().unwrap();
        let tree = idx / pow2(H * (layer + 1));
        let leaf = (idx / pow2(H * layer)) % pow2(H);
        let digits = base_w(m);
        // finish every chain from the signed position to the top
        let mut ends: Array<u32> = array![];
        let mut i: usize = 0;
        while i < LEN {
            let v = *digits.at(i);
            let mut x = sig.slice(off + i * NW, NW);
            let mut j = v;
            while j < W - 1 {
                x = t(ps, lu, tree, 0, leaf, i.try_into().unwrap(), j, x, array![].span());
                j += 1;
            };
            push_span(ref ends, x);
            i += 1;
        };
        off += LEN * NW;
        let mut node = t(ps, lu, tree, 1, leaf, 0, 0, ends.span(), array![].span());
        // climb the auth path
        let mut z: usize = 0;
        while z < H {
            let sib = sig.slice(off + z * NW, NW);
            let i_up = leaf / pow2(z + 1);
            let zu: u32 = (z + 1).try_into().unwrap();
            if (leaf / pow2(z)) % 2 == 1 {
                node = t(ps, lu, tree, 2, 0, zu, i_up, sib, node);
            } else {
                node = t(ps, lu, tree, 2, 0, zu, i_up, node, sib);
            }
            z += 1;
        };
        off += H * NW;
        m = node;
        layer += 1;
    };
    m == root
}

fn main(input: Array<u32>) -> felt252 {
    if verify(input.span()) {
        1
    } else {
        0
    }
}
