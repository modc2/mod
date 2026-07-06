export interface Game {
  id: string
  chain_game_id: number | null
  organizer: string
  title: string
  description: string
  game_type: string
  location: string
  date: string
  time: string
  max_players: number
  entry_fee: string
  players: string[]
  tx_hashes: Record<string, string>
  status: 'pending_approval' | 'open' | 'full' | 'completed' | 'cancelled' | 'removed'
  created_at: number
  updated_at: number
  completed_at?: number
  cancelled_at?: number
  completion_tx?: string
}

export type CoPlayTab = 'games' | 'my_games' | 'create' | 'admin'

export interface CoPlayConfig {
  contract_address: string | null
  chain_id: number
}
