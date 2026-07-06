"use client";

import { Auth, AuthHeaders } from './auth';

export class TokenRefreshManager {
  private refreshTimer: NodeJS.Timeout | null = null;
  private auth: Auth;
  private onTokenRefresh: (token: string) => void;

  constructor(
    auth: Auth,
    onTokenRefresh: (token: string) => void,
    private refreshInterval: number = 86400000 // 1 day in ms
  ) {
    this.auth = auth;
    this.onTokenRefresh = onTokenRefresh;
  }

  /**
   * Start automatic token refresh (only for local wallets)
   */
  public startAutoRefresh(): void {
    this.stopAutoRefresh();

    const walletMode = typeof localStorage !== 'undefined'
      ? localStorage.getItem('wallet_mode')
      : 'local';

    // Don't auto-refresh non-local wallets — they require manual signing
    if (walletMode && walletMode !== 'local') return;

    this.refreshTimer = setInterval(async () => {
      await this.refreshToken();
    }, this.refreshInterval);
  }

  /**
   * Stop automatic token refresh
   */
  public stopAutoRefresh(): void {
    if (this.refreshTimer) {
      clearInterval(this.refreshTimer);
      this.refreshTimer = null;
    }
  }

  /**
   * Manually refresh the token
   */
  public async refreshToken(): Promise<string> {
    try {
      const walletMode = typeof localStorage !== 'undefined' 
        ? localStorage.getItem('wallet_mode') 
        : 'local';
      const walletAddress = typeof localStorage !== 'undefined'
        ? localStorage.getItem('wallet_address')
        : null;

      // Generate new token based on wallet mode
      const newToken = await this.auth.token('', walletAddress, walletMode);
      
      // Update localStorage
      if (typeof localStorage !== 'undefined') {
        localStorage.setItem('wallet_token', newToken);
      }

      // Notify callback
      this.onTokenRefresh(newToken);

      return newToken;
    } catch (error) {
      console.error('Token refresh failed:', error);
      throw error;
    }
  }

  /**
   * Check if token is expired or about to expire
   */
  public isTokenExpired(token: string, bufferSeconds: number = 300): boolean {
    try {
      const authData = this.auth.token2data(token);
      const tokenTime = parseFloat(authData.time);
      const currentTime = Date.now() / 1000;
      const age = currentTime - tokenTime;
      
      // Check if token age exceeds maxAge minus buffer
      return age >= (this.auth['maxAge'] - bufferSeconds);
    } catch (error) {
      return true; // Treat invalid tokens as expired
    }
  }
}

export default TokenRefreshManager;