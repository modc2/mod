'use client'
import { useState } from 'react'
import { Package, Upload, Github, Database, Send, Loader2, CheckCircle, AlertCircle } from 'lucide-react'
import { Key } from '@/app/block/key'
import { Auth } from '@/app/block/client/auth'
import  Client  from '@/app/block/client'

interface CreateModuleTabProps {
  keyInstance: Key
}

export const CreateModuleTab = ({ keyInstance }: CreateModuleTabProps) => {
  const [moduleUrl, setModuleUrl] = useState('')
  const [isLoading, setIsLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)

  const handleCreateModule = async () => {
    if (!moduleUrl.trim()) {
      setError('Please enter a valid URL or IPFS hash')
      return
    }

    setIsLoading(true)
    setError(null)
    setSuccess(null)

    try {
      const client = new Client(undefined, keyInstance)
      const timestamp = Math.floor(Date.now() / 1000)
      
      const params = {
        path: moduleUrl,
        timestamp: timestamp
      }

      const auth = new Auth(keyInstance, 'sha256', 60)
      const headers = auth.generate(params)

      const signedParams = {
        ...params,
        signature: headers.signature,
        key: headers.key,
        crypto_type: headers.crypto_type
      }

      const response = await client.call('addmod', signedParams)
      
      setSuccess(`Module created successfully! Response: ${JSON.stringify(response)}`)
      setModuleUrl('')
    } catch (err: any) {
      setError(err.message || 'Failed to create module')
    } finally {
      setIsLoading(false)
    }
  }

  return (
    <div className="space-y-6 animate-fadeIn">
      <div className="space-y-3 p-4 rounded-lg bg-gradient-to-br from-purple-500/5 to-transparent border border-purple-500/20">
        <div className="flex items-center gap-2 text-purple-500/70 text-sm font-mono uppercase mb-3">
          <Package size={16} />
          <span>CREATE MODULE</span>
        </div>

        <div className="space-y-4">
          <div>
            <label className="text-xs text-purple-500/70 font-mono uppercase mb-2 block">
              Module URL or IPFS Hash
            </label>
            <div className="relative">
              <input
                type="text"
                value={moduleUrl}
                onChange={(e) => setModuleUrl(e.target.value)}
                placeholder="https://github.com/user/repo or ipfs://Qm..."
                className="w-full bg-black/50 border border-purple-500/30 rounded px-3 py-3 pl-10 text-purple-400 font-mono text-sm placeholder-purple-600/50 focus:outline-none focus:border-purple-500"
              />
              <div className="absolute left-3 top-1/2 -translate-y-1/2 text-purple-500/50">
                {moduleUrl.includes('github') ? <Github size={16} /> : <Database size={16} />}
              </div>
            </div>
            <p className="text-xs text-purple-500/50 mt-2 font-mono">
              Supports GitHub URLs and IPFS hashes
            </p>
          </div>

          <button
            onClick={handleCreateModule}
            disabled={!moduleUrl.trim() || isLoading}
            className="w-full py-3 border border-purple-500/50 text-purple-400 hover:bg-purple-500/10 hover:border-purple-500 transition-all rounded-lg font-mono uppercase disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2 group"
          >
            {isLoading ? (
              <>
                <Loader2 size={20} className="animate-spin" />
                <span>Creating Module...</span>
              </>
            ) : (
              <>
                <Upload size={20} className="group-hover:translate-y-[-2px] transition-transform" />
                <span>Create Module</span>
              </>
            )}
          </button>
        </div>
      </div>

      {error && (
        <div className="p-4 rounded-lg bg-gradient-to-br from-red-500/5 to-transparent border border-red-500/20">
          <div className="flex items-center gap-2 text-red-500/70 text-sm font-mono uppercase mb-2">
            <AlertCircle size={16} />
            <span>Error</span>
          </div>
          <p className="text-red-400 font-mono text-sm">{error}</p>
        </div>
      )}

      {success && (
        <div className="p-4 rounded-lg bg-gradient-to-br from-green-500/5 to-transparent border border-green-500/20">
          <div className="flex items-center gap-2 text-green-500/70 text-sm font-mono uppercase mb-2">
            <CheckCircle size={16} />
            <span>Success</span>
          </div>
          <p className="text-green-400 font-mono text-sm">{success}</p>
        </div>
      )}
    </div>
  )
}

export default CreateModuleTab