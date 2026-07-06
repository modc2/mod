/**
 * ModProtocol — Frontend emulation of the entire Mod API
 *
 * Mirrors the Rust job-server endpoints and the Python mod protocol schema,
 * providing a typed client that works from the browser.  The class also
 * exposes a universal HTML-adapter so that *any* module's config.json
 * schema can be rendered into, and bound to, arbitrary HTML elements.
 */

// ── Schema types (matches config.json "schema" block) ────────────────

export interface SchemaParam {
  name: string;
  type: "str" | "int" | "float" | "bool" | "dict" | "list" | "bytes";
  value: unknown;
}

export interface SchemaFn {
  input: SchemaParam[];
  output: { type: string; value: unknown };
  docs?: string;
  cost?: number;
}

export interface SchemaEndpoint {
  method: string | string[];
  auth: boolean;
  docs?: string;
}

export interface ModConfig {
  name: string;
  version: string;
  description: string;
  owner?: string;
  port?: number;
  default_path?: string;
  urls?: { app?: string; api?: string };
  fns: string[];
  schema: Record<string, SchemaFn>;
  endpoints?: Record<string, SchemaEndpoint>;
}

// ── API response types ───────────────────────────────────────────────

export interface Job {
  id: string;
  prompt: string;
  model: string;
  work_dir: string;
  status: "pending" | "running" | "completed" | "failed" | "cancelled";
  output: string;
  error: string | null;
  pid: number | null;
  created_at: number;
  updated_at: number;
  user_address: string;
}

export interface ModuleInfo {
  name: string;
  path: string;
  display: string;
  category: string;
  has_config: boolean;
  app_url: string | null;
  api_url: string | null;
  description: string | null;
  fns: string[];
  has_app_dir: boolean;
  has_server_dir: boolean;
  has_api_dir: boolean;
  owner: string | null;
  version: string | null;
  cid: string | null;
  created_at: number | null;
}

export interface AuthChallenge {
  message: string;
}

export interface AuthToken {
  token: string;
  address: string;
}

export interface FileNode {
  name: string;
  path: string;
  is_dir: boolean;
  children?: FileNode[];
}

export interface ChangelogEntry {
  version: string;
  cid: string;
  date: string;
  description: string;
  timestamp: number;
  file_count?: number;
}

export interface SubmitRequest {
  prompt: string;
  model?: string;
  work_dir?: string;
  module_name?: string;
  creation_mode?: "new" | "fork";
  fork_source?: string;
  anchor_dir?: string;
  images?: Array<{ name: string; data: string }>;
  agent_type?: string;
}

// ── HTML element schema (universal adapter) ──────────────────────────

export type HtmlElementType =
  | "input"
  | "textarea"
  | "select"
  | "checkbox"
  | "number"
  | "range"
  | "file"
  | "hidden"
  | "button"
  | "output"
  | "div"
  | "table"
  | "form"
  | "pre"
  | "code";

export interface HtmlFieldSchema {
  /** The mod schema param this maps to */
  param: string;
  /** HTML element to render */
  element: HtmlElementType;
  /** Label / placeholder */
  label: string;
  /** HTML attributes to spread */
  attrs?: Record<string, string>;
  /** Select options */
  options?: Array<{ label: string; value: string }>;
  /** Default value (from schema) */
  defaultValue?: unknown;
  /** CSS classes */
  className?: string;
}

export interface HtmlFormSchema {
  fn: string;
  title: string;
  description?: string;
  fields: HtmlFieldSchema[];
  submitLabel?: string;
  outputElement?: HtmlElementType;
  className?: string;
}

// ── ModProtocol class ────────────────────────────────────────────────

export class ModProtocol {
  private baseUrl: string;
  private token: string | null = null;
  private config: ModConfig | null = null;

  constructor(baseUrl: string = "http://localhost:8820") {
    this.baseUrl = baseUrl.replace(/\/$/, "");
  }

  // ── Internal helpers ───────────────────────────────────────────────

  private headers(auth = false): Record<string, string> {
    const h: Record<string, string> = { "Content-Type": "application/json" };
    if (auth && this.token) h["Authorization"] = `Bearer ${this.token}`;
    return h;
  }

  private async get<T>(path: string, auth = false): Promise<T> {
    const res = await fetch(`${this.baseUrl}${path}`, {
      headers: this.headers(auth),
    });
    if (!res.ok) throw new Error(`GET ${path}: ${res.status} ${res.statusText}`);
    return res.json() as Promise<T>;
  }

  private async post<T>(path: string, body: unknown, auth = false): Promise<T> {
    const res = await fetch(`${this.baseUrl}${path}`, {
      method: "POST",
      headers: this.headers(auth),
      body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error(`POST ${path}: ${res.status} ${res.statusText}`);
    return res.json() as Promise<T>;
  }

  private async del<T>(path: string, auth = false): Promise<T> {
    const res = await fetch(`${this.baseUrl}${path}`, {
      method: "DELETE",
      headers: this.headers(auth),
    });
    if (!res.ok) throw new Error(`DELETE ${path}: ${res.status} ${res.statusText}`);
    return res.json() as Promise<T>;
  }

  private async put<T>(path: string, body: unknown, auth = false): Promise<T> {
    const res = await fetch(`${this.baseUrl}${path}`, {
      method: "PUT",
      headers: this.headers(auth),
      body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error(`PUT ${path}: ${res.status} ${res.statusText}`);
    return res.json() as Promise<T>;
  }

  // ── Auth ───────────────────────────────────────────────────────────

  setToken(token: string) {
    this.token = token;
  }

  getToken(): string | null {
    return this.token;
  }

  async challenge(address: string): Promise<AuthChallenge> {
    return this.get<AuthChallenge>(`/auth/challenge?address=${address}`);
  }

  async verify(address: string, signature: string, message: string): Promise<AuthToken> {
    const result = await this.post<AuthToken>("/auth/verify", { address, signature, message });
    this.token = result.token;
    return result;
  }

  async role(): Promise<{ role: string; address: string }> {
    return this.get("/auth/role", true);
  }

  // ── Health & Config ────────────────────────────────────────────────

  async health(): Promise<{ status: string }> {
    return this.get("/health");
  }

  async getConfig(): Promise<ModConfig> {
    const cfg = await this.get<ModConfig>("/config");
    this.config = cfg;
    return cfg;
  }

  async owner(): Promise<{ owner: string | null; has_owner: boolean }> {
    return this.get("/owner");
  }

  // ── Jobs ───────────────────────────────────────────────────────────

  async submitJob(req: SubmitRequest): Promise<Job> {
    return this.post<Job>("/jobs", req, true);
  }

  async listJobs(): Promise<Job[]> {
    return this.get<Job[]>("/jobs", true);
  }

  async getJob(id: string): Promise<Job> {
    return this.get<Job>(`/jobs/${id}`, true);
  }

  async deleteJob(id: string): Promise<{ ok: boolean }> {
    return this.del(`/jobs/${id}`, true);
  }

  async cancelJob(id: string): Promise<{ ok: boolean }> {
    return this.post(`/jobs/${id}/cancel`, {}, true);
  }

  streamJob(id: string, onData: (text: string) => void, onDone?: () => void): () => void {
    const url = `${this.baseUrl}/jobs/${id}/stream`;
    const es = new EventSource(url);
    es.onmessage = (ev) => onData(ev.data);
    es.onerror = () => {
      es.close();
      onDone?.();
    };
    return () => es.close();
  }

  // ── Modules ────────────────────────────────────────────────────────

  async listModules(): Promise<ModuleInfo[]> {
    return this.get("/modules");
  }

  async getModuleConfig(name: string): Promise<ModConfig> {
    return this.get(`/modules/${name}/config`);
  }

  async deleteModule(name: string): Promise<{ ok: boolean }> {
    return this.del(`/modules/${name}`, true);
  }

  async renameModule(name: string, newName: string): Promise<{ ok: boolean }> {
    return this.put(`/modules/${name}/rename`, { new_name: newName }, true);
  }

  // ── Files ──────────────────────────────────────────────────────────

  async fileTree(path: string, depth?: number): Promise<FileNode[]> {
    let url = `/files/tree?path=${encodeURIComponent(path)}`;
    if (depth !== undefined) url += `&depth=${depth}`;
    return this.get(url);
  }

  async fileContent(path: string): Promise<{ content: string; path: string }> {
    return this.get(`/files/content?path=${encodeURIComponent(path)}`);
  }

  fileRawUrl(path: string): string {
    return `${this.baseUrl}/files/raw?path=${encodeURIComponent(path)}`;
  }

  async fileSearch(path: string, query: string): Promise<Array<{ name: string; path: string }>> {
    return this.get(`/files/search?path=${encodeURIComponent(path)}&query=${encodeURIComponent(query)}`);
  }

  async fileGrep(
    path: string,
    query: string,
    opts?: { caseSensitive?: boolean; regex?: boolean }
  ): Promise<Array<{ file: string; line: number; content: string }>> {
    let url = `/files/grep?path=${encodeURIComponent(path)}&query=${encodeURIComponent(query)}`;
    if (opts?.caseSensitive) url += "&caseSensitive=true";
    if (opts?.regex) url += "&regex=true";
    return this.get(url);
  }

  async writeFile(path: string, content: string): Promise<{ ok: boolean }> {
    return this.post("/files/write", { path, content }, true);
  }

  // ── Repos ──────────────────────────────────────────────────────────

  async repos(): Promise<Array<{ name: string; path: string; display: string }>> {
    return this.get("/repos");
  }

  // ── Changelog & Versions ───────────────────────────────────────────

  async changelog(): Promise<ChangelogEntry[]> {
    return this.get("/changelog");
  }

  async getVersion(version: string): Promise<Record<string, unknown>> {
    return this.get(`/versions/${encodeURIComponent(version)}`);
  }

  // ── Schema-driven function call (universal) ────────────────────────

  /**
   * Call any function defined in a module's config.json schema.
   * Resolves the endpoint from the schema, coerces params to declared
   * types, and dispatches the request.
   */
  async callFn(
    fnName: string,
    params: Record<string, unknown> = {},
    moduleConfig?: ModConfig
  ): Promise<unknown> {
    const cfg = moduleConfig || this.config;
    if (!cfg) throw new Error("No config loaded. Call getConfig() or pass moduleConfig.");
    const schema = cfg.schema[fnName];
    if (!schema) throw new Error(`Function "${fnName}" not found in schema`);

    // Coerce params to declared types
    const coerced: Record<string, unknown> = {};
    for (const p of schema.input) {
      const raw = params[p.name] ?? p.value;
      if (raw === null || raw === undefined || raw === "_empty") continue;
      coerced[p.name] = ModProtocol.coerceParam(raw, p.type);
    }

    // Look up matching endpoint
    const endpoint = this.resolveEndpoint(fnName, cfg);
    if (endpoint) {
      const method = Array.isArray(endpoint.method) ? endpoint.method[0] : endpoint.method;
      const path = endpoint.path.replace(/\{(\w+)\}/g, (_, key) => {
        const v = coerced[key];
        delete coerced[key];
        return encodeURIComponent(String(v ?? ""));
      });
      if (method === "GET") {
        const qs = Object.entries(coerced)
          .map(([k, v]) => `${k}=${encodeURIComponent(String(v))}`)
          .join("&");
        return this.get(`${path}${qs ? "?" + qs : ""}`, endpoint.auth);
      }
      return this.post(path, coerced, endpoint.auth);
    }

    // Fallback: POST to /call/{fnName}
    return this.post(`/call/${fnName}`, coerced, true);
  }

  private resolveEndpoint(
    fnName: string,
    cfg: ModConfig
  ): { path: string; method: string | string[]; auth: boolean } | null {
    if (!cfg.endpoints) return null;
    // Direct name match: fn "jobs" → endpoint "/jobs"
    const directPath = `/${fnName}`;
    if (cfg.endpoints[directPath]) {
      return { path: directPath, ...cfg.endpoints[directPath] };
    }
    // Check parametric endpoints
    for (const [pattern, ep] of Object.entries(cfg.endpoints)) {
      const base = pattern.split("/")[1];
      if (base === fnName || base === fnName.replace(/_/g, "-")) {
        return { path: pattern, ...ep };
      }
    }
    return null;
  }

  // ── Type coercion ──────────────────────────────────────────────────

  static coerceParam(value: unknown, type: string): unknown {
    if (value === null || value === undefined) return value;
    switch (type) {
      case "str":
        return String(value);
      case "int":
        return parseInt(String(value), 10);
      case "float":
        return parseFloat(String(value));
      case "bool":
        if (typeof value === "string") return value === "true" || value === "1";
        return Boolean(value);
      case "dict":
        if (typeof value === "string") {
          try { return JSON.parse(value); } catch { return {}; }
        }
        return value;
      case "list":
        if (typeof value === "string") {
          try { return JSON.parse(value); } catch { return value.split(",").map((s: string) => s.trim()); }
        }
        return Array.isArray(value) ? value : [value];
      default:
        return value;
    }
  }

  // ── HTML Schema Adapter ────────────────────────────────────────────

  /**
   * Convert a mod schema function definition into an HtmlFormSchema
   * that can drive any HTML rendering (React, vanilla DOM, etc.)
   */
  static schemaToHtml(fnName: string, schema: SchemaFn, config?: ModConfig): HtmlFormSchema {
    const fields: HtmlFieldSchema[] = schema.input.map((p) => ({
      param: p.name,
      element: ModProtocol.paramToElement(p),
      label: ModProtocol.paramToLabel(p.name),
      defaultValue: (p.value === "_empty" || p.value === null || p.value === undefined) ? undefined : p.value,
      attrs: ModProtocol.paramToAttrs(p),
      options: ModProtocol.paramToOptions(p),
      className: `mod-field mod-field-${p.type}`,
    }));

    return {
      fn: fnName,
      title: ModProtocol.paramToLabel(fnName),
      description: schema.docs,
      fields,
      submitLabel: `Run ${ModProtocol.paramToLabel(fnName)}`,
      outputElement: schema.output.type === "list" ? "table" : "pre",
      className: "mod-form",
    };
  }

  /**
   * Convert ALL functions in a config.json into HTML form schemas
   */
  static configToHtml(config: ModConfig): HtmlFormSchema[] {
    return config.fns
      .filter((fn) => config.schema[fn])
      .map((fn) => ModProtocol.schemaToHtml(fn, config.schema[fn], config));
  }

  /**
   * Given HTML form schemas, generate a complete HTML page string
   * (vanilla HTML + inline CSS/JS) that renders and executes them.
   * This is the "adapt to any HTML" capability.
   */
  static generateHtmlPage(
    forms: HtmlFormSchema[],
    config: ModConfig,
    apiUrl: string = "http://localhost:8820"
  ): string {
    const formBlocks = forms.map((form) => ModProtocol.renderFormHtml(form)).join("\n");

    return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>${config.name} — Mod Protocol</title>
<style>
:root { --bg: #0a0a0f; --fg: #e0e0e0; --accent: #60a5fa; --border: #1e293b; --card: #111118; --success: #34d399; --error: #f87171; }
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: 'SF Mono', Monaco, 'Courier New', monospace; background: var(--bg); color: var(--fg); padding: 2rem; }
h1 { color: var(--accent); margin-bottom: 0.5rem; font-size: 1.5rem; }
.mod-header { border-bottom: 1px solid var(--border); padding-bottom: 1rem; margin-bottom: 2rem; }
.mod-header p { color: #888; font-size: 0.85rem; }
.mod-form { background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 1.5rem; margin-bottom: 1.5rem; }
.mod-form h2 { color: var(--accent); font-size: 1.1rem; margin-bottom: 0.25rem; }
.mod-form .docs { color: #888; font-size: 0.8rem; margin-bottom: 1rem; }
.mod-field { margin-bottom: 1rem; }
.mod-field label { display: block; color: #aaa; font-size: 0.8rem; margin-bottom: 0.25rem; }
.mod-field input, .mod-field textarea, .mod-field select {
  width: 100%; padding: 0.5rem; background: rgba(255,255,255,0.04); border: 1px solid var(--border);
  border-radius: 4px; color: var(--fg); font-family: inherit; font-size: 0.85rem;
}
.mod-field input:focus, .mod-field textarea:focus { border-color: var(--accent); outline: none; }
.mod-field textarea { min-height: 80px; resize: vertical; }
.mod-submit { background: var(--accent); color: #000; border: none; padding: 0.6rem 1.5rem;
  border-radius: 4px; cursor: pointer; font-family: inherit; font-weight: 600; font-size: 0.85rem; }
.mod-submit:hover { opacity: 0.9; }
.mod-submit:disabled { opacity: 0.5; cursor: not-allowed; }
.mod-output { margin-top: 1rem; background: rgba(0,0,0,0.3); border: 1px solid var(--border);
  border-radius: 4px; padding: 1rem; white-space: pre-wrap; font-size: 0.8rem; max-height: 400px; overflow: auto; }
.mod-output.success { border-color: var(--success); }
.mod-output.error { border-color: var(--error); color: var(--error); }
.mod-status { font-size: 0.75rem; color: #888; margin-top: 0.5rem; }
table { width: 100%; border-collapse: collapse; font-size: 0.8rem; }
th, td { padding: 0.4rem 0.8rem; border: 1px solid var(--border); text-align: left; }
th { background: rgba(255,255,255,0.04); color: var(--accent); }
.mod-nav { display: flex; gap: 0.5rem; flex-wrap: wrap; margin-bottom: 1.5rem; }
.mod-nav button { background: rgba(255,255,255,0.04); border: 1px solid var(--border); color: #aaa;
  padding: 0.4rem 0.8rem; border-radius: 4px; cursor: pointer; font-family: inherit; font-size: 0.75rem; }
.mod-nav button:hover, .mod-nav button.active { border-color: var(--accent); color: var(--accent); }
</style>
</head>
<body>
<div class="mod-header">
  <h1>${config.name} v${config.version}</h1>
  <p>${config.description || ""}</p>
</div>
<div class="mod-nav" id="nav"></div>
<div id="forms">${formBlocks}</div>
<script>
const API = "${apiUrl}";
let TOKEN = localStorage.getItem("mod_token") || null;

function headers(auth) {
  const h = { "Content-Type": "application/json" };
  if (auth && TOKEN) h["Authorization"] = "Bearer " + TOKEN;
  return h;
}

async function callFn(fnName, form) {
  const outputEl = document.getElementById("output-" + fnName);
  const statusEl = document.getElementById("status-" + fnName);
  const btn = form.querySelector("button[type=submit]");
  btn.disabled = true;
  statusEl.textContent = "Running...";
  outputEl.textContent = "";
  outputEl.className = "mod-output";
  try {
    const data = new FormData(form);
    const params = {};
    for (const [k, v] of data.entries()) { if (v !== "") params[k] = v; }
    const res = await fetch(API + "/call/" + fnName, {
      method: "POST", headers: headers(true), body: JSON.stringify(params)
    });
    const text = await res.text();
    let parsed;
    try { parsed = JSON.parse(text); } catch { parsed = text; }
    if (typeof parsed === "object") {
      outputEl.textContent = JSON.stringify(parsed, null, 2);
    } else {
      outputEl.textContent = parsed;
    }
    outputEl.classList.add(res.ok ? "success" : "error");
    statusEl.textContent = res.ok ? "Done" : "Error " + res.status;
  } catch (e) {
    outputEl.textContent = e.message;
    outputEl.classList.add("error");
    statusEl.textContent = "Failed";
  }
  btn.disabled = false;
}

// Build nav
const nav = document.getElementById("nav");
document.querySelectorAll(".mod-form").forEach(f => {
  const btn = document.createElement("button");
  btn.textContent = f.dataset.fn;
  btn.onclick = () => {
    document.querySelectorAll(".mod-form").forEach(x => x.style.display = "none");
    f.style.display = "block";
    document.querySelectorAll(".mod-nav button").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
  };
  nav.appendChild(btn);
});
// Show first by default
const allForms = document.querySelectorAll(".mod-form");
if (allForms.length > 0) {
  allForms.forEach((f, i) => f.style.display = i === 0 ? "block" : "none");
  nav.querySelector("button")?.classList.add("active");
}
</script>
</body>
</html>`;
  }

  // ── Render a single form to HTML string ────────────────────────────

  private static renderFormHtml(form: HtmlFormSchema): string {
    const fields = form.fields
      .map((f) => {
        const val = f.defaultValue !== undefined && f.defaultValue !== null ? String(f.defaultValue) : "";
        const attrs = f.attrs
          ? Object.entries(f.attrs).map(([k, v]) => `${k}="${v}"`).join(" ")
          : "";

        let input: string;
        switch (f.element) {
          case "textarea":
            input = `<textarea name="${f.param}" placeholder="${f.label}" ${attrs}>${val}</textarea>`;
            break;
          case "select":
            const opts = (f.options || [])
              .map((o) => `<option value="${o.value}"${o.value === val ? " selected" : ""}>${o.label}</option>`)
              .join("");
            input = `<select name="${f.param}" ${attrs}>${opts}</select>`;
            break;
          case "checkbox":
            input = `<label style="display:flex;align-items:center;gap:0.5rem;cursor:pointer">
              <input type="checkbox" name="${f.param}" ${val === "true" ? "checked" : ""} ${attrs}> ${f.label}
            </label>`;
            return `<div class="${f.className || "mod-field"}">${input}</div>`;
          case "number":
            input = `<input type="number" name="${f.param}" value="${val}" placeholder="${f.label}" ${attrs}>`;
            break;
          default:
            input = `<input type="text" name="${f.param}" value="${val}" placeholder="${f.label}" ${attrs}>`;
        }

        return `<div class="${f.className || "mod-field"}">
  <label>${f.label}</label>
  ${input}
</div>`;
      })
      .join("\n");

    return `<div class="mod-form" data-fn="${form.fn}" id="form-${form.fn}">
  <h2>${form.title}</h2>
  ${form.description ? `<div class="docs">${form.description}</div>` : ""}
  <form onsubmit="event.preventDefault(); callFn('${form.fn}', this);">
    ${fields}
    <button type="submit" class="mod-submit">${form.submitLabel || "Run"}</button>
  </form>
  <div class="mod-output" id="output-${form.fn}"></div>
  <div class="mod-status" id="status-${form.fn}"></div>
</div>`;
  }

  // ── Param → HTML element mapping ───────────────────────────────────

  private static paramToElement(p: SchemaParam): HtmlElementType {
    switch (p.type) {
      case "bool":
        return "checkbox";
      case "int":
      case "float":
        return "number";
      case "list":
      case "dict":
        return "textarea";
      case "str": {
        const name = p.name.toLowerCase();
        if (name.includes("query") || name.includes("prompt") || name.includes("instruction") || name.includes("description") || name.includes("message") || name.includes("error")) {
          return "textarea";
        }
        if (name === "model") return "select";
        return "input";
      }
      default:
        return "input";
    }
  }

  private static paramToLabel(name: string): string {
    return name
      .replace(/_/g, " ")
      .replace(/\b\w/g, (c) => c.toUpperCase());
  }

  private static paramToAttrs(p: SchemaParam): Record<string, string> {
    const attrs: Record<string, string> = {};
    if (p.type === "int") attrs["step"] = "1";
    if (p.type === "float") attrs["step"] = "0.01";
    if (p.value === "_empty") attrs["required"] = "true";
    return attrs;
  }

  private static paramToOptions(p: SchemaParam): Array<{ label: string; value: string }> | undefined {
    if (p.name.toLowerCase() === "model") {
      return [
        { label: "Sonnet", value: "sonnet" },
        { label: "Opus", value: "opus" },
        { label: "Haiku", value: "haiku" },
      ];
    }
    if (p.type === "bool") {
      return [
        { label: "True", value: "true" },
        { label: "False", value: "false" },
      ];
    }
    return undefined;
  }

  // ── Bind to existing DOM elements ──────────────────────────────────

  /**
   * Mount a mod schema form into an existing DOM element.
   * Works with any HTML container — the protocol adapts to the target.
   */
  bindToElement(
    container: HTMLElement,
    fnName: string,
    schema: SchemaFn,
    onResult?: (result: unknown) => void
  ): { destroy: () => void } {
    const formSchema = ModProtocol.schemaToHtml(fnName, schema);
    container.innerHTML = ModProtocol.renderFormHtml(formSchema);

    const form = container.querySelector("form");
    if (!form) return { destroy: () => { container.innerHTML = ""; } };

    const handler = async (e: Event) => {
      e.preventDefault();
      const data = new FormData(form);
      const params: Record<string, unknown> = {};
      for (const [k, v] of data.entries()) {
        if (v !== "") params[k] = v;
      }

      const outputEl = container.querySelector(`#output-${fnName}`) as HTMLElement;
      const statusEl = container.querySelector(`#status-${fnName}`) as HTMLElement;
      const btn = form.querySelector("button[type=submit]") as HTMLButtonElement;

      if (btn) btn.disabled = true;
      if (statusEl) statusEl.textContent = "Running...";
      if (outputEl) { outputEl.textContent = ""; outputEl.className = "mod-output"; }

      try {
        const result = await this.callFn(fnName, params);
        if (outputEl) {
          outputEl.textContent = typeof result === "object" ? JSON.stringify(result, null, 2) : String(result);
          outputEl.classList.add("success");
        }
        if (statusEl) statusEl.textContent = "Done";
        onResult?.(result);
      } catch (err: unknown) {
        const msg = err instanceof Error ? err.message : String(err);
        if (outputEl) { outputEl.textContent = msg; outputEl.classList.add("error"); }
        if (statusEl) statusEl.textContent = "Failed";
      }
      if (btn) btn.disabled = false;
    };

    form.addEventListener("submit", handler);
    return {
      destroy: () => {
        form.removeEventListener("submit", handler);
        container.innerHTML = "";
      },
    };
  }

  /**
   * Render ALL schema functions into a container with navigation.
   */
  bindAllToElement(container: HTMLElement, config: ModConfig): { destroy: () => void } {
    const html = ModProtocol.generateHtmlPage(
      ModProtocol.configToHtml(config),
      config,
      this.baseUrl
    );
    // Extract just the body content (strip html/head/body tags)
    const bodyMatch = html.match(/<body[^>]*>([\s\S]*)<\/body>/);
    if (bodyMatch) {
      container.innerHTML = bodyMatch[1];
    } else {
      container.innerHTML = html;
    }
    return { destroy: () => { container.innerHTML = ""; } };
  }
}

export default ModProtocol;
