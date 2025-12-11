import {Auth, AuthHeaders} from './auth';
import {Key} from '@/block/key';


export class Client {
  public url: string;
  public key: Key | undefined;
  public auth: Auth;

  constructor(url?: string, key?: Key ) {
    this.url = url || process.env.NEXT_PUBLIC_API_URL || config.api_url || 'http://localhost:8000';
    console.log('Client initialized with URL:', this.url);
    this.key = key;
    this.auth = new Auth(key);
  }

  public async call(fn: string = 'info', params: Record<string, any> | FormData = {}, cost = 0, headers: any = {}): Promise<any> {
    let body: string | FormData;
    let start_time = Date.now();
    
    headers = this.auth.generate({'fn': fn, 'params': params}, cost);
    
    body = JSON.stringify(params);
    headers['Content-Type'] = 'application/json';
    headers['Accept'] = 'application/json';
    headers['Access-Control-Request-Method']
    const url: string = `${this.url}/${fn}`;
    
    try {
      const response = await fetch(url, {
        method: 'POST',
        headers: headers,
        body: body,
      });

      
      if (!response.ok) {
        if (response.status === 401) {
          throw new Error('Unauthorized access - please check your authentication credentials.');
        } else if (response.status === 404) {
          throw new Error('Resource not found - please check the URL or function name.');
        } else if (response.status === 500) {
          throw new Error('Internal server error - please try again later.');
        } else {
          throw new Error(`Unexpected error - status code: ${response.status}`);
        }
      }
      
      const contentType = response.headers.get('Content-Type');
      if (contentType?.includes('text/event-stream')) {
        return this.handleStream(response);
      }
      if (contentType?.includes('application/json')) {
        let result = await response.json();
        if (result && result.success === false) {
          let error_msg = JSON.stringify(result);
          throw new Error(`API Error: ${error_msg}`);
        }
        return result;
      }
      return await response.text();
    } catch (error) {
      console.error('Request failed:', error);
      throw error;
    }
  }

  private async handleStream(response: Response): Promise<void> {
    const reader = response.body!.getReader();
    const decoder = new TextDecoder();

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
    }
  }
}
