"use client";

import { WalletAdapter } from '../types'
import { cryptoWaitReady } from '@polkadot/util-crypto'
// dynamic import: @polkadot/extension-dapp accesses window at load time
import { stringToU8a, u8aToHex } from '@polkadot/util'
import { signatureVerify } from '@polkadot/util-crypto'

export class SubwalletAdapter implements WalletAdapter {
  private async polkadotDapp() {
    return await import('@polkadot/extension-dapp')
  }

  async connect(): Promise<void> {
    await cryptoWaitReady()
    const { web3Enable } = await this.polkadotDapp()
    const extensions = await web3Enable('MOD')
    if (extensions.length === 0) throw new Error('No extension found')
  }

  async disconnect(): Promise<void> {
    localStorage.removeItem('wallet_mode')
    localStorage.removeItem('wallet_address')
    localStorage.removeItem('wallet_type')
  }

  async signIn(): Promise<void> {
    const address = localStorage.getItem('wallet_address')
    if (!address) throw new Error('No account selected')
    localStorage.setItem('wallet_mode', 'subwallet')
  }

  async isAvailable(): Promise<boolean> {
    const { web3Enable } = await this.polkadotDapp()
    const extensions = await web3Enable('MOD')
    return extensions.length > 0
  }

  async getAccounts(): Promise<any[]> {
    const { web3Accounts } = await this.polkadotDapp()
    return await web3Accounts()
  }

  async sign(message: string): Promise<string> {
    const address = localStorage.getItem('wallet_address')
    if (!address) throw new Error('No wallet address')
    
    const { web3Enable, web3FromAddress } = await this.polkadotDapp()
    const extensions = await web3Enable('MOD')
    if (extensions.length === 0) throw new Error('No wallet extension found')

    const injector = await web3FromAddress(address)
    if (!injector.signer.signRaw) throw new Error('Wallet does not support signing')
    
    const signRaw = injector.signer.signRaw
    const { signature } = await signRaw({
      address: address,
      data: u8aToHex(stringToU8a(message)),
      type: 'bytes'
    })
    
    return signature
  }

  async verify(message: string, signature: string, publicKey: string): Promise<boolean> {
    const verification = signatureVerify(
      u8aToHex(stringToU8a(message)),
      signature,
      publicKey
    )
    return verification.isValid
  }
}
