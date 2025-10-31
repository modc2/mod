


  export type ModuleType = {
      name: string
      key: string // address 
      desc?: string // description
      content?: string // code of the mod
      createed: number // time of the mod
      updated?: number // time of last update
      schema?: string // the schema of the mod
      url?: string // the url of the server
    }


interface ModulesState {
    mods: ModuleType[]
    n: number
    loading: boolean
    error: string | null
  }
  

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
