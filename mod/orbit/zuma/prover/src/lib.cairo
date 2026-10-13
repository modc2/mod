//! zuma prover — the off-chain half of the protocol, run with
//! `scarb cairo-run '[[op, ...]]'`. It links the SAME `zuma::lsag` module
//! the contract verifies with, so signer and verifier cannot drift.
//! Every result is printed as `Z <felt>` lines (Array returns from main
//! print memory pointers under scarb 2.8, so we print instead).
//!
//!   1 keygen  [1, secret]                          -> x, y, key_id
//!   2 sign    [2, scope, msg, secret, alpha, n, x0, y0, .., f0..f(n-1)]
//!                                                  -> n, ring (sorted), c0, s.., Ix, Iy, tag
//!   3 verify  [3, scope, msg, n, x0, y0, .., c0, s.., Ix, Iy] -> 1 | 0
//!   4 image   [4, secret, scope]                   -> Ix, Iy, tag
//!   5 scope   [5, kind(1 mine | 2 score), subnet, epoch, tag] -> scope

use core::poseidon::poseidon_hash_span;
use zuma::lsag;
use zuma::lsag::{Point, Signature};

fn out(v: felt252) {
    println!("Z {}", v);
}

fn read_ring(args: Span<felt252>, ref i: usize, n: usize) -> Array<Point> {
    let mut ring = array![];
    let mut k = 0;
    while k < n {
        ring.append(Point { x: *args.at(i), y: *args.at(i + 1) });
        i += 2;
        k += 1;
    };
    ring
}

fn main(args: Array<felt252>) {
    let a = args.span();
    let op = *a.at(0);
    if op == 1 {
        let p = lsag::public_key(*a.at(1));
        out(p.x);
        out(p.y);
        out(lsag::key_id(p));
    } else if op == 2 {
        let (scope, msg, secret, alpha) = (*a.at(1), *a.at(2), *a.at(3), *a.at(4));
        let n: usize = (*a.at(5)).try_into().unwrap();
        let mut i: usize = 6;
        let ring = lsag::sort_ring(read_ring(a, ref i, n).span()).span();
        let fill = a.slice(i, n);
        let me = lsag::public_key(secret);
        let mut idx: usize = 0;
        while idx < n && *ring.at(idx) != me {
            idx += 1;
        };
        assert(idx < n, 'your key is not in the ring');
        let sig = lsag::sign(ring, idx, secret, scope, msg, alpha, fill);
        assert(lsag::verify(ring, scope, msg, sig), 'self-check failed');
        out(n.into());
        for p in ring {
            out(*p.x);
            out(*p.y);
        };
        out(sig.c0);
        for s in sig.s {
            out(*s);
        };
        out(sig.image.x);
        out(sig.image.y);
        out(lsag::key_id(sig.image));
    } else if op == 3 {
        let (scope, msg) = (*a.at(1), *a.at(2));
        let n: usize = (*a.at(3)).try_into().unwrap();
        let mut i: usize = 4;
        let ring = read_ring(a, ref i, n);
        let c0 = *a.at(i);
        let s = a.slice(i + 1, n);
        let image = Point { x: *a.at(i + 1 + n), y: *a.at(i + 2 + n) };
        let ok = lsag::verify(ring.span(), scope, msg, Signature { c0, s, image });
        out(if ok { 1 } else { 0 });
    } else if op == 4 {
        let img = lsag::key_image(*a.at(1), *a.at(2));
        out(img.x);
        out(img.y);
        out(lsag::key_id(img));
    } else if op == 5 {
        let (kind, subnet, epoch, tag) = (*a.at(1), *a.at(2), *a.at(3), *a.at(4));
        if kind == 1 {
            out(poseidon_hash_span(array!['zuma-mine', subnet, epoch].span()));
        } else {
            out(poseidon_hash_span(array!['zuma-score', subnet, epoch, tag].span()));
        }
    } else {
        panic!("unknown op");
    }
}
