import { ModuleType } from './mod'

export interface UserType {
  key: string
  mods: ModuleType[]
  balance: number
}

export interface UsersState {
  users: UserType[]
  n: number
  loading: boolean
  error: string | null
}
