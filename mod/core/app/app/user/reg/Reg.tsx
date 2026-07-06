"use client";
import { useEffect, useState } from 'react'
import { Package, Upload, Database, Loader2, CheckCircle, AlertCircle, ArrowUpDown, Plus, Trash2 } from 'lucide-react'
import {userContext} from '@/context'
// dynamic import: @polkadot/extension-dapp accesses window at load time
import { stringToU8a, u8aToHex } from '@polkadot/util'
import ModCard from '@/mod/ModCard'
import { ModuleType } from '@/types'
import { UrlTypeSelector, UrlType } from './UrlTypeSelector'
import { Key } from '@/key'

type FieldType = 'string' | 'int' | 'float' | 'bytes' | 'dict' | 'bool'

interface CustomField {
  name: string
  type: FieldType
  value: string
}

export const Reg = ( ) => {
  const { client, network } = userContext()
  const [isSubwalletEnabled, setIsSubwalletEnabled] = useState(false)
  const [modUrl, setModUrl] = useState('')
  const [urlType, setUrlType] = useState<UrlType>('git')
  const [modName, setModName] = useState('')
  const [collateral, setCollateral] = useState(0.0)
  const [isLoading, setIsLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)
  const [signatureInfo, setSignatureInfo] = useState<{signature: string, timestamp: number, address: string} | null>(null)
  const [isLocalWallet, setIsLocalWallet] = useState(false)
  const [walletAddress, setWalletAddress] = useState('')
  const [createdMod, setCreatedMod] = useState<ModuleType | null>(null)
  const [registrationType, setRegistrationType] = useState<'local' | 'onchain'>('local')
  const [localModules, setLocalModules] = useState<ModuleType[]>([])
  const [selectedLocalMod, setSelectedLocalMod] = useState<string>('')
  const [sortAsc, setSortAsc] = useState(false)
  const [customFields, setCustomFields] = useState<CustomField[]>([
    { name: 'name', type: 'string', value: '' },
    { name: 'cid', type: 'string', value: '' }
  ])

  useEffect(() => {
    const walletMode = localStorage.getItem('wallet_mode')
    const address = localStorage.getItem('wallet_address')
    
    setIsSubwalletEnabled(walletMode === 'subwallet')
    setIsLocalWallet(walletMode === 'local')
    setWalletAddress(address || '')
  }, [client])

  useEffect(() => {
    const fetchLocalModules = async () => {
      if (!client) return
      try {
        const response = await client.call('mods', {})
        const localMods = response.filter((mod: ModuleType) => mod.network === 'local')
        setLocalModules(localMods)
        if (localMods.length > 0 && !selectedLocalMod) {
          setSelectedLocalMod(localMods[0].name)
        }
        console.log('Fetched local modules:', selectedLocalMod, localMods)
      } catch (err) {
        console.error('Failed to fetch local modules:', err)
      }
    }
    fetchLocalModules()
  }, [client, registrationType])

  const handleUrlChange = async (value: string, inferredType: UrlType) => {
    setModUrl(value)
    setUrlType(inferredType)

    // If it's a fork type, fetch the module data to pre-populate fields
    if (inferredType === 'fork' && value.trim() && client) {
      try {
        const modPath = value.trim()
        const parts = modPath.split('/')
        let modName = parts.length === 2 ? parts[1] : parts[0]

        // Try to fetch the existing module
        const existingMods = await client.call('mods', { search: modName })
        const existingMod = Array.isArray(existingMods) ? existingMods.find((m: any) => m.name === modName) : null

        if (existingMod) {
          setModName(modName)
          updateFieldValue('name', modName)
          updateFieldValue('cid', existingMod.cid || '')

          // Set desc if available
          if (existingMod.desc) {
            const descField = customFields.find(f => f.name === 'desc')
            if (descField) {
              updateCustomField(customFields.indexOf(descField), { value: existingMod.desc })
            } else {
              setCustomFields(prev => [...prev, { name: 'desc', type: 'string', value: existingMod.desc }])
            }
          }
        } else {
          setModName(modName)
          updateFieldValue('name', modName)
        }
      } catch (err) {
        console.error('Failed to fetch module for fork:', err)
        // Fallback to just using the name
        const modPath = value.trim()
        const parts = modPath.split('/')
        let name = parts.length === 2 ? parts[1] : parts[0]
        setModName(name.toLowerCase())
        updateFieldValue('name', name.toLowerCase())
      }
    } else {
      // Original logic for other URL types
      let name = value.split('/')[value.split('/').length - 1]
      name = name.endsWith('.git') ? name.slice(0, -4) : name
      name = name.toLowerCase()
      setModName(name)
      updateFieldValue('name', name)
    }
  }

  const handleNameChange = (e: any) => {
    const newName = e.target.value || ''
    setModName(newName)
    updateFieldValue('name', newName)
  }

  const updateFieldValue = (fieldName: string, value: string) => {
    setCustomFields(prev => prev.map(f => 
      f.name === fieldName ? { ...f, value } : f
    ))
  }

  const addCustomField = () => {
    setCustomFields(prev => [...prev, { name: '', type: 'string', value: '' }])
  }

  const removeCustomField = (index: number) => {
    if (customFields.length <= 2) return // Keep at least name and cid
    setCustomFields(prev => prev.filter((_, i) => i !== index))
  }

  const updateCustomField = (index: number, field: Partial<CustomField>) => {
    setCustomFields(prev => prev.map((f, i) => 
      i === index ? { ...f, ...field } : f
    ))
  }

  const convertFieldValue = (value: string, type: FieldType): any => {
    switch (type) {
      case 'int': return parseInt(value) || 0
      case 'float': return parseFloat(value) || 0.0
      case 'bool': return value.toLowerCase() === 'true'
      case 'dict': 
        try { return JSON.parse(value) } catch { return {} }
      case 'bytes': return value
      default: return value
    }
  }

  const buildModPayload = () => {
    const payload: any = {}
    customFields.forEach(field => {
      if (field.name) {
        payload[field.name] = convertFieldValue(field.value, field.type)
      }
    })
    return payload
  }

  const getLocalKey = (): Key | null => {
    try {
      const password = localStorage.getItem('wallet_password') || ''
      const cryptoType = localStorage.getItem('wallet_type') || 'ecdsa'
      return new Key(password, cryptoType as any)
    } catch (err) {
      console.error('Failed to get local key:', err)
      return null
    }
  }

  const handleCreateModuleLocal = async () => {
    if (!modUrl.trim()) {
      setError('Please enter a valid URL or hash')
      return
    }

    setIsLoading(true)
    setError(null)
    setSuccess(null)
    setSignatureInfo(null)
    setCreatedMod(null)

    try {
      if (!client) {
        throw new Error('Client not initialized')
      }
      let signature: string
      let signerAddress: string
      let reg_payload: any
      if (isSubwalletEnabled && walletAddress) {
        reg_payload = await client.call('reg_url', {'url': modUrl.trim(), 'key':walletAddress , 'collateral': collateral, 'payload': true})
        let messageToSign = JSON.stringify(reg_payload)

        const { web3Enable, web3FromAddress } = await import('@polkadot/extension-dapp')
        const extensions = await web3Enable('MOD')
        if (extensions.length === 0) {
          throw new Error('SubWallet not found. Please install it.')
        }

        const injector = await web3FromAddress(walletAddress)
        const signRaw = injector?.signer?.signRaw
        if (signRaw) {
          const { signature: sig } = await signRaw({
            address: walletAddress,
            data: u8aToHex(stringToU8a(messageToSign)),
            type: 'bytes'
          })
          signature = sig
          signerAddress = walletAddress
        } else {
          throw new Error('SubWallet signing not available')
        }
      } else if (isLocalWallet) {
        const localKey = getLocalKey()
        if (!localKey) {
          throw new Error('Local key not found. Please sign in with Local Key.')
        }
        reg_payload = await client.call('reg_payload', {'url': modUrl.trim(), 'key':localKey.address , 'collateral': collateral})
        let messageToSign = JSON.stringify(reg_payload)
        signature = localKey.sign(messageToSign)
        signerAddress = localKey.address
      } else {
        throw new Error('No signing method available. Please connect SubWallet or sign in with Local Key first.')
      }
      const previewData = {
        ...reg_payload,
        signature: signature,
      }

      // Fire and forget - don't wait for response
      client.call('reg', {mod: previewData, token: client.token}, true, {}, 120000)

      const timestamp = Date.now()
      setSignatureInfo({
        signature: signature,
        timestamp: timestamp,
        address: signerAddress
      })

      const newMod: ModuleType = {
        name: modName.trim() || reg_payload?.name || 'New Module',
        key: signerAddress,
        desc: reg_payload?.desc || '',
        cid: reg_payload?.cid || '',
        created: timestamp,
        updated: timestamp,
        collateral: 0,
        network: 'local'
      }

      setCreatedMod(newMod)
      setSuccess(`Module registration submitted`)
      setModUrl('')
      setModName('')
      setCollateral(0.0)
      setCustomFields([
        { name: 'name', type: 'string', value: '' },
        { name: 'cid', type: 'string', value: '' }
      ])
    } catch (err: any) {
      console.error('Module creation error:', err)
      setError(err.message || 'Failed to create module')
    } finally {
      setIsLoading(false)
    }
  }

  const handleCreateModuleOnchain = async () => {
    console.log('Starting onchain module registration with selectedLocalMod:', selectedLocalMod)
    if (!selectedLocalMod) {
      setError('Please select a local module to register onchain')
      return
    }

    setIsLoading(true)
    setError(null)
    setSuccess(null)
    setSignatureInfo(null)
    setCreatedMod(null)

    try {
      if (!network) {
        throw new Error('Network not initialized')
      }

      if (!isSubwalletEnabled || !walletAddress) {
        throw new Error('SubWallet connection required for onchain registration')
      }

      const { web3Enable: enable } = await import('@polkadot/extension-dapp')
      const extensions = await enable('MOD')
      if (extensions.length === 0) {
        throw new Error('SubWallet not found. Please install it.')
      }

      const selectedMod = localModules.find(m => m.name === selectedLocalMod)
      if (!selectedMod) {
        throw new Error('Selected module not found')
      }

      const result = await network.register(
        walletAddress,
        selectedMod.name + '/' + walletAddress,
        selectedMod.cid || '',
        (typeof selectedMod.url === 'string' ? selectedMod.url : '') || '0.0.0.0:8888',
        collateral,
      )

      const timestamp = Date.now()
      setSignatureInfo({
        signature: result.signature || 'onchain',
        timestamp: timestamp,
        address: walletAddress
      })

      const newMod: ModuleType = {
        name: selectedMod.name,
        key: walletAddress,
        desc: selectedMod.desc || '',
        cid: selectedMod.cid || '',
        created: timestamp,
        updated: timestamp,
        collateral: collateral,
        network: 'local'
      }

      setCreatedMod(newMod)
      setSuccess(`Module registered onchain successfully! TX: ${result.blockHash || 'pending'}`)
      setSelectedLocalMod('')
      setCollateral(0.0)
    } catch (err: any) {
      console.error('Onchain registration error:', err)
      setError(err.message || 'Failed to register module onchain')
    } finally {
      setIsLoading(false)
    }
  }

  const handleCreateModule = () => {
    if (registrationType === 'local') {
      handleCreateModuleLocal()
    } else {
      handleCreateModuleOnchain()
    }
  }

  const sortedLocalModules = [...localModules].sort((a, b) => {
    const timeA = new Date(a.updated || 0).getTime()
    const timeB = new Date(b.updated || 0).getTime()
    return sortAsc ? timeA - timeB : timeB - timeA
  })

  return (
    <div className="space-y-6 animate-fadeIn">
      <div className="space-y-5 p-6 rounded-xl bg-gradient-to-br from-purple-500/10 via-pink-500/10 to-cyan-500/10 border-2 border-purple-500/30 shadow-2xl">

        <div className="space-y-4">
          <div className="flex gap-2 mb-4">
            <button
              onClick={() => setRegistrationType('local')}
              className={`flex-1 px-4 py-2 rounded-lg font-bold text-xs uppercase tracking-wider transition-all border ${
                registrationType === 'local'
                  ? 'bg-purple-500/30 text-purple-300 border-purple-500'
                  : 'bg-black/60 text-purple-500/60 border-purple-500/30 hover:bg-purple-500/10 hover:border-purple-500/50'
              }`}
            >
              API
            </button>
            <button
              onClick={() => setRegistrationType('onchain')}
              className={`flex-1 px-4 py-2 rounded-lg font-bold text-xs uppercase tracking-wider transition-all border ${
                registrationType === 'onchain'
                  ? 'bg-purple-500/30 text-purple-300 border-purple-500'
                  : 'bg-black/60 text-purple-500/60 border-purple-500/30 hover:bg-purple-500/10 hover:border-purple-500/50'
              }`}
            >
              {network?.url ? network.url.split('//')[1]?.split('.')[0]?.toUpperCase() || 'CHAIN' : 'CHAIN'}
            </button>
          </div>

          {registrationType === 'local' ? (
            <>
              <div className="space-y-2">
                <label className="text-sm text-purple-400 font-mono uppercase font-bold tracking-wide">
                  Module Source
                </label>
                <UrlTypeSelector
                  value={modUrl}
                  onChange={handleUrlChange}
                  selectedType={urlType}
                  onTypeChange={setUrlType}
                />
              </div>

              <div className="space-y-4 p-4 bg-black/40 rounded-lg border border-purple-500/30">
                <div className="flex items-center justify-between">
                  <label className="text-sm text-purple-400 font-mono uppercase font-bold tracking-wide">
                    Custom Fields
                  </label>
                  <button
                    onClick={addCustomField}
                    className="px-3 py-1.5 rounded-lg bg-purple-500/20 text-purple-300 border border-purple-500/40 hover:bg-purple-500/30 transition-all flex items-center gap-2 text-xs font-bold"
                  >
                    <Plus size={14} />
                    ADD FIELD
                  </button>
                </div>

                {customFields.map((field, index) => (
                  <div key={index} className="grid grid-cols-12 gap-2 items-center">
                    <input
                      type="text"
                      value={field.name}
                      onChange={(e) => updateCustomField(index, { name: e.target.value })}
                      placeholder="Field name"
                      disabled={field.name === 'name' || field.name === 'cid'}
                      className="col-span-3 bg-black/60 border border-purple-500/40 rounded px-3 py-2 text-purple-300 font-mono text-sm placeholder-purple-600/50 focus:outline-none focus:border-purple-500 disabled:opacity-50"
                    />
                    <select
                      value={field.type}
                      onChange={(e) => updateCustomField(index, { type: e.target.value as FieldType })}
                      className="col-span-2 bg-black/60 border border-purple-500/40 rounded px-3 py-2 text-purple-300 font-mono text-sm focus:outline-none focus:border-purple-500"
                    >
                      <option value="string">string</option>
                      <option value="int">int</option>
                      <option value="float">float</option>
                      <option value="bytes">bytes</option>
                      <option value="dict">dict</option>
                      <option value="bool">bool</option>
                    </select>
                    <input
                      type="text"
                      value={field.value}
                      onChange={(e) => updateCustomField(index, { value: e.target.value })}
                      placeholder="Value"
                      className="col-span-6 bg-black/60 border border-purple-500/40 rounded px-3 py-2 text-purple-300 font-mono text-sm placeholder-purple-600/50 focus:outline-none focus:border-purple-500"
                    />
                    <button
                      onClick={() => removeCustomField(index)}
                      disabled={field.name === 'name' || field.name === 'cid' || customFields.length <= 2}
                      className="col-span-1 p-2 rounded bg-red-500/20 text-red-400 border border-red-500/40 hover:bg-red-500/30 transition-all disabled:opacity-30 disabled:cursor-not-allowed"
                    >
                      <Trash2 size={16} />
                    </button>
                  </div>
                ))}
              </div>
            </>
          ) : (
            <>
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <label className="text-sm text-purple-400 font-mono uppercase font-bold tracking-wide">
                    Select Local Module
                  </label>
                  <button
                    onClick={() => setSortAsc(!sortAsc)}
                    className="px-3 py-1.5 rounded-lg hover:opacity-80 transition-all flex items-center gap-2 text-xs font-semibold border"
                    style={{ backgroundColor: 'rgba(0,0,0,0.4)', color: '#a855f7', borderColor: '#a855f740' }}
                  >
                    <ArrowUpDown className="w-4 h-4" />
                    {sortAsc ? 'Oldest' : 'Newest'}
                  </button>
                </div>
                <select
                  value={selectedLocalMod}
                  onChange={(e) => setSelectedLocalMod(e.target.value)}
                  className="w-full bg-black/60 border-2 border-purple-500/40 rounded-lg px-4 py-3 text-purple-300 font-mono text-base focus:outline-none focus:border-purple-500 focus:ring-2 focus:ring-purple-500/30 transition-all"
                >
                  <option value="">-- Select a local module --</option>
                  {sortedLocalModules.map((mod) => (
                    <option key={mod.name} value={mod.name}>
                      {mod.name}
                    </option>
                  ))}
                </select>
                {localModules.length === 0 && (
                  <p className="text-purple-400/60 text-sm font-mono mt-2">
                    No local modules found. Please register a module locally first.
                  </p>
                )}
              </div>

            </>
          )}

          <button
            onClick={handleCreateModule}
            disabled={(!isSubwalletEnabled && !isLocalWallet) || (registrationType === 'onchain' && !selectedLocalMod) || (registrationType === 'local' && !modUrl.trim())}
            className="w-full py-4 border-2 border-purple-500/60 bg-gradient-to-r from-purple-500/20 to-pink-500/20 text-purple-400 hover:bg-purple-500/30 hover:border-purple-500 hover:scale-[1.02] transition-all duration-300 rounded-xl font-mono uppercase font-black text-lg disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:scale-100 flex items-center justify-center gap-3 shadow-lg"
          >
            {isLoading ? (
              <>
                <Loader2 size={20} className="animate-spin" />
                <span>DEPLOYING...</span>
              </>
            ) : (
              <>
                <Upload size={20} />
                <span>DEPLOY {registrationType === 'onchain' ? 'ONCHAIN' : 'LOCALLY'}</span>
              </>
            )}
          </button>
        </div>
      </div>

      {createdMod && (
        <div className="space-y-4">
          <div className="flex items-center justify-center gap-3 text-emerald-400 font-black text-xl uppercase tracking-wide">
            <CheckCircle size={24} />
            <span>MODULE DEPLOYED SUCCESSFULLY</span>
          </div>
          <ModCard mod={createdMod} card_enabled={true} />
        </div>
      )}

      {signatureInfo && (
        <div className="space-y-4 p-6 rounded-xl border-2 border-emerald-500/40 bg-gradient-to-br from-emerald-500/10 shadow-2xl">
          <div className="flex items-center gap-3 text-base font-mono uppercase font-bold text-emerald-400">
            <CheckCircle size={20} />
            <span>SIGNATURE VERIFICATION</span>
          </div>
          <div className="space-y-3">
            <div className="bg-black/60 p-4 rounded-lg border-2 border-emerald-500/30">
              <span className="text-emerald-500/70 bloc mb-2 font-mono uppercase text-xs font-bold">ADDRESS:</span>
              <p className="break-all text-emerald-300 font-mono text-sm">{signatureInfo.address}</p>
            </div>
            <div className="bg-black/60 p-4 rounded-lg border-2 border-emerald-500/30">
              <span className="text-emerald-500/70 bloc mb-2 font-mono uppercase text-xs font-bold">TIMESTAMP:</span>
              <p className="text-emerald-300 font-mono text-sm">{new Date(signatureInfo.timestamp).toISOString()}</p>
            </div>
            <div className="bg-black/60 p-4 rounded-lg border-2 border-emerald-500/30">
              <span className="text-emerald-500/70 bloc mb-2 font-mono uppercase text-xs font-bold">SIGNATURE:</span>
              <p className="break-all text-emerald-300 font-mono text-xs">{signatureInfo.signature}</p>
            </div>
          </div>
        </div>
      )}

      {error && (
        <div className="space-y-4 p-6 rounded-xl border-2 border-red-500/40 bg-gradient-to-br from-red-500/10 shadow-2xl">
          <div className="flex items-center gap-3 text-base font-mono uppercase font-bold text-red-400">
            <AlertCircle size={20} />
            <span>ERROR</span>
          </div>
          <div className="text-red-400 font-mono text-base bg-black/60 p-4 rounded-lg border-2 border-red-500/30 whitespace-pre-wrap font-bold">
            {error}
          </div>
        </div>
      )}

      {success && (
        <div className="space-y-4 p-6 rounded-xl border-2 border-emerald-500/40 bg-gradient-to-br from-emerald-500/10 shadow-2xl">
          <div className="flex items-center gap-3 text-base font-mono uppercase font-bold text-emerald-400">
            <CheckCircle size={20} />
            <span>SUCCESS</span>
          </div>
          <pre className="text-emerald-400 font-mono text-sm overflow-x-auto bg-black/60 p-4 rounded-lg border-2 border-emerald-500/30">
{success}
          </pre>
        </div>
      )}
    </div>
  )
}

export default Reg
