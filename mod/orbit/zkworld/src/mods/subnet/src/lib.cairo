// Subnet — Yuma consensus with sealed (commit-reveal) weights.
//
// Each epoch runs three phases:
//
//   1. COMMIT  validators post seal = H('yuma', contract, epoch, who, salt, w_0..w_n).
//              Nobody, not even the sequencer, can see anyone's weights, so a
//              lazy validator cannot copy the honest ones (weight copying).
//   2. REVEAL  once every validator has sealed (or the commit window ends) each
//              one opens their seal. Weights are fixed: a late revealer can read
//              earlier reveals but cannot change their own.
//   3. FINALIZE once everyone has revealed (or the reveal window ends), anyone
//              runs Yuma over the revealed weights and the epoch rolls.
//
// Yuma (fixed point, ONE = 1e9), over validators who revealed:
//   w_ij   = validator i's weights normalized to sum ONE
//   s_i    = i's share of revealed stake
//   c_j    = stake-weighted median of w_.j (kappa = 1/2): largest c such that
//            validators holding >= half the stake gave miner j at least c
//   wc_ij  = min(w_ij, c_j)                     clip weight above consensus
//   I_j    = sum_i s_i wc_ij, normalized        miner incentive
//   B_ij   = EMA(alpha, s_i wc_ij / sum_i s_i wc_ij)   bonds, kept across epochs
//   D_i    = sum_j B_ij I_j, normalized         validator dividends
// Emission splits half to miners by I, half to validators by D.
//
// Privacy scope: weights are hidden while they could still be copied, then
// published (same guarantee as Bittensor's commit-reveal). Validators who seal
// but never reveal simply drop out of that epoch. Stake is paper: the owner
// sets it, no token moves.

use core::poseidon::poseidon_hash_span;
use starknet::ContractAddress;

pub const ONE: u128 = 1_000_000_000;
pub const MAX_VALIDATORS: u32 = 16;
pub const MAX_MINERS: u32 = 32;
// bond EMA: new bonds weigh 10%, history 90%
pub const BOND_ALPHA: u128 = 100_000_000;

pub const PHASE_COMMIT: u8 = 0;
pub const PHASE_REVEAL: u8 = 1;
pub const PHASE_FINALIZE: u8 = 2;

/// The seal a validator commits for `weights` hidden under `salt`. Bound to the
/// contract, epoch and validator so a seal can be neither replayed next epoch
/// nor copied by another validator.
pub fn seal_of(
    contract: ContractAddress, epoch: u64, who: ContractAddress, weights: Span<u16>, salt: felt252,
) -> felt252 {
    let mut data: Array<felt252> = array!['yuma', contract.into(), epoch.into(), who.into(), salt];
    for w in weights {
        data.append((*w).into());
    };
    poseidon_hash_span(data.span())
}

#[starknet::interface]
pub trait ISubnet<TContractState> {
    // owner
    fn set_validator(ref self: TContractState, who: ContractAddress, stake: u128);
    // validators
    fn commit(ref self: TContractState, seal: felt252);
    fn reveal(ref self: TContractState, weights: Span<u16>, salt: felt252);
    // anyone
    fn finalize(ref self: TContractState);
    // reads
    fn seal(self: @TContractState, who: ContractAddress, weights: Span<u16>, salt: felt252) -> felt252;
    fn phase(self: @TContractState) -> u8;
    fn epoch(self: @TContractState) -> u64;
    fn miners(self: @TContractState) -> u32;
    fn validators(self: @TContractState) -> Array<ContractAddress>;
    fn stake_of(self: @TContractState, who: ContractAddress) -> u128;
    fn sealed(self: @TContractState) -> u32;
    fn revealed(self: @TContractState) -> u32;
    fn has_sealed(self: @TContractState, who: ContractAddress) -> bool;
    fn weights_of(self: @TContractState, epoch: u64, who: ContractAddress) -> Array<u16>;
    fn consensus(self: @TContractState, epoch: u64) -> Array<u128>;
    fn incentive(self: @TContractState, epoch: u64) -> Array<u128>;
    fn dividends(self: @TContractState, epoch: u64) -> Array<u128>;
    fn miner_earned(self: @TContractState, miner: u32) -> u128;
    fn validator_earned(self: @TContractState, who: ContractAddress) -> u128;
}

#[starknet::contract]
pub mod Subnet {
    use core::num::traits::Zero;
    use starknet::{ContractAddress, get_block_timestamp, get_caller_address, get_contract_address};
    use starknet::storage::{
        Map, StorageMapReadAccess, StorageMapWriteAccess, StoragePointerReadAccess,
        StoragePointerWriteAccess,
    };
    use super::{
        BOND_ALPHA, MAX_MINERS, MAX_VALIDATORS, ONE, PHASE_COMMIT, PHASE_FINALIZE, PHASE_REVEAL,
        seal_of,
    };

    #[storage]
    struct Storage {
        owner: ContractAddress,
        n_miners: u32,
        commit_secs: u64,
        reveal_secs: u64,
        emission: u128,
        // validator set: index -> addr, addr -> index + 1 (0 = not a validator)
        n_vals: u32,
        val_addr: Map<u32, ContractAddress>,
        val_slot: Map<ContractAddress, u32>,
        stake: Map<u32, u128>,
        // the running epoch
        epoch: u64,
        epoch_start: u64,
        n_sealed: u32,
        n_revealed: u32,
        seals: Map<(u64, u32), felt252>,
        opened: Map<(u64, u32), bool>,
        weight: Map<(u64, u32, u32), u16>,
        // persistent bonds (validator, miner)
        bond: Map<(u32, u32), u128>,
        // results per epoch
        cons: Map<(u64, u32), u128>,
        incent: Map<(u64, u32), u128>,
        divs: Map<(u64, u32), u128>,
        miner_paid: Map<u32, u128>,
        val_paid: Map<u32, u128>,
    }

    #[event]
    #[derive(Drop, starknet::Event)]
    pub enum Event {
        ValidatorSet: ValidatorSet,
        Sealed: Sealed,
        Revealed: Revealed,
        Finalized: Finalized,
    }

    #[derive(Drop, starknet::Event)]
    pub struct ValidatorSet {
        #[key]
        pub who: ContractAddress,
        pub stake: u128,
    }

    #[derive(Drop, starknet::Event)]
    pub struct Sealed {
        #[key]
        pub epoch: u64,
        #[key]
        pub who: ContractAddress,
    }

    #[derive(Drop, starknet::Event)]
    pub struct Revealed {
        #[key]
        pub epoch: u64,
        #[key]
        pub who: ContractAddress,
    }

    #[derive(Drop, starknet::Event)]
    pub struct Finalized {
        #[key]
        pub epoch: u64,
        pub voters: u32,
        pub emission: u128,
    }

    #[constructor]
    fn constructor(
        ref self: ContractState,
        owner: ContractAddress,
        miners: u32,
        commit_secs: u64,
        reveal_secs: u64,
        emission: u128,
    ) {
        // explicit owner: deploys go through the UDC, so the caller is not the deployer
        assert(!owner.is_zero(), 'zero owner');
        assert(miners > 0 && miners <= MAX_MINERS, 'miners 1..32');
        self.owner.write(owner);
        self.n_miners.write(miners);
        self.commit_secs.write(commit_secs);
        self.reveal_secs.write(reveal_secs);
        self.emission.write(emission);
        self.epoch_start.write(get_block_timestamp());
    }

    #[generate_trait]
    impl Internal of InternalTrait {
        fn slot(self: @ContractState, who: ContractAddress) -> u32 {
            let s = self.val_slot.read(who);
            assert(s != 0, 'not a validator');
            s - 1
        }

        fn commit_closed(self: @ContractState) -> bool {
            let n = self.n_vals.read();
            get_block_timestamp() >= self.epoch_start.read() + self.commit_secs.read()
                || (n > 0 && self.n_sealed.read() == n)
        }

        fn reveal_end(self: @ContractState) -> u64 {
            self.epoch_start.read() + self.commit_secs.read() + self.reveal_secs.read()
        }

        fn phase_now(self: @ContractState) -> u8 {
            if !self.commit_closed() {
                return PHASE_COMMIT;
            }
            let sealed = self.n_sealed.read();
            if get_block_timestamp() >= self.reveal_end()
                || (sealed > 0 && self.n_revealed.read() == sealed) {
                return PHASE_FINALIZE;
            }
            PHASE_REVEAL
        }

        fn row(self: @ContractState, map_epoch: u64, which: u8) -> Array<u128> {
            let mut out = array![];
            let n = if which == 2 { self.n_vals.read() } else { self.n_miners.read() };
            for j in 0..n {
                let v = if which == 0 {
                    self.cons.read((map_epoch, j))
                } else if which == 1 {
                    self.incent.read((map_epoch, j))
                } else {
                    self.divs.read((map_epoch, j))
                };
                out.append(v);
            };
            out
        }
    }

    #[abi(embed_v0)]
    impl SubnetImpl of super::ISubnet<ContractState> {
        fn set_validator(ref self: ContractState, who: ContractAddress, stake: u128) {
            assert(get_caller_address() == self.owner.read(), 'only owner');
            assert(!who.is_zero(), 'zero address');
            let s = self.val_slot.read(who);
            if s == 0 {
                // joining mid-epoch would move the "everyone sealed" goalpost
                assert(self.n_sealed.read() == 0, 'epoch already sealing');
                let n = self.n_vals.read();
                assert(n < MAX_VALIDATORS, 'validator set full');
                self.val_addr.write(n, who);
                self.val_slot.write(who, n + 1);
                self.stake.write(n, stake);
                self.n_vals.write(n + 1);
            } else {
                self.stake.write(s - 1, stake);
            }
            self.emit(ValidatorSet { who, stake });
        }

        fn commit(ref self: ContractState, seal: felt252) {
            let who = get_caller_address();
            let i = self.slot(who);
            assert(self.phase_now() == PHASE_COMMIT, 'commit window closed');
            assert(seal != 0, 'empty seal');
            let e = self.epoch.read();
            assert(self.seals.read((e, i)) == 0, 'already sealed');
            self.seals.write((e, i), seal);
            self.n_sealed.write(self.n_sealed.read() + 1);
            self.emit(Sealed { epoch: e, who });
        }

        fn reveal(ref self: ContractState, weights: Span<u16>, salt: felt252) {
            let who = get_caller_address();
            let i = self.slot(who);
            assert(self.phase_now() == PHASE_REVEAL, 'not reveal phase');
            let e = self.epoch.read();
            let s = self.seals.read((e, i));
            assert(s != 0, 'nothing sealed');
            assert(!self.opened.read((e, i)), 'already revealed');
            assert(weights.len() == self.n_miners.read(), 'one weight per miner');
            assert(seal_of(get_contract_address(), e, who, weights, salt) == s, 'bad reveal');
            let mut j: u32 = 0;
            for w in weights {
                self.weight.write((e, i, j), *w);
                j += 1;
            };
            self.opened.write((e, i), true);
            self.n_revealed.write(self.n_revealed.read() + 1);
            self.emit(Revealed { epoch: e, who });
        }

        fn finalize(ref self: ContractState) {
            assert(self.phase_now() == PHASE_FINALIZE, 'epoch still open');
            let e = self.epoch.read();
            let nv = self.n_vals.read();
            let nm = self.n_miners.read();

            // voters: revealed, staked, nonzero weights. Normalize weights + stake.
            let mut vi: Array<u32> = array![];
            let mut vs: Array<u128> = array![];
            let mut total_stake: u128 = 0;
            for i in 0..nv {
                let st = self.stake.read(i);
                if self.opened.read((e, i)) && st > 0 {
                    let mut sum: u128 = 0;
                    for j in 0..nm {
                        sum += self.weight.read((e, i, j)).into();
                    };
                    if sum > 0 {
                        vi.append(i);
                        vs.append(st);
                        total_stake += st;
                    }
                }
            };
            let k = vi.len();
            // w[a * nm + j] normalized weight of voter a on miner j; s[a] stake share
            let mut w: Array<u128> = array![];
            let mut s: Array<u128> = array![];
            for a in 0..k {
                let i = *vi.at(a);
                let mut sum: u128 = 0;
                for j in 0..nm {
                    sum += self.weight.read((e, i, j)).into();
                };
                for j in 0..nm {
                    let raw: u128 = self.weight.read((e, i, j)).into();
                    w.append(raw * ONE / sum);
                };
                s.append(*vs.at(a) * ONE / total_stake);
            };

            // consensus c_j = stake-weighted median, then clip and rank
            let mut c: Array<u128> = array![];
            let mut rank: Array<u128> = array![];
            let mut rank_total: u128 = 0;
            for j in 0..nm {
                let mut best: u128 = 0;
                for b in 0..k {
                    let cand = *w.at(b * nm + j);
                    if cand > best {
                        let mut support: u128 = 0;
                        for a in 0..k {
                            if *w.at(a * nm + j) >= cand {
                                support += *s.at(a);
                            }
                        };
                        if support * 2 >= ONE {
                            best = cand;
                        }
                    }
                };
                c.append(best);
                self.cons.write((e, j), best);
                let mut r: u128 = 0;
                for a in 0..k {
                    let wc = core::cmp::min(*w.at(a * nm + j), best);
                    r += *s.at(a) * wc / ONE;
                };
                rank.append(r);
                rank_total += r;
            };

            // incentive + miner emission
            let half = self.emission.read() / 2;
            let mut inc: Array<u128> = array![];
            for j in 0..nm {
                let ij = if rank_total == 0 { 0 } else { *rank.at(j) * ONE / rank_total };
                inc.append(ij);
                self.incent.write((e, j), ij);
                if ij > 0 {
                    self.miner_paid.write(j, self.miner_paid.read(j) + half * ij / ONE);
                }
            };

            // bonds: EMA toward this epoch's column-normalized clipped stake
            for j in 0..nm {
                let mut col: u128 = 0;
                for a in 0..k {
                    col += *s.at(a) * core::cmp::min(*w.at(a * nm + j), *c.at(j)) / ONE;
                };
                for i in 0..nv {
                    // voters get their share, everyone else decays toward zero
                    let mut fresh: u128 = 0;
                    for a in 0..k {
                        if *vi.at(a) == i && col > 0 {
                            let x = *s.at(a) * core::cmp::min(*w.at(a * nm + j), *c.at(j)) / ONE;
                            fresh = x * ONE / col;
                        }
                    };
                    let old = self.bond.read((i, j));
                    self.bond.write((i, j), (BOND_ALPHA * fresh + (ONE - BOND_ALPHA) * old) / ONE);
                };
            };

            // dividends D_i = sum_j B_ij I_j, normalized; validator emission
            let mut d: Array<u128> = array![];
            let mut d_total: u128 = 0;
            for i in 0..nv {
                let mut x: u128 = 0;
                for j in 0..nm {
                    x += self.bond.read((i, j)) * *inc.at(j) / ONE;
                };
                d.append(x);
                d_total += x;
            };
            for i in 0..nv {
                let di = if d_total == 0 { 0 } else { *d.at(i) * ONE / d_total };
                self.divs.write((e, i), di);
                if di > 0 {
                    self.val_paid.write(i, self.val_paid.read(i) + half * di / ONE);
                }
            };

            self.emit(Finalized { epoch: e, voters: k, emission: self.emission.read() });
            self.epoch.write(e + 1);
            self.epoch_start.write(get_block_timestamp());
            self.n_sealed.write(0);
            self.n_revealed.write(0);
        }

        fn seal(
            self: @ContractState, who: ContractAddress, weights: Span<u16>, salt: felt252,
        ) -> felt252 {
            seal_of(get_contract_address(), self.epoch.read(), who, weights, salt)
        }

        fn phase(self: @ContractState) -> u8 {
            self.phase_now()
        }

        fn epoch(self: @ContractState) -> u64 {
            self.epoch.read()
        }

        fn miners(self: @ContractState) -> u32 {
            self.n_miners.read()
        }

        fn validators(self: @ContractState) -> Array<ContractAddress> {
            let mut out = array![];
            for i in 0..self.n_vals.read() {
                out.append(self.val_addr.read(i));
            };
            out
        }

        fn stake_of(self: @ContractState, who: ContractAddress) -> u128 {
            let s = self.val_slot.read(who);
            if s == 0 { 0 } else { self.stake.read(s - 1) }
        }

        fn sealed(self: @ContractState) -> u32 {
            self.n_sealed.read()
        }

        fn revealed(self: @ContractState) -> u32 {
            self.n_revealed.read()
        }

        fn has_sealed(self: @ContractState, who: ContractAddress) -> bool {
            let s = self.val_slot.read(who);
            s != 0 && self.seals.read((self.epoch.read(), s - 1)) != 0
        }

        fn weights_of(self: @ContractState, epoch: u64, who: ContractAddress) -> Array<u16> {
            let i = self.slot(who);
            // the current epoch's weights only exist once revealed anyway; this
            // just makes "sealed" read as an error instead of zeros
            assert(self.opened.read((epoch, i)), 'not revealed');
            let mut out = array![];
            for j in 0..self.n_miners.read() {
                out.append(self.weight.read((epoch, i, j)));
            };
            out
        }

        fn consensus(self: @ContractState, epoch: u64) -> Array<u128> {
            self.row(epoch, 0)
        }

        fn incentive(self: @ContractState, epoch: u64) -> Array<u128> {
            self.row(epoch, 1)
        }

        fn dividends(self: @ContractState, epoch: u64) -> Array<u128> {
            self.row(epoch, 2)
        }

        fn miner_earned(self: @ContractState, miner: u32) -> u128 {
            self.miner_paid.read(miner)
        }

        fn validator_earned(self: @ContractState, who: ContractAddress) -> u128 {
            self.val_paid.read(self.slot(who))
        }
    }
}
