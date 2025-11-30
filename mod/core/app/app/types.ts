


  export type ModuleType = {
      name: string
      key: string // address 
      desc?: string // description
      content?: string // CID to the content of the user
      created: number // time of the mod
      updated?: number // time of last update
      schema?: string // the schema of the mod
      url?: string // the url of the server
      url_app?: string // the url of the app
      chain_id?: string // chain id if applicable
      collateral?: number // collateral locked in the mod
      cid?: string // CID of the mod content
      network?: string // network of the mod
    }


export interface ModulesState {
    mods: ModuleType[]
    n: number
    loading: boolean
    error: string | null
  }
  
export interface UserType {
  key: string
  crypto_type: string
  balance?: number
  mods?: any[]
  network?: string
}



export interface UsersState {
  users: UserType[]
  n: number
  loading: boolean
  error: string | null
}
