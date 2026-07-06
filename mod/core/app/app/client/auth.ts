"use client";
import { createHash } from 'crypto';
import {Key} from '@/key';
// dynamic import: @polkadot/extension-dapp accesses window at load time
import { stringToU8a, u8aToHex } from '@polkadot/util'
import { MetamaskAdapter } from '@/wallet/adapters/MetamaskAdapter'
import { PhantomAdapter } from '@/wallet/adapters/PhantomAdapter'

export interface AuthData {
  data: string;
  time: string;
  key: string;
  signature: string;
  dataHash?: string;
}
export interface AuthHeaders {
  token: string;
  refreshToken?: string;
}

export class Auth {
  private key: Key | null = null;
  private maxAge: number;
  private signatureKeys: string[];
  private tokenCache: { token: string; refreshToken: string; expiresAt: number } | null = null;
  private refreshInterval: number;

  /**
   * Initialize the Auth class
   * @param maxAge - Maximum staleness allowed for timestamps (in seconds)
   * @param signatureKeys - The keys to use for signing
   * @param refreshInterval - Token refresh interval in seconds (default: 86400 = 1 day)
   * @param key - Optional Key instance to use instead of creating one from localStorage
   */
  constructor(
    maxAge: number = 86400,
    signatureKeys: string[] = ['data', 'time'],
    refreshInterval: number = 86400,
    key?: Key
  ) {
    this.maxAge = maxAge;
    this.signatureKeys = signatureKeys;
    this.refreshInterval = refreshInterval;
    if (key) {
      this.key = key;
    } else {
      this.key = null; // Defer localStorage access until needed
    }
  }

  private getKey(): Key {
    if (!this.key) {
      const password = typeof window !== 'undefined' ? 'wefwefewf' : 'wefwefewf';
      this.key = new Key(password);
    }
    return this.key;
  }



  /**
   * Infer the crypto type from a wallet address string.
   * Mirrors server-side detect_address_type logic.
   */
  public inferCryptoType(address: string): 'sr25519' | 'ecdsa' | 'solana' {
    if (!address) return 'ecdsa';
    const a = address.trim();
    // Ethereum: 0x + 40 hex chars
    if (a.length === 42 && /^0x[0-9a-fA-F]{40}$/.test(a)) return 'ecdsa';
    // Substrate SS58: 47-48 base58 chars with common prefix
    const ss58Prefixes = '159DFHJKLMNPQRSTUVWXYZabcdefghij';
    if ((a.length === 47 || a.length === 48) && /^[1-9A-HJ-NP-Za-km-z]{47,48}$/.test(a) && ss58Prefixes.includes(a[0])) {
      return 'sr25519';
    }
    // Solana: base58-encoded 32-byte key (32-44 chars)
    if (/^[1-9A-HJ-NP-Za-km-z]{32,44}$/.test(a)) return 'solana';
    return 'ecdsa';
  }

  public base64urlEncode(data: string | Record<string, unknown> | Uint8Array): string {
    let bytes: Uint8Array;

    if (typeof data === "string") {
      bytes = new TextEncoder().encode(data);
    } else if (data instanceof Uint8Array) {
      bytes = data;
    } else {
      const json = JSON.stringify(data);
      bytes = new TextEncoder().encode(json);
    }

    const base64 = Buffer.from(bytes).toString("base64");

    return base64
      .replace(/\+/g, "-")
      .replace(/\//g, "_")
      .replace(/=+$/, "");
  }

  public base64urlDecode(data: string): Uint8Array {
    const padding = "=".repeat((4 - (data.length % 4)) % 4);

    const base64 = (data + padding)
      .replace(/-/g, "+")
      .replace(/_/g, "/");

    return new Uint8Array(Buffer.from(base64, "base64"));
  }
  
  public hash(data: string): string {
    return createHash('sha256').update(data).digest('hex');
  }

  public signatureData(data: any): string {
    const parts: string[] = [];
    
    this.signatureKeys.forEach(k => {
      if (k in data) {
        const value = data[k as keyof typeof data] as string;
        parts.push(`"${k}":${JSON.stringify(value)}`);
      }
    });
    
    return `{${parts.join(',')}}`;
  }

  public async signWithInjector(signMessage: string, walletAddress: string): Promise<string> {
    const { web3Enable, web3FromAddress } = await import('@polkadot/extension-dapp')
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
    return sig;
  }

  public async signWithMetamask(signMessage: string): Promise<string> {
    const metamaskAdapter = new MetamaskAdapter();
    return await metamaskAdapter.sign(signMessage);
  }

  public async signWithPhantom(signMessage: string): Promise<string> {
    const phantomAdapter = new PhantomAdapter();
    await phantomAdapter.connect();
    return await phantomAdapter.sign(signMessage);
  }

  public async signLocal(signMessage: string, cryptoType?: 'sr25519' | 'ecdsa' | 'solana'): Promise<string> {
    const password = typeof window !== 'undefined' ? localStorage.getItem('wallet_password') : null;
    if (!password) {
      throw new Error('No wallet_password found in localStorage. Please sign in again.');
    }
    const localKey = new Key(password, cryptoType || 'ecdsa');
    return localKey.sign(signMessage);
  }
      
  public async token(data: any = '', walletAddress?: any, wallet_mode?: any): Promise<string> {


    if (!wallet_mode) {
      wallet_mode = typeof window !== 'undefined' ? localStorage.getItem('wallet_mode') : 'local';
    }
    
    let authData: AuthData = {
      data: data || '',
      time: String(this.time()),
      key: walletAddress,
      signature: '',
    };

    let signatureData: string = this.signatureData(authData);
    authData.dataHash = this.hash(signatureData);
    // Sign with appropriate wallet adapter based on wallet mode
    if (wallet_mode === 'metamask') {
      authData.signature = await this.signWithMetamask(signatureData);
    } else if (wallet_mode === 'injector' || wallet_mode === 'subwallet') {
      // Use polkadot extension injector for sr25519
      authData.signature = await this.signWithInjector(signatureData, walletAddress);
    } else if (wallet_mode === 'phantom') {
      authData.signature = await this.signWithPhantom(signatureData);
    } else if (wallet_mode === 'solana') {
      authData.signature = await this.signLocal(signatureData, 'solana');
    } else if (wallet_mode === 'local') {
      // Infer crypto type from wallet address to ensure signature matches
      const cryptoType = this.inferCryptoType(walletAddress);
      const password = typeof window !== 'undefined' ? localStorage.getItem('wallet_password') : null;
      if (!password) {
        throw new Error('No wallet_password found in localStorage. Please sign in again.');
      }
      // Use matching key type so address and signature are consistent
      const localKey = new Key(password, cryptoType);
      authData.key = localKey.address;
      authData.signature = await this.signLocal(signatureData, cryptoType);
    }
    return this.base64urlEncode(JSON.stringify(authData));
  }



  public async generate(data: any, walletAddress?: any): Promise<AuthHeaders> {
    return {
      token: await this.token(data, walletAddress),
    };
  }

  public time(): number {
    return Date.now() / 1000;
  }

  public token2data(token: string): AuthData {
    const decoded = this.base64urlDecode(token);
    const jsonString = new TextDecoder().decode(decoded);
    return JSON.parse(jsonString) as AuthData;
  }

  public verify(headers: AuthHeaders, data?: any): boolean {
    const authData = this.token2data(headers.token);

    if (!authData.signature) {
      throw new Error('Missing signature');
    }
    const staleness = Math.abs(this.time() - parseFloat(authData.time));
    if (staleness > this.maxAge) {
      throw new Error(`Token is stale: ${staleness}s > ${this.maxAge}s`);
    }
    
    const signatureData: Record<string, string> = {};
    this.signatureKeys.forEach(k => {
      if (k in headers) {
        signatureData[k] = headers[k as keyof AuthHeaders] as string;
      }
    });

    const signatureDataString = JSON.stringify(signatureData);

    let params = {
      message: signatureDataString,
      signature: authData.signature,
      public_key: authData.key,
    };

    let verified = this.getKey().verify(params.message, params.signature, params.public_key);

    verified = Boolean(verified);
    return verified;
  }

  /**
   * Test the authentication flow
   * @param key - The Key instance to test with
   * @returns Test results
   */
  public static async test(
    key: Key,
  ): Promise<{ headers: boolean; verified: boolean }> {
    const data = { fn: 'test', params: { a: 1, b: 2 } };
    const auth = new Auth(86400, ['data', 'time'], 86400, key);
    
    const headers = await auth.generate(data);
    
    auth.verify(headers);
    
    const verifiedHeaders = auth.verify(headers);
    
    return { 
      headers: verifiedHeaders,
      verified: true
    };
  }
}

export default Auth;
