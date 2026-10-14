//! zuma prover — the off-chain half, run with `scarb cairo-run '[[op, ...]]'`.
//! It links the SAME `zuma::lsag` + scope/seal fns the contract verifies
//! with, so signer and verifier cannot drift. Results print as `Z <felt>`
//! lines (scarb 2.8 prints Array returns from main as memory pointers).
//!
//!   1 keygen  [1, secret]                                 -> x, y, key_id
//!   2 sign    [2, scope, msg, secret, alpha, n, x0, y0, .., f0..f(n-1)]
//!                                                         -> n, ring (sorted), c0, s.., Ix, Iy, id
//!   4 image   [4, secret, scope]                          -> Ix, Iy, id (tag / nullifier)
//!   5 scope   [5, kind (1 mine | 2 vote), contract, epoch] -> scope
//!   6 seal    [6, contract, epoch, nullifier, salt, n, w0..] -> seal

use zuma::lsag;
use zuma::lsag::Point;

fn out(v: felt252) {
    println!("Z {}", v);
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
        let mut raw: Array<Point> = array![];
        for k in 0..n {
            raw.append(Point { x: *a.at(6 + 2 * k), y: *a.at(7 + 2 * k) });
        };
        let ring = lsag::sort_ring(raw.span()).span();
        let fill = a.slice(6 + 2 * n, n);
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
    } else if op == 4 {
        let img = lsag::key_image(*a.at(1), *a.at(2));
        out(img.x);
        out(img.y);
        out(lsag::key_id(img));
    } else if op == 5 {
        let contract: starknet::ContractAddress = (*a.at(2)).try_into().unwrap();
        let epoch: u64 = (*a.at(3)).try_into().unwrap();
        if *a.at(1) == 1 {
            out(zuma::mine_scope_of(contract, epoch));
        } else {
            out(zuma::vote_scope_of(contract, epoch));
        }
    } else if op == 6 {
        let contract: starknet::ContractAddress = (*a.at(1)).try_into().unwrap();
        let epoch: u64 = (*a.at(2)).try_into().unwrap();
        let n: usize = (*a.at(5)).try_into().unwrap();
        let mut w: Array<u16> = array![];
        for k in 0..n {
            w.append((*a.at(6 + k)).try_into().unwrap());
        };
        out(zuma::seal_of(contract, epoch, *a.at(3), w.span(), *a.at(4)));
    } else {
        panic!("unknown op");
    }
}
