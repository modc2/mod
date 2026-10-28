//! Gas probes (feature "bench", never in the deployed build): each returns
//! the gas a primitive burns, measured in-contract with env::used_gas().
use crate::lsag;
use crate::{ZTensor, ZTensorExt};
use near_sdk::{env, near};
use starknet_types_core::felt::Felt;

#[near]
impl ZTensor {
    pub fn bench(&mut self, op: String, n: u32) -> u64 {
        let g = lsag::generator();
        let a = Felt::from_hex_unchecked("0x123456789abcdef0123456789abcdef0123456789abcdef0123456789abcde");
        let mut x = a;
        let t0 = env::used_gas().as_gas();
        match op.as_str() {
            "fmul" => { for _ in 0..n { x = x * a; } }
            "fadd" => { for _ in 0..n { x = x + a; } }
            "dbl" => { let mut p = g.clone(); for _ in 0..n { p = p.double(); } x = p.x(); }
            "add" => { let mut p = g.clone(); let q = g.double(); for _ in 0..n { p = &p + &q; } x = p.x(); }
            "smul" => { let mut p = g.clone(); for _ in 0..n { p = &p * a; } x = p.x(); }
            "h2p" => { for _ in 0..n { x = lsag::hash_to_point(&x).x; } }
            "poseidon" => { for _ in 0..n { x = lsag::h(&[x, a, x, a, x, a]); } }
            "inv" => { for _ in 0..n { x = x.inverse().unwrap(); } }
            "fpmul" => { let fa = crate::fp::Fp::from_felt(&a); let mut y = fa; for _ in 0..n { y = y.mul(&fa); } x = y.to_felt(); }
            "fdbl" => { let ga = crate::fp::Aff { x: crate::fp::Fp::from_felt(&g.x()), y: crate::fp::Fp::from_felt(&g.y()), inf: false }; let mut j = crate::fp::Jac::from_aff(&ga); for _ in 0..n { j = j.double(); } x = j.x.to_felt(); }
            "fmadd" => { let ga = crate::fp::Aff { x: crate::fp::Fp::from_felt(&g.x()), y: crate::fp::Fp::from_felt(&g.y()), inf: false }; let mut j = crate::fp::Jac::from_aff(&ga).double(); for _ in 0..n { j = j.add_aff(&ga); } x = j.x.to_felt(); }
            "table" => { let ga = crate::fp::Aff { x: crate::fp::Fp::from_felt(&g.x()), y: crate::fp::Fp::from_felt(&g.y()), inf: false }; for _ in 0..n { let t = crate::fp::table(&ga); x = t[3].x.to_felt(); } }
            "mul2" => { let ga = crate::fp::Aff { x: crate::fp::Fp::from_felt(&g.x()), y: crate::fp::Fp::from_felt(&g.y()), inf: false }; let t = crate::fp::table(&ga); let nb = crate::fp::nibbles(&a); for _ in 0..n { let j = crate::fp::mul2(&nb, &t, &nb, &t); x = j.x.to_felt(); } }
            "h2phint" => { let p = lsag::hash_to_point(&a); let t1 = env::used_gas().as_gas(); let mut y = x; for _ in 0..n { y = lsag::hash_to_point_hinted(&a, Some(&p)).x; } if y == Felt::ZERO { env::log_str("z"); } return (env::used_gas().as_gas() - t1) / n as u64; }
            _ => {}
        }
        let used = env::used_gas().as_gas() - t0;
        if x == Felt::ZERO { env::log_str("z"); }
        used / n.max(1) as u64
    }
}
