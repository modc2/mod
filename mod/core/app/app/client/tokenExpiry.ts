"use client";
import { Auth } from './auth';
import { toast } from 'react-toastify';

export class TokenExpiryHandler {
  private auth: Auth;
  private checkInterval: NodeJS.Timeout | null = null;
  private onExpiry: () => void;
  private walletMode: string;

  constructor(
    auth: Auth,
    onExpiry: () => void,
    private checkIntervalMs: number = 60000 // Check every minute
  ) {
    this.auth = auth;
    this.onExpiry = onExpiry;
    this.walletMode = typeof localStorage !== 'undefined' 
      ? localStorage.getItem('wallet_mode') || 'local'
      : 'local';
  }

  /**
   * Start monitoring token expiry
   */
  public startMonitoring(): void {
    this.stopMonitoring();
    
    this.checkInterval = setInterval(() => {
      this.checkTokenExpiry();
    }, this.checkIntervalMs);

    // Also check immediately
    this.checkTokenExpiry();
  }

  /**
   * Stop monitoring token expiry
   */
  public stopMonitoring(): void {
    if (this.checkInterval) {
      clearInterval(this.checkInterval);
      this.checkInterval = null;
    }
  }

  /**
   * Check if token is expired and trigger appropriate action
   */
  private checkTokenExpiry(): void {
    if (typeof localStorage === 'undefined') return;

    const token = localStorage.getItem('wallet_token');
    if (!token) {
      this.handleExpiredToken();
      return;
    }

    try {
      const isExpired = this.isTokenExpired(token);
      if (isExpired) {
        this.handleExpiredToken();
      }
    } catch (error) {
      console.error('Error checking token expiry:', error);
      this.handleExpiredToken();
    }
  }

  private hasNotifiedExpiry = false;

  /**
   * Handle expired token — attempt auto-refresh for all wallet types,
   * only prompt user if refresh fails
   */
  private handleExpiredToken(): void {
    this.autoRefreshToken();
  }

  /**
   * Auto-refresh token for any wallet type, prompt user on failure
   */
  private async autoRefreshToken(): Promise<void> {
    try {
      const walletAddress = localStorage.getItem('wallet_address');
      const walletMode = localStorage.getItem('wallet_mode') || 'local';

      const newToken = await this.auth.token('', walletAddress, walletMode);
      localStorage.setItem('wallet_token', newToken);

      // Update per-account cache
      if (walletAddress) {
        localStorage.setItem(`wallet_token_${walletAddress.toLowerCase()}`, newToken);
      }

      if (typeof window !== 'undefined' && (window as any).__userContextClient) {
        (window as any).__userContextClient.token = newToken;
      }

      if (typeof window !== 'undefined') {
        (window as any).__tokenExpired = false;
      }
      this.hasNotifiedExpiry = false;
    } catch (error) {
      console.error('Failed to auto-refresh token:', error);

      if (typeof window !== 'undefined') {
        (window as any).__tokenExpired = true;
      }

      // Only prompt if auto-refresh failed
      if (!this.hasNotifiedExpiry) {
        this.hasNotifiedExpiry = true;
        toast.warning('Token expired — tap refresh to renew', {
          toastId: 'token-expired',
          position: 'top-center',
          autoClose: 8000,
          closeOnClick: true,
          closeButton: true,
        });
      }
    }
  }

  /**
   * Check if token is expired (helper method)
   */
  public isTokenExpired(token: string, bufferSeconds: number = 300): boolean {
    try {
      const authData = this.auth.token2data(token);
      const tokenTime = parseFloat(authData.time);
      const currentTime = Date.now() / 1000;
      const age = currentTime - tokenTime;
      
      // Check if token age exceeds maxAge minus buffer
      return age >= ((this.auth as any)['maxAge'] - bufferSeconds);
    } catch (error) {
      return true; // Treat invalid tokens as expired
    }
  }
}

export default TokenExpiryHandler;
