export interface SubnetInfo {
  id: number;
  owner: string;
  name: string;
  account_id: string;
  registered_block: number;
  active: boolean;
  consensus_type: string;
  inflation_type: string;
  stake_score: string;
  is_immune: boolean;
}

export interface ValidatorEntry {
  key: string;
  score: string;
  total_stt: string;
  commission_bps: number;
  active: boolean;
}

export interface ConsensusState {
  current_block: number;
  last_emission_block: number;
  total_blocktime: string;
  epoch_length: number;
  current_epoch: number;
  consensus_type: string;
  inflation_type: any;
  emission_rate: string;
  total_supply: string;
}

export interface StakePosition {
  stake_id: number;
  staker: string;
  validator_key: string;
  amount: string;
  start_block: number;
  lock_blocks: number;
  minted_balance: string;
  unlocks_at: number;
}

export interface PoolInfo {
  total_shares: string;
  total_bloctime: string;
  current_price: string;
  locked_stake: string;
}
