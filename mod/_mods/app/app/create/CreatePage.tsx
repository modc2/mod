'use client'
import { useEffect, useState } from 'react'
import { Package, Upload, Github, Database, Send, Loader2, CheckCircle, AlertCircle } from 'lucide-react'
import {useUserContext} from '@/app/block/context/UserContext'
import { Key } from '@/app/block/key'
import { web3Enable, web3FromAddress } from '@polkadot/extension-dapp'
import { stringToU8a, u8aToHex } from '@polkadot/util'



export const CreateMod = ( ) => {
  const { client, localKey } = useUserContext()
  const [isSubwalletEnabled, setIsSubwalletEnabled] = useState(false)
  const [modUrl, setModUrl] = useState('')
  const [modName, setModName] = useState('')
  const [collateral, setCollateral] = useState(0.0)
  const [isLoading, setIsLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)
  const [signatureInfo, setSignatureInfo] = useState<{signature: string, timestamp: number, address: string} | null>(null)
  const [isLocalWallet, setIsLocalWallet] = useState(false)
  const [walletAddress, setWalletAddress] = useState('')
  const [regInfo, setRegInfo] = useState<string>('')

  useEffect(() => {
    const walletMode = localStorage.getItem('wallet_mode')
    const address = localStorage.getItem('wallet_address')
    const password = localStorage.getItem('wallet_password')
    
    setIsSubwalletEnabled(walletMode === 'subwallet')
    setIsLocalWallet(walletMode === 'local')
    setWalletAddress(address || '')
    
  }, [client])


  const handleUrlChange = (e) => {
    const value = e.target.value || '';
    setModUrl(value);

  if (!client) {
        throw new Error('Client not initialized')
      }

    const namePart = value.split('/')[value.split('/').length - 1];
    setModName(namePart);

  };

  const handleNameChange = (e) => {
    setModName(e.target.value || '');
  };

  const generateRegInfo = async () => {
    if (!modUrl.trim()) {
      setError('Please enter a valid URL or IPFS hash')
      return
    }

    try {
      if (!client) {
        throw new Error('Client not initialized')
      }
      let signature: string
      let signerAddress: string
      let mod_preview: any
      if (isSubwalletEnabled && walletAddress) {

        mod_preview = await client.call('mod_preview', {'url': modUrl.trim(), 'key':walletAddress , 'collateral': collateral})
        let messageToSign = JSON.stringify(mod_preview)

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
        if (!localKey) {
          throw new Error('Local key not found. Please sign in with Local Key.')
        }
        mod_preview = await client.call('mod_preview', {'url': modUrl.trim(), 'key':localKey.address , 'collateral': collateral})
        let messageToSign = JSON.stringify(mod_preview)
        signature = localKey.sign(messageToSign)
        signerAddress = localKey.address
        console.log('Signed with local key:', { signature, signerAddress })
      } else {
        throw new Error('No signing method available. Please connect SubWallet or sign in with Local Key first.')
      }
      const previewData = {
        ...mod_preview,
        signature: signature,
      }
        
      setRegInfo(JSON.stringify(previewData, null, 2))
      setError(null)
    } catch (err: any) {
      console.error('Preview generation error:', err)
      setError(err.message || 'Failed to generate preview')
      setRegInfo('')
    }
  }

  const handleCreateModule = async () => {
    if (!modUrl.trim()) {
      setError('Please enter a valid URL or IPFS hash')
      return
    }

    setIsLoading(true)
    setError(null)
    setSuccess(null)
    setSignatureInfo(null)


    try {
      const timestamp = Date.now()
      const messageToSign = `${modUrl.trim()}:${timestamp}`
      let signature: string
      let signerAddress: string
      let wallet_mode = localStorage.getItem('wallet_mode') || 'local'
      if (wallet_mode === 'subwallet' && walletAddress) {
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
      } else if (wallet_mode === 'local' && localKey) {
        signature = localKey.sign(messageToSign)
        signerAddress = localKey.address
        console.log('Signed with local key:', { signature, signerAddress })
      } else {
        throw new Error('No signing method available. Please connect SubWallet or sign in with Local Key first.')
      }

      const params = {
        url: modUrl.trim(),
        name: modName.trim() || undefined,
        collateral: collateral,
        signature: signature,
        timestamp: timestamp,
        key: signerAddress
      }
      const regInfoObj = regInfo ? JSON.parse(regInfo) : null 
      if (!client) {
        throw new Error('Client not initialized')
      }
      const response = await client.call('reg', {mod: regInfoObj} )
      
      setSignatureInfo({
        signature: signature,
        timestamp: timestamp,
        address: signerAddress
      })
      
      setSuccess(`Module created successfully! Response: ${JSON.stringify(response)}`)
      setModUrl('')
      setModName('')
      setCollateral(0.0)
      setRegInfo('')
    } catch (err: any) {
      console.error('Module creation error:', err)
      setError(err.message || 'Failed to create module')
    } finally {
      setIsLoading(false)
    }
  }

  return (
    <div className="space-y-6 animate-fadeIn">
      {(isSubwalletEnabled || isLocalWallet) && (
        <div className="p-3 rounded-lg bg-gradient-to-br from-purple-500/10 to-transparent border border-purple-500/30">
          <div className="flex items-center gap-2 text-purple-400 text-sm font-mono">
            <CheckCircle size={16} />
            <span>{isSubwalletEnabled ? 'SUBWALLET MODE' : 'LOCAL WALLET MODE'} - Signing with {isSubwalletEnabled ? 'extension' : 'local key'}</span>
          </div>
          {walletAddress && (
            <div className="text-purple-500/70 text-xs font-mono mt-1">
              {walletAddress.slice(0, 8)}...{walletAddress.slice(-6)}
            </div>
          )}
        </div>
      )}

      {!isSubwalletEnabled && !isLocalWallet && (
        <div className="p-4 rounded-lg bg-gradient-to-br from-red-500/10 to-transparent border border-red-500/30">
          <div className="flex items-center gap-2 text-red-400 text-sm font-mono">
            <AlertCircle size={16} />
            <span>NO WALLET CONNECTED - Please sign in with SubWallet or Local Key to create modules</span>
          </div>
        </div>
      )}

      <div className="space-y-3 p-4 rounded-lg bg-gradient-to-br from-purple-500/5 to-transparent border border-purple-500/20">
        <div className="flex items-center gap-2 text-purple-500/70 text-sm font-mono uppercase mb-3">
          <Package size={16} />
          <span>CREATE MODULE</span>
        </div>

        <div className="space-y-4">
          <div>
            <label className="text-xs text-purple-500/70 font-mono uppercase mb-2 block flex items-center gap-2">
              <span>Github or Mod Ipfs URL</span>
            </label>
            <div className="relative">
              <input
                type="text"
                value={modUrl}
                onChange={(e) => { handleUrlChange(e) }}
                placeholder="https://github.com/user/repo or ipfs://Qm..."
                className="w-full bg-black/50 border border-purple-500/30 rounded px-3 py-3 pl-10 text-purple-400 font-mono text-sm placeholder-purple-600/50 focus:outline-none focus:border-purple-500"
              />
              <div className="absolute left-3 top-1/2 -translate-y-1/2 text-purple-500/50">
                {modUrl.includes('github') ? <Github size={16} /> : <Database size={16} />}
              </div>
            </div>
          </div>

          <div>
            <label className="text-xs text-purple-500/70 font-mono uppercase mb-2 block">
              Module Name
            </label>
            <div className="relative">
              <input
                type="text"
                value={modName}
                onChange={handleNameChange}
                placeholder="Enter module name"
                className="w-full bg-black/50 border border-purple-500/30 rounded px-3 py-3 pl-10 text-purple-400 font-mono text-sm placeholder-purple-600/50 focus:outline-none focus:border-purple-500"
              />
              <div className="absolute left-3 top-1/2 -translate-y-1/2 text-purple-500/50">
                <Package size={16} />
              </div>
            </div>
          </div>

          <div>
            <label className="text-xs text-purple-500/70 font-mono uppercase mb-2 block">
              Collateral (Float)
            </label>
            <div className="relative">
              <input
                type="number"
                step="0.01"
                value={collateral}
                onChange={(e) => setCollateral(parseFloat(e.target.value) || 0.0)}
                placeholder="0.00"
                className="w-full bg-black/50 border border-purple-500/30 rounded px-3 py-3 pl-10 text-purple-400 font-mono text-sm placeholder-purple-600/50 focus:outline-none focus:border-purple-500"
              />
              <div className="absolute left-3 top-1/2 -translate-y-1/2 text-purple-500/50">
                <Database size={16} />
              </div>
            </div>
          </div>

          <button
            onClick={generateRegInfo}
            disabled={!modUrl.trim() || (!isSubwalletEnabled && !isLocalWallet)}
            className="w-full py-3 border border-blue-500/50 text-blue-400 hover:bg-blue-500/10 hover:border-blue-500 transition-all rounded-lg font-mono uppercase disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2 group"
          >
            <CheckCircle size={20} className="group-hover:scale-110 transition-transform" />
            <span>Preview JSON Before Submit</span>
          </button>

          {regInfo && (
            <div className="p-4 rounded-lg bg-gradient-to-br from-blue-500/5 to-transparent border border-blue-500/20">
              <div className="flex items-center gap-2 text-blue-500/70 text-sm font-mono uppercase mb-2">
                <CheckCircle size={16} />
                <span>JSON Preview - Verify Before Submitting</span>
              </div>
              <pre className="text-blue-400 font-mono text-xs overflow-x-auto bg-black/50 p-3 rounded border border-blue-500/20">
                {regInfo}
              </pre>
            </div>
          )}

          <button
            onClick={handleCreateModule}
            disabled={!modUrl.trim() || isLoading || (!isSubwalletEnabled && !isLocalWallet)}
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

      {signatureInfo && (
        <div className="p-4 rounded-lg bg-gradient-to-br from-blue-500/5 to-transparent border border-blue-500/20">
          <div className="flex items-center gap-2 text-blue-500/70 text-sm font-mono uppercase mb-2">
            <CheckCircle size={16} />
            <span>Signature Details</span>
          </div>
          <div className="space-y-2 text-blue-400 font-mono text-xs">
            <div>
              <span className="text-blue-500/70">Address:</span>
              <p className="break-all">{signatureInfo.address}</p>
            </div>
            <div>
              <span className="text-blue-500/70">Timestamp:</span>
              <p>{new Date(signatureInfo.timestamp).toISOString()}</p>
            </div>
            <div>
              <span className="text-blue-500/70">Signature:</span>
              <p className="break-all">{signatureInfo.signature}</p>
            </div>
          </div>
        </div>
      )}

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

export default CreateMod