'use client'

import { UserType } from '@/app/types'
import {ModCard} from '@/app/mod/explore/ModCard'


type TabType = 'modules'

function UserModules({ userData }: { userData: UserType })  {
  const { mods } = userData
  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-6">
      {mods.map((mod) => (
        <ModCard mod={mod} />
      ))}
    </div>
  )
}