import React, { useState, useEffect } from 'react';
import { UserType } from '@/bloc/types';

interface UserProfileProps {
  user: UserType;
  isOpen: boolean;
  onClose: () => void;
  keyInstance: any;
}

export default function UserProfile({ user, isOpen, onClose, keyInstance }: UserProfileProps) {
  const [message, setMessage] = useState('');
  const [signature, setSignature] = useState('');
  const [verifyMessage, setVerifyMessage] = useState('');
  const [verifySignature, setVerifySignature] = useState('');
  const [verifyPublicKey, setVerifyPublicKey] = useState('');
  const [verifyResult, setVerifyResult] = useState<string | null>(null);
  const [signResult, setSignResult] = useState<string | null>(null);
  const [autoVerifyResult, setAutoVerifyResult] = useState<string | null>(null);

  // Auto-verify whenever we sign something
  useEffect(() => {
    if (signature && message && keyInstance) {
      const publicKey = keyInstance.publicKey || keyInstance.public_key || user.key;
      try {
        const isValid = keyInstance.verify(message, signature, publicKey);
        setAutoVerifyResult(isValid ? '✓ AUTO-VERIFIED: Signature is valid!' : '✗ AUTO-VERIFY FAILED: Signature invalid!');
      } catch (error) {
        setAutoVerifyResult(`✗ AUTO-VERIFY ERROR: ${error}`);
      }
    } else {
      setAutoVerifyResult(null);
    }
  }, [signature, message, keyInstance, user.key]);

  const handleSign = () => {
    if (!message.trim()) {
      setSignResult('✗ ERROR: Please enter a message to sign');
      return;
    }
    
    try {
      const sig = keyInstance.sign(message);
      setSignature(sig);
      setSignResult('✓ Message signed successfully!');
    } catch (error) {
      setSignResult(`✗ ERROR: ${error}`);
      setSignature('');
    }
  };

  const handleVerify = () => {
    if (!verifyMessage.trim() || !verifySignature.trim() || !verifyPublicKey.trim()) {
      setVerifyResult('✗ ERROR: All fields required for verification');
      return;
    }

    try {
      const isValid = keyInstance.verify(verifyMessage, verifySignature, verifyPublicKey);
      setVerifyResult(isValid ? '✓ VERIFIED: Signature is valid!' : '✗ INVALID: Signature does not match!');
    } catch (error) {
      setVerifyResult(`✗ ERROR: ${error}`);
    }
  };

  const copyToClipboard = (text: string) => {
    navigator.clipboard.writeText(text);
  };

  const useMyPublicKey = () => {
    const publicKey = keyInstance?.publicKey || keyInstance?.public_key || user.key;
    setVerifyPublicKey(publicKey);
  };

  if (!isOpen) return null;

  return (
    <>
      {/* Backdrop */}
      <div 
        className="fixed inset-0 bg-black/50 z-40 transition-opacity"
        onClick={onClose}
      />
      
      {/* Panel */}
      <div className="fixed right-0 top-0 h-full w-full md:w-[600px] bg-black border-l-2 border-green-500 z-50 overflow-y-auto animate-slide-in">
        <div className="p-6 font-mono text-green-500">
          {/* Header */}
          <div className="flex justify-between items-center mb-6 pb-4 border-b border-green-500">
            <h2 className="text-2xl font-bold">$ USER_PROFILE</h2>
            <button 
              onClick={onClose}
              className="text-green-500 hover:text-green-300 text-2xl font-bold"
            >
              ✕
            </button>
          </div>

          {/* User Info */}
          <div className="mb-8 space-y-2">
            <div className="flex items-center gap-2">
              <span className="text-green-300">ADDRESS:</span>
              <span className="text-sm break-all">{user.key}</span>
              <button 
                onClick={() => copyToClipboard(user.key)}
                className="text-green-300 hover:text-green-100 ml-2"
              >
                📋
              </button>
            </div>
            <div>
              <span className="text-green-300">CRYPTO_TYPE:</span> {user.crypto_type || 'UNKNOWN'}
            </div>
            <div>
              <span className="text-green-300">BALANCE:</span> {user.balance || 0}
            </div>
          </div>

          {/* SIGN SECTION */}
          <div className="mb-8 p-4 border border-green-500 rounded">
            <h3 className="text-xl mb-4 text-green-300">$ SIGN MESSAGE</h3>
            <p className="text-sm mb-4 text-green-400">
              ⚠️ Sign messages to prove ownership of your private key. 
              The signature will be AUTO-VERIFIED immediately to ensure integrity.
            </p>
            
            <div className="space-y-4">
              <div>
                <label className="bloc mb-2 text-green-300">MESSAGE TO SIGN:</label>
                <textarea
                  value={message}
                  onChange={(e) => setMessage(e.target.value)}
                  className="w-full bg-black border border-green-500 text-green-500 p-2 rounded font-mono"
                  rows={3}
                  placeholder="Enter your message here..."
                />
              </div>

              <button
                onClick={handleSign}
                className="w-full bg-green-500 text-black py-2 px-4 rounded font-bold hover:bg-green-400 transition"
              >
                $ SIGN
              </button>

              {signResult && (
                <div className={`p-2 rounded ${signResult.startsWith('✓') ? 'bg-green-900/30 text-green-300' : 'bg-red-900/30 text-red-300'}`}>
                  {signResult}
                </div>
              )}

              {signature && (
                <div>
                  <label className="bloc mb-2 text-green-300">SIGNATURE OUTPUT:</label>
                  <div className="relative">
                    <textarea
                      value={signature}
                      readOnly
                      className="w-full bg-black border border-green-500 text-green-500 p-2 rounded font-mono text-xs"
                      rows={4}
                    />
                    <button
                      onClick={() => copyToClipboard(signature)}
                      className="absolute top-2 right-2 text-green-300 hover:text-green-100"
                    >
                      📋 COPY
                    </button>
                  </div>
                </div>
              )}

              {autoVerifyResult && (
                <div className={`p-3 rounded border-2 font-bold ${autoVerifyResult.startsWith('✓') ? 'bg-green-900/50 border-green-400 text-green-300' : 'bg-red-900/50 border-red-400 text-red-300'}`}>
                  🔒 {autoVerifyResult}
                  <p className="text-xs mt-1 font-normal">
                    This signature was automatically verified against your public key to prevent tampering.
                  </p>
                </div>
              )}
            </div>
          </div>

          {/* VERIFY SECTION */}
          <div className="mb-8 p-4 border border-green-500 rounded">
            <h3 className="text-xl mb-4 text-green-300">$ VERIFY SIGNATURE</h3>
            <p className="text-sm mb-4 text-green-400">
              🔍 Verify any signature to ensure it was created by the claimed public key.
              This prevents sneaky attacks where someone tries to replace signatures.
            </p>

            <div className="space-y-4">
              <div>
                <label className="bloc mb-2 text-green-300">ORIGINAL MESSAGE:</label>
                <textarea
                  value={verifyMessage}
                  onChange={(e) => setVerifyMessage(e.target.value)}
                  className="w-full bg-black border border-green-500 text-green-500 p-2 rounded font-mono"
                  rows={3}
                  placeholder="Enter the original message..."
                />
              </div>

              <div>
                <label className="bloc mb-2 text-green-300">SIGNATURE TO VERIFY:</label>
                <textarea
                  value={verifySignature}
                  onChange={(e) => setVerifySignature(e.target.value)}
                  className="w-full bg-black border border-green-500 text-green-500 p-2 rounded font-mono text-xs"
                  rows={4}
                  placeholder="Paste signature here..."
                />
              </div>

              <div>
                <div className="flex justify-between items-center mb-2">
                  <label className="text-green-300">PUBLIC KEY:</label>
                  <button
                    onClick={useMyPublicKey}
                    className="text-xs bg-green-700 text-white py-1 px-2 rounded hover:bg-green-600"
                  >
                    USE MY KEY
                  </button>
                </div>
                <input
                  type="text"
                  value={verifyPublicKey}
                  onChange={(e) => setVerifyPublicKey(e.target.value)}
                  className="w-full bg-black border border-green-500 text-green-500 p-2 rounded font-mono text-xs"
                  placeholder="Enter public key..."
                />
              </div>

              <button
                onClick={handleVerify}
                className="w-full bg-green-500 text-black py-2 px-4 rounded font-bold hover:bg-green-400 transition"
              >
                $ VERIFY
              </button>

              {verifyResult && (
                <div className={`p-3 rounded border-2 font-bold ${verifyResult.startsWith('✓') ? 'bg-green-900/50 border-green-400 text-green-300' : 'bg-red-900/50 border-red-400 text-red-300'}`}>
                  {verifyResult}
                </div>
              )}
            </div>
          </div>

          {/* Info Section */}
          <div className="p-4 bg-green-900/20 border border-green-500 rounded">
            <h4 className="font-bold mb-2 text-green-300">🛡️ SECURITY INFO</h4>
            <ul className="text-xs space-y-1 text-green-400">
              <li>• Every signature is AUTO-VERIFIED immediately after signing</li>
              <li>• This prevents tampering or replacement attacks</li>
              <li>• Always verify signatures from others before trusting them</li>
              <li>• Your private key never leaves your device</li>
              <li>• Signatures are cryptographically secure</li>
            </ul>
          </div>
        </div>
      </div>

      <style jsx>{`
        @keyframes slide-in {
          from {
            transform: translateX(100%);
          }
          to {
            transform: translateX(0);
          }
        }
        .animate-slide-in {
          animation: slide-in 0.3s ease-out;
        }
      `}</style>
    </>
  );
}