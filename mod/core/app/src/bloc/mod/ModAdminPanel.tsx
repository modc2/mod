'use client'

import { useState } from 'react'
import { ModuleType } from '@/bloc/types'
import { useUserContext } from '@/bloc/context'
import { Settings, Globe, Lock, Users, Save, X } from 'lucide-react'

interface ModAdminPanelProps {
  mod: ModuleType
  onClose: () => void
}

export const ModAdminPanel = ({ mod, onClose }: ModAdminPanelProps) => {
  const { user, client } = useUserContext()
  const [modName, setModName] = useState(mod.name || '')
  const [isPublic, setIsPublic] = useState(mod.public || false)
  const [allowedUsers, setAllowedUsers] = useState<string[]>(mod.allowed_users || [])
  const [newUser, setNewUser] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState(false)

  const isOwner = user && user.key === mod.key

  if (!isOwner) {
    return null
  }

  const handleAddUser = () => {
    if (newUser.trim() && !allowedUsers.includes(newUser.trim())) {
      setAllowedUsers([...allowedUsers, newUser.trim()])
      setNewUser('')
    }
  }

  const handleRemoveUser = (userToRemove: string) => {
    setAllowedUsers(allowedUsers.filter(u => u !== userToRemove))
  }

  const handleSave = async () => {
    if (!client) return
    
    setLoading(true)
    setError(null)
    setSuccess(false)

    try {
      await client.call('update_mod_settings', {
        mod: mod.name,
        key: mod.key,
        settings: {
          name: modName,
          public: isPublic,
          allowed_users: allowedUsers
        }
      })
      setSuccess(true)
      setTimeout(() => onClose(), 1500)
    } catch (err: any) {
      setError(err?.message || 'Failed to update module settings')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm">
      <div className="relative w-full max-w-2xl mx-4 bg-gradient-to-br from-gray-900 to-black border-2 border-purple-500/50 rounded-2xl shadow-2xl shadow-purple-500/20">
        <div className="flex items-center justify-between p-6 border-b border-purple-500/30">
          <div className="flex items-center gap-3">
            <Settings className="w-6 h-6 text-purple-400" />
            <h2 className="text-2xl font-bold text-purple-300 font-mono uppercase">Admin Settings</h2>
          </div>
          <button
            onClick={onClose}
            className="p-2 hover:bg-purple-500/20 rounded-lg transition-colors"
          >
            <X className="w-6 h-6 text-purple-400" />
          </button>
        </div>

        <div className="p-6 space-y-6">
          <div className="space-y-2">
            <label className="text-sm text-purple-400 font-mono uppercase font-bold tracking-wide">
              Module Name
            </label>
            <input
              type="text"
              value={modName}
              onChange={(e) => setModName(e.target.value)}
              disabled={loading}
              className="w-full bg-black/60 border-2 border-purple-500/40 rounded-lg px-4 py-3 text-purple-300 font-mono text-base focus:outline-none focus:border-purple-500 focus:ring-2 focus:ring-purple-500/30 disabled:opacity-50 transition-all"
            />
          </div>

          <div className="space-y-3">
            <label className="flex items-center gap-3 px-4 py-3 bg-black/60 border-2 border-purple-500/40 rounded-lg cursor-pointer hover:border-purple-500/70 transition-all">
              <input
                type="checkbox"
                checked={isPublic}
                onChange={(e) => setIsPublic(e.target.checked)}
                disabled={loading}
                className="w-5 h-5 accent-purple-500 cursor-pointer"
              />
              {isPublic ? (
                <Globe className="w-5 h-5 text-green-400" />
              ) : (
                <Lock className="w-5 h-5 text-yellow-400" />
              )}
              <span className="text-sm font-bold text-purple-300 uppercase tracking-wider">
                {isPublic ? 'Public Module' : 'Private Module'}
              </span>
            </label>
          </div>

          <div className="space-y-3">
            <div className="flex items-center gap-2">
              <Users className="w-5 h-5 text-purple-400" />
              <label className="text-sm text-purple-400 font-mono uppercase font-bold tracking-wide">
                Allowed Users
              </label>
            </div>
            
            <div className="flex gap-2">
              <input
                type="text"
                value={newUser}
                onChange={(e) => setNewUser(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && handleAddUser()}
                placeholder="Enter user key..."
                disabled={loading}
                className="flex-1 bg-black/60 border-2 border-purple-500/40 rounded-lg px-4 py-2 text-purple-300 font-mono text-sm focus:outline-none focus:border-purple-500 disabled:opacity-50 transition-all"
              />
              <button
                onClick={handleAddUser}
                disabled={!newUser.trim() || loading}
                className="px-4 py-2 bg-purple-500/20 border-2 border-purple-500/50 rounded-lg text-purple-300 font-bold hover:bg-purple-500/30 disabled:opacity-50 transition-all"
              >
                Add
              </button>
            </div>

            <div className="space-y-2 max-h-40 overflow-y-auto">
              {allowedUsers.map((user, idx) => (
                <div
                  key={idx}
                  className="flex items-center justify-between px-4 py-2 bg-black/60 border border-purple-500/30 rounded-lg"
                >
                  <code className="text-sm text-purple-300 font-mono">{user}</code>
                  <button
                    onClick={() => handleRemoveUser(user)}
                    disabled={loading}
                    className="p-1 hover:bg-red-500/20 rounded transition-colors"
                  >
                    <X className="w-4 h-4 text-red-400" />
                  </button>
                </div>
              ))}
              {allowedUsers.length === 0 && (
                <p className="text-sm text-purple-400/60 text-center py-4">No users added yet</p>
              )}
            </div>
          </div>

          {error && (
            <div className="p-4 bg-red-500/10 border-2 border-red-500/40 rounded-lg">
              <p className="text-red-400 font-mono text-sm">{error}</p>
            </div>
          )}

          {success && (
            <div className="p-4 bg-green-500/10 border-2 border-green-500/40 rounded-lg">
              <p className="text-green-400 font-mono text-sm">Settings updated successfully!</p>
            </div>
          )}

          <button
            onClick={handleSave}
            disabled={loading || !modName.trim()}
            className="w-full py-3 bg-gradient-to-r from-purple-500/20 to-pink-500/20 border-2 border-purple-500/60 rounded-lg text-purple-300 font-bold uppercase tracking-wider hover:bg-purple-500/30 hover:border-purple-500 disabled:opacity-50 disabled:cursor-not-allowed transition-all flex items-center justify-center gap-2"
          >
            {loading ? (
              <>
                <div className="w-5 h-5 border-2 border-purple-400 border-t-transparent rounded-full animate-spin" />
                <span>Saving...</span>
              </>
            ) : (
              <>
                <Save className="w-5 h-5" />
                <span>Save Changes</span>
              </>
            )}
          </button>
        </div>
      </div>
    </div>
  )
}

export default ModAdminPanel