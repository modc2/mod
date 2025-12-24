import { createHash } from 'crypto';
import {Key} from '@/bloc/key';

export interface AuthData {
  data: string;
  time: string;
  key: string;
  signature: string;
}
export interface AuthHeaders {
  token: string;
}

export class Auth {
  private key: Key;
  private maxAge: number;
  private signatureKeys: string[];

  /**
   * Initialize the Auth class
   * @param key - The key to use for signing
   * @param maxAge - Maximum staleness allowed for timestamps (in seconds)
   * @param signatureKeys - The keys to use for signing
   */
  constructor(
    key: Key | undefined,
    maxAge: number = 3600,
    signatureKeys: string[] = ['data', 'time', 'cost']
  ) {
    if (!key) {
      throw new Error('Key is required for Auth');
    }
    this.key = key;
    this.maxAge = maxAge;
    this.signatureKeys = signatureKeys;
  }

  /**
   * Generate authentication headers with signature
   * @param data - The data to sign
   * @param key - Optional key override
   * @returns Authentication headers with signature
   */

  public base64encode(data: string): string {
    return Buffer.from(data).toString('base64url');

  }
  public base64decode(data: string): string {
    return Buffer.from(data, 'base64url').toString('utf-8');
  }
  public generate(data: any, cost: number = 10): AuthHeaders {
    const authData: AuthData = {
      data: this.hash(data),
      time: String(this.time()), // Unix timestamp in seconds
      key: this.key.address,
      signature: '',
    };

    // Create signature data object with only the specified keys
    let signatureData: Record<string, string> = {};
    this.signatureKeys.forEach(k => {
      if (k in headers) {
        signatureData[k] = authData[k as keyof AuthData] as string;
      }
    });
    // Sign the data
    let signatureDataString = JSON.stringify(signatureData); // Ensure it's a plain object
    authData.signature = this.key.sign(signatureDataString)
    const verified = this.key.verify( signatureDataString, authData.signature, authData.key);
    if (!verified) {
      throw new Error('Signature verification failed');
    }
    const headers : AuthHeaders = {token: this.base64encode(JSON.stringify( authData))};
    return headers;
  }

  /**
   * Verify and decode authentication headers
   * @param headers - The headers to verify
   * @param data - Optional data to verify against the hash
   * @returns The verified headers
   * @throws Error if verification fails
   */
  public time(): number {
    return Date.now() / 1000; // Returns current timestamp in seconds
  }

  public verify(headers: AuthHeaders, data?: any): boolean {
    // Check staleness
    const authData = JSON.parse(this.base64decode(headers.token)) as AuthData;
    const currentTime = this.time()
    const headerTime = parseFloat(authData.time);
    const staleness = Math.abs(currentTime - headerTime);
    
    if (staleness > this.maxAge) {
      throw new Error(`Token is stale: ${staleness}s > ${this.maxAge}s`);
    }

    if (!authData.signature) {
      throw new Error('Missing signature');
    }

    // Create signature data object for verification
    const signatureData: Record<string, string> = {};
    this.signatureKeys.forEach(k => {
      if (k in headers) {
        signatureData[k] = headers[k as keyof AuthHeaders] as string;
      }
    });

    const signatureDataString = JSON.stringify(signatureData); // Ensure it's a plain object


    let params = {
      message: signatureDataString,
      signature: authData.signature,
      public_key: authData.key,
    };

    let verified = this.key.verify(params.message, params.signature, params.public_key);

    // get boolean value of verified
    verified = Boolean(verified);

    // Verify data hash if provided
    if (data) {
      const rehashData = this.hash(data);
      if (authData.data !== rehashData) {
        throw new Error(`Invalid data hash: ${authData.data} !== ${rehashData}`);
      }
    }

    return verified;
  }

  /**
   * Hash the data using the specified hash type
   * @param data - The data to hash
   * @returns The hash string
   */
  private hash(data: any): string {
    let dataToHash = JSON.stringify(data);
    let hash =  createHash('sha256').update(dataToHash).digest('hex');
    return hash;
  }


  /**
   * Test the authentication flow
   * @param keyName - Name of the test key
   * @returns Test results
   */
  public static async test(
    key: Key,
  ): Promise<{ headers: boolean; verified: boolean }> {
    const data = { fn: 'test', params: { a: 1, b: 2 } };
    const auth = new Auth(key);
    
    // Generate headers
    const headers = auth.generate(data);
    
    // Verify headers without data
    auth.verify(headers);
    
    // Verify headers with data
    const verifiedHeaders = auth.verify(headers, data);
    
    return { 
      headers: verifiedHeaders,
      verified: true
    };
  }
}

export default Auth;
