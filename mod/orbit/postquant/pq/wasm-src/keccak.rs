//! SHAKE128/256 (FIPS 202) — incremental absorb and squeeze over
//! Keccak-f[1600]. Both verifiers sample rejection streams, so the XOF must
//! squeeze byte-at-a-time from a live state, exactly the stream hashlib
//! produces when python re-digests at a longer length.

pub const RC: [u64; 24] = [
    0x0000000000000001, 0x0000000000008082, 0x800000000000808a,
    0x8000000080008000, 0x000000000000808b, 0x0000000080000001,
    0x8000000080008081, 0x8000000000008009, 0x000000000000008a,
    0x0000000000000088, 0x0000000080008009, 0x000000008000000a,
    0x000000008000808b, 0x800000000000008b, 0x8000000000008089,
    0x8000000000008003, 0x8000000000008002, 0x8000000000000080,
    0x000000000000800a, 0x800000008000000a, 0x8000000080008081,
    0x8000000000008080, 0x0000000080000001, 0x8000000080008008,
];
const RHO: [u32; 24] = [1, 3, 6, 10, 15, 21, 28, 36, 45, 55, 2, 14,
                        27, 41, 56, 8, 25, 43, 62, 18, 39, 61, 20, 44];
const PI: [usize; 24] = [10, 7, 11, 17, 18, 3, 5, 16, 8, 21, 24, 4,
                         15, 23, 19, 13, 12, 2, 20, 14, 22, 9, 6, 1];

pub fn keccakf(a: &mut [u64; 25]) {
    let mut bc = [0u64; 5];
    for round in 0..24 {
        for i in 0..5 {
            bc[i] = a[i] ^ a[i + 5] ^ a[i + 10] ^ a[i + 15] ^ a[i + 20];
        }
        for i in 0..5 {
            let t = bc[(i + 4) % 5] ^ bc[(i + 1) % 5].rotate_left(1);
            let mut j = 0;
            while j < 25 {
                a[j + i] ^= t;
                j += 5;
            }
        }
        let mut t = a[1];
        for i in 0..24 {
            let j = PI[i];
            let tmp = a[j];
            a[j] = t.rotate_left(RHO[i]);
            t = tmp;
        }
        let mut j = 0;
        while j < 25 {
            for i in 0..5 {
                bc[i] = a[j + i];
            }
            for i in 0..5 {
                a[j + i] = bc[i] ^ ((!bc[(i + 1) % 5]) & bc[(i + 2) % 5]);
            }
            j += 5;
        }
        a[0] ^= RC[round];
    }
}

pub struct Xof {
    st: [u64; 25],
    rate: usize,
    pos: usize,
    squeezing: bool,
}

#[allow(dead_code)] // each verifier uses the subset its scheme needs
impl Xof {
    pub const fn new(rate: usize) -> Self {
        Xof { st: [0; 25], rate, pos: 0, squeezing: false }
    }

    pub const fn shake128() -> Self {
        Self::new(168)
    }

    pub const fn shake256() -> Self {
        Self::new(136)
    }

    pub fn absorb(&mut self, data: &[u8]) {
        for &b in data {
            self.st[self.pos / 8] ^= (b as u64) << (8 * (self.pos % 8));
            self.pos += 1;
            if self.pos == self.rate {
                keccakf(&mut self.st);
                self.pos = 0;
            }
        }
    }

    fn finish(&mut self) {
        self.st[self.pos / 8] ^= 0x1Fu64 << (8 * (self.pos % 8));
        self.st[(self.rate - 1) / 8] ^= 0x80u64 << (8 * ((self.rate - 1) % 8));
        keccakf(&mut self.st);
        self.pos = 0;
        self.squeezing = true;
    }

    pub fn squeeze(&mut self, out: &mut [u8]) {
        if !self.squeezing {
            self.finish();
        }
        for b in out.iter_mut() {
            if self.pos == self.rate {
                keccakf(&mut self.st);
                self.pos = 0;
            }
            *b = (self.st[self.pos / 8] >> (8 * (self.pos % 8))) as u8;
            self.pos += 1;
        }
    }

    pub fn squeeze_byte(&mut self) -> u8 {
        let mut b = [0u8; 1];
        self.squeeze(&mut b);
        b[0]
    }
}

/// SHAKE256 over concatenated parts, one shot.
pub fn shake256(parts: &[&[u8]], out: &mut [u8]) {
    let mut x = Xof::shake256();
    for p in parts {
        x.absorb(p);
    }
    x.squeeze(out);
}
