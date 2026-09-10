/**
 * Browser-side HyperEVM — chain switching, ERC-20 transfers, personal_sign.
 *
 * The pool's money moves on Hyperliquid's EVM (chain 999), which most wallets
 * do not ship a preset for. `ensureHyperEVM` switches to it and adds it if the
 * wallet has never seen it, so a depositor never has to configure a network by
 * hand before they can use the app.
 */

import { ethers } from 'ethers'

export const HYPEREVM = {
  chainId: 999,
  chainIdHex: '0x3e7',
  name: 'HyperEVM',
  rpc: 'https://rpc.hyperliquid.xyz/evm',
  explorer: 'https://hyperevmscan.io',
  currency: { name: 'HYPE', symbol: 'HYPE', decimals: 18 },
}

export const ERC20 = [
  'function balanceOf(address) view returns (uint256)',
  'function decimals() view returns (uint8)',
  'function symbol() view returns (string)',
  'function transfer(address to, uint256 value) returns (bool)',
]

/** An ethers provider over whatever wallet wagmi handed us. */
export async function browserProvider(walletClient?: any) {
  const eip1193 = walletClient ?? (typeof window !== 'undefined' ? (window as any).ethereum : null)
  if (!eip1193) throw new Error('No wallet connected')
  return new ethers.BrowserProvider(eip1193 as any)
}

export async function currentChainId(walletClient?: any): Promise<number | null> {
  try {
    const provider = await browserProvider(walletClient)
    return Number((await provider.getNetwork()).chainId)
  } catch {
    return null
  }
}

/** Switch the wallet to HyperEVM, adding the network if it is unknown. */
export async function ensureHyperEVM(walletClient?: any) {
  const provider = await browserProvider(walletClient)
  const net = await provider.getNetwork()
  if (Number(net.chainId) === HYPEREVM.chainId) return provider

  try {
    await provider.send('wallet_switchEthereumChain', [{ chainId: HYPEREVM.chainIdHex }])
  } catch (err: any) {
    // 4902 = the wallet has never heard of this chain; offer it.
    if (err?.code === 4902 || err?.error?.code === 4902 || /Unrecognized chain/i.test(err?.message || '')) {
      await provider.send('wallet_addEthereumChain', [{
        chainId: HYPEREVM.chainIdHex,
        chainName: HYPEREVM.name,
        nativeCurrency: HYPEREVM.currency,
        rpcUrls: [HYPEREVM.rpc],
        blockExplorerUrls: [HYPEREVM.explorer],
      }])
    } else {
      throw err
    }
  }
  return browserProvider(walletClient)
}

/** Send stablecoin to the vault. Returns the hash the API credits against. */
export async function depositToVault(walletClient: any, token: string, decimals: number,
                                     vault: string, amount: string): Promise<string> {
  const provider = await ensureHyperEVM(walletClient)
  const signer = await provider.getSigner()
  const erc20 = new ethers.Contract(token, ERC20, signer)
  const tx = await erc20.transfer(vault, ethers.parseUnits(amount, decimals))
  return tx.hash
}

/** What the wallet actually holds on HyperEVM, for the deposit form's MAX. */
export async function walletBalance(walletClient: any, token: string, decimals: number,
                                    owner: string): Promise<number> {
  const provider = await browserProvider(walletClient)
  const erc20 = new ethers.Contract(token, ERC20, provider)
  return Number(ethers.formatUnits(await erc20.balanceOf(owner), decimals))
}

/**
 * Sign a pool action. This is a plain personal_sign of readable text — the
 * wallet dialog shows the amount and the asset, so nobody is approving a blob.
 */
export async function signAction(walletClient: any, message: string): Promise<string> {
  const provider = await browserProvider(walletClient)
  const signer = await provider.getSigner()
  return signer.signMessage(message)
}

export function txUrl(hash: string) {
  return `${HYPEREVM.explorer}/tx/${hash}`
}

export function addressUrl(address: string) {
  return `${HYPEREVM.explorer}/address/${address}`
}
