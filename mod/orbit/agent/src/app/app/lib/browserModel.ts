// The `browser` provider's half of the run loop.
//
// The agent loop is server-side, but a browser model isn't: the weights are in
// this tab. So the server parks each step's generation request on the run's SSE
// stream as a `model_request` event, this file generates it in a worker, and
// POSTs the text back to /browser/completion — which unblocks the run.
//
// The worker (public/lfm-worker.js) is the liquidai module's, verbatim: it
// pulls transformers.js off a CDN and the ONNX weights off HuggingFace, so
// nothing about a browser run — prompt, tokens, weights — touches our server.

const BASE = process.env.NEXT_PUBLIC_BASE_PATH || ''

export type BrowserState = {
  phase: 'idle' | 'loading' | 'ready' | 'generating' | 'error'
  repo?: string
  device?: string          // webgpu | wasm
  dtype?: string
  pct?: number             // weight download, 0-100
  file?: string
  tokens?: number          // tokens streamed in the current generation
  error?: string
}

type Pending = { resolve: (text: string) => void; reject: (e: Error) => void }

export class BrowserModel {
  private worker: Worker | null = null
  private loaded: string | null = null       // repo currently in the worker
  private loading: Promise<void> | null = null
  private pending: Pending | null = null     // one generation at a time
  private text = ''
  private tokens = 0
  onState: (s: BrowserState) => void = () => {}

  private state(s: BrowserState) { this.onState(s) }

  /** WebGPU is the fast path; wasm still works, just slower. */
  static get device(): string {
    return typeof navigator !== 'undefined' && (navigator as any).gpu ? 'webgpu' : 'wasm'
  }

  private spawn(): Worker {
    if (this.worker) return this.worker
    const w = new Worker(`${BASE}/lfm-worker.js`, { type: 'module' })
    w.onmessage = (e: MessageEvent) => this.handle(e.data || {})
    w.onerror = (e) => this.fail(String((e as any).message || 'worker failed'))
    this.worker = w
    return w
  }

  private handle(msg: any) {
    switch (msg.type) {
      case 'status':
        this.state({ phase: 'loading', repo: msg.repo, file: msg.file, dtype: msg.dtype, device: msg.device })
        break
      case 'progress':
        this.state({ phase: 'loading', repo: this.loaded || undefined, pct: msg.pct, file: msg.file })
        break
      case 'ready':
        this.loaded = msg.repo
        this.state({ phase: 'ready', repo: msg.repo, device: msg.device, dtype: msg.dtype })
        this.readyResolve?.()
        break
      case 'token':
        this.text += msg.text
        this.tokens += 1
        this.state({ phase: 'generating', repo: this.loaded || undefined, tokens: this.tokens })
        break
      case 'done': {
        const out = this.text
        this.state({ phase: 'ready', repo: this.loaded || undefined, device: msg.device, dtype: msg.dtype })
        this.pending?.resolve(out)
        this.pending = null
        break
      }
      case 'error':
        this.fail(msg.error || 'unknown worker error')
        break
    }
  }

  private readyResolve: (() => void) | null = null
  private readyReject: ((e: Error) => void) | null = null

  private fail(error: string) {
    this.state({ phase: 'error', repo: this.loaded || undefined, error })
    this.pending?.reject(new Error(error))
    this.pending = null
    this.readyReject?.(new Error(error))
    // a failed load leaves nothing resident — force the next call to reload
    this.loading = null
    this.readyResolve = null
    this.readyReject = null
  }

  /** Download + compile a repo into the tab. Idempotent per repo. */
  async load(repo: string): Promise<void> {
    if (this.loaded === repo && !this.loading) return
    if (this.loading && this.loaded === repo) return this.loading
    this.loaded = repo
    this.state({ phase: 'loading', repo })
    this.loading = new Promise<void>((resolve, reject) => {
      this.readyResolve = () => { this.loading = null; resolve() }
      this.readyReject = (e) => { this.loading = null; reject(e) }
      this.spawn().postMessage({
        type: 'load', repo, device: BrowserModel.device,
        modality: /-VL-|vision/i.test(repo) ? 'vision' : 'text',
      })
    })
    return this.loading
  }

  /** One completion, loading the model first if it isn't resident. */
  async generate(req: { model: string; messages: any[]; max_tokens?: number; temperature?: number }): Promise<string> {
    await this.load(req.model)
    if (this.pending) throw new Error('the tab is already generating a step')
    this.text = ''
    this.tokens = 0
    this.state({ phase: 'generating', repo: req.model, tokens: 0 })
    return new Promise<string>((resolve, reject) => {
      this.pending = { resolve, reject }
      this.spawn().postMessage({
        type: 'generate', messages: req.messages,
        max_tokens: req.max_tokens ?? 512,
        temperature: req.temperature ?? 0,
      })
    })
  }

  /** Free the tab's memory — a resident 1.2B model is hundreds of MB. */
  dispose() {
    this.worker?.terminate()
    this.worker = null
    this.loaded = null
    this.loading = null
    this.pending = null
  }
}

/** Answer one `model_request` event: generate here, hand the text back. */
export async function serveModelRequest(model: BrowserModel, ev: any, apiUrl: string) {
  let body: any
  try {
    const text = await model.generate({
      model: ev.model, messages: ev.messages,
      max_tokens: ev.max_tokens, temperature: ev.temperature,
    })
    body = { id: ev.id, text }
  } catch (e: any) {
    body = { id: ev.id, error: String(e?.message || e) }
  }
  // the run is blocked on this POST — a failure to deliver would hang it
  // until the bridge times out, so say so in the console rather than swallow it
  await fetch(`${apiUrl}/browser/completion`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}
