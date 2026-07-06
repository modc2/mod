"use client";

import {Auth} from './auth';
import {Key} from '@/key';
import modConfig from '@config';

export class Client {
  public url: string;
  public token: string | undefined;
  public auth: Auth;
  public key?: Key;
  private keyRotationCallback?: () => void;

  constructor(url?: string, token?: string) {
    const customUrl = typeof window !== 'undefined' ? localStorage.getItem('custom_node_url') : null;
    const baseUrl = url || customUrl || process.env.NEXT_PUBLIC_API_URL || modConfig.url.api || 'http://localhost:8000';
    // Use /api path — middleware proxies to Flask backend (avoids CORS)
    if (typeof window !== 'undefined' && !url && !customUrl) {
      this.url = '/api';
    } else {
      this.url = baseUrl;
    }
    console.log('Client initialized with URL:', this.url);
    this.auth = new Auth(undefined);
    this.token = token;
  }


  public async call(fn: string = 'info', params: Record<string, any> | FormData = {}, wait: boolean = true, headers: any = {}, timeout: number = 30000, onCancel?: () => void): Promise<any> {
    
    // if / in fn, treat as path and do not append to url
    let isPathCall = false;
    if (fn.includes('/')) {
      isPathCall = true;
      params = {fn: fn, params: params, token: this.token || '', wait: wait, timeout: timeout};
      fn = 'call';
    }
    let body: string | FormData;
    headers = { token: this.token || ''};
    body = JSON.stringify(params);
    headers['Content-Type'] = 'application/json';
    headers['Accept'] = 'application/json';
    const url: string = `${this.url}/${fn}`;

    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), timeout);

    if (onCancel) {
      onCancel = () => {
        controller.abort();
        clearTimeout(timeoutId);
      };
    }

    try {
      const response = await fetch(url, {
        method: 'POST',
        headers: headers,
        body: body,
        signal: controller.signal,
        cache: 'no-cache',
      });

      clearTimeout(timeoutId);

      if (!response.ok) {
        const errorText = await response.text();
        console.error('[Safari Debug] Error response body:', errorText);

        if (response.status === 401) {
          throw new Error('Unauthorized access - please check your authentication credentials.');
        } else if (response.status === 404) {
          throw new Error('Resource not found - please check the URL or function name.');
        } else if (response.status === 500) {
          throw new Error(`Internal server error - ${errorText}`);
        } else {
          throw new Error(`HTTP ${response.status}: ${errorText || 'Unknown error'}`);
        }
      }

      const contentType = response.headers.get('Content-Type');

      if (contentType?.includes('text/event-stream')) {
        return this.handleStream(response);
      }
      if (contentType?.includes('application/json')) {
        let result = await response.json();
        // if result is a dictionary and only has the result key, return the value of the result key
        if (typeof result === 'object' && result !== null && 'result' in result && Object.keys(result).length === 1) {
          result = result['result'];
        }
        console.log('[Safari Debug] JSON response:', result);
        if (result && result.success === false) {
          let error_msg = JSON.stringify(result);
          throw new Error(`API Error: ${error_msg}`);
        }
        if (isPathCall && typeof window !== 'undefined') {
          window.dispatchEvent(new Event('mod:tx'));
        }
        return result;
      }
      // if it is a dictionary and result key exists, return result key
      const textResult = await response.text();
      return textResult;
    } catch (error: any) {
      clearTimeout(timeoutId);

      if (error.name === 'AbortError') {
        throw new Error('Request cancelled or timed out');
      }
      if (error.name === 'TypeError' && error.message.includes('Failed to fetch')) {
        throw new Error(`Network error - Safari blocked the request. Check: 1) CORS headers on server, 2) URL is correct (${url}), 3) Server is running. Error: ${error.message}`);
      }
      throw error;
    }
  }

  private createStreamGenerator(response: Response): AsyncGenerator<string, void, unknown> {
    const reader = response.body!.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    return (async function* () {
      try {
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          // Decode the chunk and add to buffer
          buffer += decoder.decode(value, { stream: true });

          // Split on newlines to process complete lines
          const lines = buffer.split('\n');

          // Keep the last incomplete line in buffer
          buffer = lines.pop() || '';

          for (const line of lines) {
            const trimmedLine = line.trim();
            if (!trimmedLine) continue;

            // Handle SSE format: "data: {content}"
            if (trimmedLine.startsWith('data: ')) {
              const data = trimmedLine.slice(6);
              if (data === '[DONE]') continue;

              try {
                const parsed = JSON.parse(data);
                // Support multiple content field formats
                const content = parsed.content || parsed.text || parsed.delta?.content || parsed.choices?.[0]?.delta?.content;
                if (content) {
                  yield content;
                }
              } catch (e) {
                // If not JSON, treat as raw content
                if (data) {
                  yield data;
                }
              }
            } else if (trimmedLine.startsWith('{')) {
              // Handle raw JSON lines (non-SSE format)
              try {
                const parsed = JSON.parse(trimmedLine);
                const content = parsed.content || parsed.text || parsed.delta?.content || parsed.choices?.[0]?.delta?.content;
                if (content) {
                  yield content;
                }
              } catch (e) {
                // Skip malformed JSON
                console.warn('Malformed JSON in stream:', trimmedLine);
              }
            }
          }
        }

        // Process any remaining buffer content
        if (buffer.trim()) {
          try {
            const parsed = JSON.parse(buffer.trim());
            const content = parsed.content || parsed.text || parsed.delta?.content;
            if (content) {
              yield content;
            }
          } catch (e) {
            // Yield raw buffer if not JSON
            if (buffer.trim()) {
              yield buffer.trim();
            }
          }
        }
      } finally {
        reader.releaseLock();
      }
    })();
  }

  private async handleStream(response: Response): Promise<AsyncGenerator<string, void, unknown>> {
    return this.createStreamGenerator(response);
  }

  /**
   * Call a function with streaming support
   * Returns an AsyncGenerator for streaming responses
   */
  public async callStream(fn: string, params: Record<string, any> = {}): Promise<AsyncGenerator<string, void, unknown>> {
    const url = `${this.url}/call`;
    const body = JSON.stringify({
      fn,
      params: { ...params, stream: true },
      token: this.token || '',
      wait: true
    });

    const response = await fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Accept': 'text/event-stream',
        token: this.token || ''
      },
      body
    });

    if (!response.ok) {
      throw new Error(`HTTP ${response.status}: ${await response.text()}`);
    }

    return this.createStreamGenerator(response);
  }
}
