//! SLH-DSA (FIPS 205, SHAKE) signature verification, compiled to wasm.
//!
//! A port of the verify path of pq/slhdsa.py: FORS pk-from-sig, then the
//! hypertree of XMSS/WOTS+ pk-from-sig walks, everything SHAKE256. This is
//! the chain's hash-based hedge — nothing here but Keccak and bytes, so it
//! stands even if lattices fall. One source, one blob per parameter set,
//! chosen at compile time: a key type IS a blob on this chain.
//!
//! Build (see build.sh): rustc --cfg slh_128f … -o ../wasm/slhdsa128f.wasm slhdsa.rs

#![no_std]

#[path = "keccak.rs"]
mod keccak;
#[path = "abi.rs"]
mod abi;

use keccak::shake256;

#[cfg(slh_128f)]
mod params {
    pub const NB: usize = 16; // n — hash output bytes
    pub const H: u32 = 66; // total tree height
    pub const DL: usize = 22; // layers
    pub const HP: u32 = 3; // height per layer (h')
    pub const A: usize = 6; // FORS tree height
    pub const KF: usize = 33; // FORS trees
}
#[cfg(slh_192f)]
mod params {
    pub const NB: usize = 24;
    pub const H: u32 = 66;
    pub const DL: usize = 22;
    pub const HP: u32 = 3;
    pub const A: usize = 8;
    pub const KF: usize = 33;
}
#[cfg(slh_256f)]
mod params {
    pub const NB: usize = 32;
    pub const H: u32 = 68;
    pub const DL: usize = 17;
    pub const HP: u32 = 4;
    pub const A: usize = 9;
    pub const KF: usize = 35;
}
#[cfg(not(any(slh_128f, slh_192f, slh_256f)))]
compile_error!("pick a parameter set: --cfg slh_128f | slh_192f | slh_256f");

use params::{A, DL, H, HP, KF, NB};

const W: u32 = 16; // Winternitz, lg_w = 4 in every SHAKE-f set
const LEN1: usize = 2 * NB; // 8n / lg_w
const LEN2: usize = 3; // the checksum digits, 3 for every set here
const LEN: usize = LEN1 + LEN2; // WOTS chains
const M: usize = (KF * A + 7) / 8 + (H as usize - H as usize / DL + 7) / 8
    + (H as usize / DL + 7) / 8; // H_msg output bytes
const PK_BYTES: usize = 2 * NB;
const SIG_BYTES: usize = NB * (1 + KF * (1 + A) + H as usize + DL * LEN);
const FORS_BYTES: usize = KF * (1 + A) * NB;
const XMSS_BYTES: usize = (LEN + HP as usize) * NB; // one layer's signature
const TREE_BITS: u32 = H - H / DL as u32; // 63 or 64
const LEAF_BITS: u32 = H / DL as u32;

// ADRS types (FIPS 205 section 4.2)
const WOTS_HASH: u32 = 0;
const WOTS_PK: u32 = 1;
const TREE: u32 = 2;
const FORS_TREE: u32 = 3;
const FORS_ROOTS: u32 = 4;

#[derive(Clone, Copy)]
struct Adrs {
    b: [u8; 32],
}

impl Adrs {
    fn new() -> Self {
        Adrs { b: [0; 32] }
    }
    fn set32(&mut self, off: usize, v: u32) {
        self.b[off..off + 4].copy_from_slice(&v.to_be_bytes());
    }
    fn set_layer(&mut self, x: u32) {
        self.set32(0, x);
    }
    fn set_tree(&mut self, x: u64) {
        // 12 bytes big-endian; the top 4 stay zero for a u64 index
        self.b[4..8].copy_from_slice(&[0; 4]);
        self.b[8..16].copy_from_slice(&x.to_be_bytes());
    }
    fn set_type_and_clear(&mut self, t: u32) {
        self.set32(16, t);
        self.b[20..32].copy_from_slice(&[0; 12]);
    }
    fn set_key_pair(&mut self, x: u32) {
        self.set32(20, x);
    }
    fn get_key_pair(&self) -> u32 {
        u32::from_be_bytes([self.b[20], self.b[21], self.b[22], self.b[23]])
    }
    fn set_chain(&mut self, x: u32) {
        self.set32(24, x);
    }
    fn set_hash(&mut self, x: u32) {
        self.set32(28, x);
    }
    fn set_tree_height(&mut self, x: u32) {
        self.set_chain(x);
    }
    fn set_tree_index(&mut self, x: u32) {
        self.set_hash(x);
    }
    fn get_tree_index(&self) -> u32 {
        u32::from_be_bytes([self.b[28], self.b[29], self.b[30], self.b[31]])
    }
}

type Node = [u8; NB];

fn f(pk_seed: &[u8], adrs: &Adrs, m1: &[u8]) -> Node {
    let mut out = [0u8; NB];
    shake256(&[pk_seed, &adrs.b, m1], &mut out);
    out
}

/// Algorithm 4 — the first out.len() base-2^b digits of x, big-endian.
fn base_2b(x: &[u8], b: usize, out: &mut [u32]) {
    let mut i = 0usize;
    let mut bits = 0usize;
    let mut total: u64 = 0;
    for o in out.iter_mut() {
        while bits < b {
            total = (total << 8) | x[i] as u64;
            i += 1;
            bits += 8;
        }
        bits -= b;
        *o = ((total >> bits) & ((1u64 << b) - 1)) as u32;
    }
}

/// Algorithms 7/8's shared prologue: len1 message digits + len2 checksum.
fn wots_digits(mdig: &Node) -> [u32; LEN] {
    let mut out = [0u32; LEN];
    base_2b(mdig, 4, &mut out[..LEN1]);
    let mut csum: u32 = 0;
    for i in 0..LEN1 {
        csum += W - 1 - out[i];
    }
    csum <<= 4; // (8 - (len2 * lg_w % 8)) % 8
    let cb = [(csum >> 8) as u8, (csum & 0xFF) as u8];
    let mut tail = [0u32; LEN2];
    base_2b(&cb, 4, &mut tail);
    out[LEN1..].copy_from_slice(&tail);
    out
}

/// Algorithm 5 — s applications of F starting at position i.
fn chain(mut x: Node, i: u32, s: u32, pk_seed: &[u8], adrs: &mut Adrs) -> Node {
    for j in i..i + s {
        adrs.set_hash(j);
        x = f(pk_seed, adrs, &x);
    }
    x
}

/// Algorithm 8 — WOTS+ public key from a signature.
fn wots_pk_from_sig(sig: &[u8], mdig: &Node, pk_seed: &[u8],
                    adrs: &mut Adrs) -> Node {
    let digits = wots_digits(mdig);
    let mut tmp = [0u8; LEN * NB];
    for i in 0..LEN {
        adrs.set_chain(i as u32);
        let mut x = [0u8; NB];
        x.copy_from_slice(&sig[i * NB..(i + 1) * NB]);
        let node = chain(x, digits[i], W - 1 - digits[i], pk_seed, adrs);
        tmp[i * NB..(i + 1) * NB].copy_from_slice(&node);
    }
    let mut pk_adrs = *adrs;
    pk_adrs.set_type_and_clear(WOTS_PK);
    pk_adrs.set_key_pair(adrs.get_key_pair());
    f(pk_seed, &pk_adrs, &tmp)
}

/// Algorithm 11 — XMSS public key (tree root) from one layer's signature.
fn xmss_pk_from_sig(idx: u32, sig_xmss: &[u8], mdig: &Node, pk_seed: &[u8],
                    adrs: &mut Adrs) -> Node {
    const WOTS_BYTES: usize = LEN * NB;
    adrs.set_type_and_clear(WOTS_HASH);
    adrs.set_key_pair(idx);
    let mut node = wots_pk_from_sig(&sig_xmss[..WOTS_BYTES], mdig, pk_seed, adrs);
    let auth = &sig_xmss[WOTS_BYTES..];
    adrs.set_type_and_clear(TREE);
    adrs.set_tree_index(idx);
    for k in 0..HP {
        adrs.set_tree_height(k + 1);
        let sibling = &auth[(k as usize) * NB..(k as usize + 1) * NB];
        let mut cat = [0u8; 2 * NB];
        if (idx >> k) & 1 == 0 {
            adrs.set_tree_index(adrs.get_tree_index() / 2);
            cat[..NB].copy_from_slice(&node);
            cat[NB..].copy_from_slice(sibling);
        } else {
            adrs.set_tree_index((adrs.get_tree_index() - 1) / 2);
            cat[..NB].copy_from_slice(sibling);
            cat[NB..].copy_from_slice(&node);
        }
        node = f(pk_seed, adrs, &cat);
    }
    node
}

/// Algorithm 13 — climb the hypertree and compare against the root.
fn ht_verify(mdig: &Node, sig_ht: &[u8], pk_seed: &[u8], mut idx_tree: u64,
             mut idx_leaf: u32, pk_root: &[u8]) -> bool {
    let mut adrs = Adrs::new();
    adrs.set_tree(idx_tree);
    let mut node = xmss_pk_from_sig(idx_leaf, &sig_ht[..XMSS_BYTES], mdig,
                                    pk_seed, &mut adrs);
    for j in 1..DL {
        idx_leaf = (idx_tree & ((1 << HP) - 1)) as u32;
        idx_tree >>= HP;
        let mut adrs = Adrs::new();
        adrs.set_layer(j as u32);
        adrs.set_tree(idx_tree);
        node = xmss_pk_from_sig(idx_leaf,
                                &sig_ht[j * XMSS_BYTES..(j + 1) * XMSS_BYTES],
                                &node, pk_seed, &mut adrs);
    }
    node == pk_root[..NB]
}

/// Algorithm 17 — FORS public key from a signature.
fn fors_pk_from_sig(sig: &[u8], md: &[u8], pk_seed: &[u8],
                    adrs: &mut Adrs) -> Node {
    const STEP: usize = (1 + A) * NB;
    let mut indices = [0u32; KF];
    base_2b(md, A, &mut indices);
    let mut roots = [0u8; KF * NB];
    for i in 0..KF {
        let idx = indices[i];
        let chunk = &sig[i * STEP..(i + 1) * STEP];
        let sk = &chunk[..NB];
        let auth = &chunk[NB..];
        adrs.set_tree_height(0);
        adrs.set_tree_index(((i as u32) << A) + idx);
        let mut node = f(pk_seed, adrs, sk);
        for j in 0..A {
            adrs.set_tree_height(j as u32 + 1);
            let sibling = &auth[j * NB..(j + 1) * NB];
            let mut cat = [0u8; 2 * NB];
            if (idx >> j) & 1 == 0 {
                adrs.set_tree_index(adrs.get_tree_index() / 2);
                cat[..NB].copy_from_slice(&node);
                cat[NB..].copy_from_slice(sibling);
            } else {
                adrs.set_tree_index((adrs.get_tree_index() - 1) / 2);
                cat[..NB].copy_from_slice(sibling);
                cat[NB..].copy_from_slice(&node);
            }
            node = f(pk_seed, adrs, &cat);
        }
        roots[i * NB..(i + 1) * NB].copy_from_slice(&node);
    }
    let mut pk_adrs = *adrs;
    pk_adrs.set_type_and_clear(FORS_ROOTS);
    pk_adrs.set_key_pair(adrs.get_key_pair());
    f(pk_seed, &pk_adrs, &roots)
}

/// Algorithm 20, with the FIPS m' wrapper absorbed in parts.
fn verify(pk: &[u8], msg: &[u8], sig: &[u8], ctx: &[u8]) -> bool {
    if pk.len() != PK_BYTES || sig.len() != SIG_BYTES || ctx.len() > 255 {
        return false;
    }
    let pk_seed = &pk[..NB];
    let pk_root = &pk[NB..];
    let r = &sig[..NB];
    let sig_fors = &sig[NB..NB + FORS_BYTES];
    let sig_ht = &sig[NB + FORS_BYTES..];

    // digest = H_msg(r, pk_seed, pk_root, m'), m' = 0x00 || len || ctx || msg
    let mut digest = [0u8; M];
    shake256(&[r, pk_seed, pk_root, &[0u8, ctx.len() as u8], ctx, msg],
             &mut digest);

    // section 9.2 slicing: md(25) || idx_tree(8) || idx_leaf(1)
    const MD_LEN: usize = (KF * A + 7) / 8; // 25
    const TREE_LEN: usize = ((H - H / DL as u32) as usize + 7) / 8; // 8
    let md = &digest[..MD_LEN];
    let mut tb = [0u8; 8];
    tb.copy_from_slice(&digest[MD_LEN..MD_LEN + TREE_LEN]);
    let idx_tree = u64::from_be_bytes(tb) % (1u64 << (H - H / DL as u32));
    let idx_leaf = (digest[MD_LEN + TREE_LEN] % (1 << (H / DL as u32))) as u32;

    let mut adrs = Adrs::new();
    adrs.set_tree(idx_tree);
    adrs.set_type_and_clear(FORS_TREE);
    adrs.set_key_pair(idx_leaf);
    let pk_fors = fors_pk_from_sig(sig_fors, md, pk_seed, &mut adrs);
    ht_verify(&pk_fors, sig_ht, pk_seed, idx_tree, idx_leaf, pk_root)
}

#[no_mangle]
pub extern "C" fn pq_verify(pk: *const u8, pk_len: usize,
                            msg: *const u8, msg_len: usize,
                            sig: *const u8, sig_len: usize,
                            ctx: *const u8, ctx_len: usize) -> i32 {
    let (pk, msg, sig, ctx) = unsafe {
        (abi::input(pk, pk_len), abi::input(msg, msg_len),
         abi::input(sig, sig_len), abi::input(ctx, ctx_len))
    };
    verify(pk, msg, sig, ctx) as i32
}
