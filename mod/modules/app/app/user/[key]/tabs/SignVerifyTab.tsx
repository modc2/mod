'use client'
import { useState, useEffect } from 'react'
import { Copy, CheckCircle, FileSignature } from 'lucide-react'
import { Key } from '@/app/block/key'
import { copyToClipboard } from '@/app/utils'
import { web3Enable, web3FromAddress } from '@polkadot/extension-dapp'
import { stringToU8a, u8aToHex } from '@polkadot/util'
import { signatureVerify } from '@polkadot/util-crypto'

interface SignVerifyTabProps {
  keyInstance: Key
}

export const SignVerifyTab = ({ keyInstance }: SignVerifyTabProps) => {
  const [copiedField, setCopiedField] = useState<string | null>(null)
  const [signMessage, setSignMessage] = useState('')
  const [signature, setSignature] = useState('')
  const [verifyMessage, setVerifyMessage] = useState('')
  const [verifySignature, setVerifySignature] = useState('')
  const [verifyPublicKey, setVerifyPublicKey] = useState('')
  const [verifyResult, setVerifyResult] = useState<boolean | null>(null)
  const [isSubwalletEnabled, setIsSubwalletEnabled] = useState(false)
  const [isLocalWallet, setIsLocalWallet] = useState(false)
  const [walletAddress, setWalletAddress] = useState('')

  useEffect(() => {
    const checkWalletMode = () => {
      const walletMode = localStorage.getItem('wallet_mode')
      const address = localStorage.getItem('wallet_address')
      setIsSubwalletEnabled(walletMode === 'subwallet')
      setIsLocalWallet(walletMode === 'local')
      setWalletAddress(address || '')
    }
    checkWalletMode()
  }, [])

  const handleSign = async () => {
    if (!signMessage) return
    
    try {
      if (isSubwalletEnabled && walletAddress) {
        const extensions = await web3Enable('MOD')
        if (extensions.length === 0) {
          throw new Error('No wallet extension found')
        }
        
        const injector = await web3FromAddress(walletAddress)
        if (!injector.signer.signRaw) {
          throw new Error('Wallet does not support signing')
        }
        
        const signRaw = injector.signer.signRaw
        const { signature: sig } = await signRaw({
          address: walletAddress,
          data: u8aToHex(stringToU8a(signMessage)),
          type: 'bytes'
        })
        
        setSignature(sig)
        setVerifyMessage(signMessage)
        setVerifySignature(sig)
        setVerifyPublicKey(walletAddress)
      } else if (isLocalWallet && keyInstance) {
        const sig = await keyInstance.sign(signMessage)
        setSignature(sig)
        setVerifyMessage(signMessage)
        setVerifySignature(sig)
        setVerifyPublicKey(keyInstance.public_key)
      } else {
        throw new Error('No wallet enabled')
      }
    } catch (error) {
      console.error('Error signing message:', error)
      if (error instanceof Error) {
        alert(`Failed to sign: ${error.message}`)
      }
    }
  }

  const handleVerify = async () => {
    if (!verifyMessage || !verifySignature || !verifyPublicKey) return
    try {
      let result = false
      
      if (isSubwalletEnabled || verifySignature.startsWith('0x')) {
        // Use Polkadot signature verification for SubWallet signatures
        const verification = signatureVerify(
          u8aToHex(stringToU8a(verifyMessage)),
          verifySignature,
          verifyPublicKey
        )
        result = verification.isValid
      } else if (keyInstance) {
        // Use local key verification for local wallet signatures
        result = await keyInstance.verify(verifyMessage, verifySignature, verifyPublicKey)
      }
      
      setVerifyResult(result)
    } catch (error) {
      console.error('Error verifying signature:', error)
      setVerifyResult(false)
    }
  }

  const canSign = (isSubwalletEnabled && walletAddress) || (isLocalWallet && keyInstance)

  return (
    <div className="space-y-6 animate-fadeIn">
      {(isSubwalletEnabled || isLocalWallet) && (
        <div className="p-3 rounded-lg bg-gradient-to-br from-purple-500/10 to-transparent border border-purple-500/30">
          <div className="flex items-center gap-2 text-purple-400 text-sm font-mono">
            <CheckCircle size={16} />
            <span>{isSubwalletEnabled ? 'SUBWALLET MODE ENABLED' : 'LOCAL WALLET MODE ENABLED'} - Signing with {isSubwalletEnabled ? 'extension' : 'local key'}</span>
          </div>
        </div>
      )}

      <div className="space-y-3 p-4 rounded-lg bg-gradient-to-br from-green-500/5 to-transparent border border-green-500/20">
        <div className="flex items-center gap-2 text-green-500/70 text-sm font-mono uppercase mb-3">
          <FileSignature size={16} />
          <span>SIGN MESSAGE</span>
        </div>
        <textarea
          value={signMessage}
          onChange={(e) => setSignMessage(e.target.value)}
          placeholder="Enter message to sign..."
          className="w-full h-24 bg-black/50 border border-green-500/30 rounded-lg p-3 text-green-400 font-mono text-sm placeholder-green-600/50 focus:outline-none focus:border-green-500 transition-all"
        />
        <button
          onClick={handleSign}
          disabled={!signMessage || !canSign}
          className="w-full py-2 border border-green-500/50 text-green-400 hover:bg-green-500/10 hover:border-green-500 transition-all rounded-lg font-mono uppercase disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {isSubwalletEnabled ? 'Sign with SubWallet' : isLocalWallet ? 'Sign with Local Key' : 'Sign Message'}
        </button>
        {signature && (
          <div className="space-y-2 mt-4 p-3 bg-green-500/5 rounded-lg border border-green-500/20">
            <div className="text-green-500/70 text-sm font-mono uppercase">Signature:</div>
            <div className="flex items-center gap-2">
              <code className="flex-1 text-green-400 font-mono text-xs break-all bg-black/50 p-3 border border-green-500/30 rounded-lg">
                {signature}
              </code>
              <button
                onClick={() => {
                  copyToClipboard(signature)
                  setCopiedField('signature')
                  setTimeout(() => setCopiedField(null), 2000)
                }}
                className={`p-3 border rounded-lg transition-all ${
                  copiedField === 'signature'
                    ? 'border-green-400 bg-green-500/20 text-green-400'
                    : 'border-green-500/30 text-green-500 hover:bg-green-500/10 hover:border-green-500'
                }`}
                title="Copy signature"
              >
                {copiedField === 'signature' ? <CheckCircle size={16} /> : <Copy size={16} />}
              </button>
            </div>
          </div>
        )}
      </div>

      <div className="space-y-3 p-4 rounded-lg bg-gradient-to-br from-green-500/5 to-transparent border border-green-500/20">
        <div className="flex items-center gap-2 text-green-500/70 text-sm font-mono uppercase mb-3">
          <CheckCircle size={16} />
          <span>VERIFY SIGNATURE</span>
        </div>
        <textarea
          value={verifyMessage}
          onChange={(e) => setVerifyMessage(e.target.value)}
          placeholder="Enter message to verify..."
          className="w-full h-20 bg-black/50 border border-green-500/30 rounded-lg p-3 text-green-400 font-mono text-sm placeholder-green-600/50 focus:outline-none focus:border-green-500 transition-all"
        />
        <input
          type="text"
          value={verifySignature}
          onChange={(e) => setVerifySignature(e.target.value)}
          placeholder="Enter signature..."
          className="w-full bg-black/50 border border-green-500/30 rounded-lg p-3 text-green-400 font-mono text-sm placeholder-green-600/50 focus:outline-none focus:border-green-500 transition-all"
        />
        <input
          type="text"
          value={verifyPublicKey}
          onChange={(e) => setVerifyPublicKey(e.target.value)}
          placeholder="Enter public key/address..."
          className="w-full bg-black/50 border border-green-500/30 rounded-lg p-3 text-green-400 font-mono text-sm placeholder-green-600/50 focus:outline-none focus:border-green-500 transition-all"
        />
        <button
          onClick={handleVerify}
          disabled={!verifyMessage || !verifySignature || !verifyPublicKey}
          className="w-full py-2 border border-green-500/50 text-green-400 hover:bg-green-500/10 hover:border-green-500 transition-all rounded-lg font-mono uppercase disabled:opacity-50 disabled:cursor-not-allowed"
        >
          Verify Signature
        </button>
        {verifyResult !== null && (
          <div className={`text-center p-3 border rounded-lg font-mono transition-all ${
            verifyResult 
              ? 'border-green-500 bg-green-500/10 text-green-400' 
              : 'border-red-500 bg-red-500/10 text-red-400'
          }`}>
            {verifyResult ? '✓ SIGNATURE VALID' : '✗ SIGNATURE INVALID'}
          </div>
        )}
      </div>
    </div>
  )
}