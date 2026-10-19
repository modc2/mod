//! openhouse_proof — proof of rent paid, without the tenant.
//!
//! The bank is the only party that sees a tenant's payment: who paid, how
//! much, from which account. This program is the STATEMENT it proves about
//! that payment. Run with `scarb cairo-run '[[op, ...]]'`; every result is
//! printed as a `P <felt>` line (Array returns print memory pointers under
//! scarb 2.8, so we print instead).
//!
//!   1 prove      [1, property_id, period, rent_due, tenant, salt, amount_paid]
//!                -> VERSION, property_id, period, rent_due, commitment, nullifier
//!                   (panics unless amount_paid >= rent_due — an under-payment
//!                   has no proof, it is not a proof of "less")
//!   2 commit     [2, tenant, salt]                      -> commitment
//!   3 nullifier  [3, commitment, property_id, period]   -> nullifier
//!   4 version    [4]                                    -> VERSION
//!
//! PRIVATE inputs: tenant, salt, amount_paid. PUBLIC outputs: everything the
//! chain needs and nothing about the person — the commitment is Poseidon over
//! (tenant, salt) so it opens only to someone holding the salt, and the
//! nullifier binds that commitment to one property and one period so the
//! same proof can never be booked twice.
//!
//! On-chain verifiers (near/ and contracts/OpenHouseProofs.sol) never run
//! this code. They check a FACT — keccak256(program_hash ‖ keccak256(outputs))
//! — carried either by the bank's signature (today) or by a STARK fact
//! registry (same fact, same outputs, when a prover is plugged in).

use core::poseidon::poseidon_hash_span;

/// Bump when the statement changes: on-chain verifiers pin output[0].
const VERSION: felt252 = 1;

fn out(v: felt252) {
    println!("P {}", v);
}

/// commitment = Poseidon('OH_COMMIT', tenant, salt)
pub fn commitment(tenant: felt252, salt: felt252) -> felt252 {
    poseidon_hash_span(array!['OH_COMMIT', tenant, salt].span())
}

/// nullifier = Poseidon('OH_NULL', commitment, property_id, period)
pub fn nullifier(commitment: felt252, property_id: felt252, period: felt252) -> felt252 {
    poseidon_hash_span(array!['OH_NULL', commitment, property_id, period].span())
}

/// The statement. Returns the public outputs, in on-chain order.
pub fn prove(
    property_id: felt252, period: felt252, rent_due: felt252,
    tenant: felt252, salt: felt252, amount_paid: felt252,
) -> (felt252, felt252, felt252, felt252, felt252, felt252) {
    let due: u128 = rent_due.try_into().expect('rent_due must fit u128');
    let paid: u128 = amount_paid.try_into().expect('amount must fit u128');
    assert(paid >= due, 'paid less than rent due');
    assert(tenant != 0, 'tenant required');
    assert(salt != 0, 'salt required');
    let _pid: u64 = property_id.try_into().expect('property_id must fit u64');
    let _per: u64 = period.try_into().expect('period must fit u64');
    let c = commitment(tenant, salt);
    let n = nullifier(c, property_id, period);
    (VERSION, property_id, period, rent_due, c, n)
}

fn main(args: Array<felt252>) {
    let a = args.span();
    let op = *a.at(0);
    if op == 1 {
        let (v, pid, per, due, c, n) = prove(
            *a.at(1), *a.at(2), *a.at(3), *a.at(4), *a.at(5), *a.at(6));
        out(v); out(pid); out(per); out(due); out(c); out(n);
    } else if op == 2 {
        out(commitment(*a.at(1), *a.at(2)));
    } else if op == 3 {
        out(nullifier(*a.at(1), *a.at(2), *a.at(3)));
    } else if op == 4 {
        out(VERSION);
    } else {
        panic!("unknown op");
    }
}

#[cfg(test)]
mod tests {
    use super::{commitment, nullifier, prove};

    #[test]
    fn outputs_are_deterministic_and_hide_the_tenant() {
        let (v, pid, per, due, c, n) = prove(1, 202610, 1_500_000_000, 0xabc, 0x5a17, 1_500_000_000);
        assert(v == 1, 'version');
        assert(pid == 1 && per == 202610 && due == 1_500_000_000, 'public terms pass through');
        assert(c == commitment(0xabc, 0x5a17), 'commitment');
        assert(n == nullifier(c, 1, 202610), 'nullifier');
        // a different salt => a different commitment, same tenant
        assert(commitment(0xabc, 0x5a18) != c, 'salt matters');
        // same tenant+salt, next month => a different nullifier
        assert(nullifier(c, 1, 202611) != n, 'period matters');
    }

    #[test]
    fn overpaying_is_fine() {
        let (_, _, _, due, _, _) = prove(7, 202601, 100, 0x1, 0x2, 250);
        assert(due == 100, 'rent_due stays the public term');
    }

    #[test]
    #[should_panic(expected: ('paid less than rent due',))]
    fn underpaying_has_no_proof() {
        prove(7, 202601, 100, 0x1, 0x2, 99);
    }

    #[test]
    #[should_panic(expected: ('salt required',))]
    fn zero_salt_refused() {
        prove(7, 202601, 100, 0x1, 0, 100);
    }
}
