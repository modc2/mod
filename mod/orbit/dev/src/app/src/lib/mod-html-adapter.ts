/**
 * ModHtmlAdapter — Universal HTML adapter for the Mod Protocol
 *
 * Takes any module's config.json schema and produces:
 *   1. Typed form definitions from schema params
 *   2. HTML string rendering for any context (iframe, shadow DOM, server-side)
 *   3. React-compatible JSON structures for component rendering
 *   4. Table schemas for list/dict output types
 *   5. Full standalone page generation from any config.json
 *
 * The adapter normalizes the mod protocol's schema into a universal
 * intermediate representation that can target any HTML rendering context.
 */

import {
  ModConfig,
  SchemaFn,
  SchemaParam,
  HtmlFormSchema,
  HtmlFieldSchema,
  HtmlElementType,
  ModProtocol,
} from "./mod-protocol";

// ── Table schema (for output rendering) ──────────────────────────────

export interface TableColumn {
  key: string;
  label: string;
  type: "string" | "number" | "boolean" | "object" | "date";
  width?: string;
}

export interface TableSchema {
  columns: TableColumn[];
  sortable: boolean;
  filterable: boolean;
}

// ── Adapted module (the universal output) ────────────────────────────

export interface AdaptedModule {
  config: ModConfig;
  forms: HtmlFormSchema[];
  tables: Record<string, TableSchema>;
  nav: Array<{ fn: string; label: string; category: string }>;
  html: string;
  css: string;
}

// ── Category detection ───────────────────────────────────────────────

const CATEGORY_PATTERNS: Record<string, RegExp[]> = {
  "AI & Code": [/forward|ask|analyze|generate|refactor|debug|edit_file|run_task|batch/i],
  "Jobs": [/submit|jobs?$|cancel|delete_job|tail|bg/i],
  "Versioning": [/snapshot|changelog|version|restore/i],
  "Admin": [/owner|health|modules|repos|files|models|serve|test/i],
};

function categorize(fnName: string): string {
  for (const [cat, patterns] of Object.entries(CATEGORY_PATTERNS)) {
    if (patterns.some((p) => p.test(fnName))) return cat;
  }
  return "Other";
}

// ── Output type → table schema ───────────────────────────────────────

function outputToTable(fnName: string, schema: SchemaFn): TableSchema | null {
  const t = schema.output.type.toLowerCase();
  if (t !== "list") return null;

  // Infer columns from known patterns
  if (fnName === "jobs" || fnName === "bg_list") {
    return {
      columns: [
        { key: "id", label: "ID", type: "string", width: "200px" },
        { key: "status", label: "Status", type: "string", width: "100px" },
        { key: "prompt", label: "Prompt", type: "string" },
        { key: "model", label: "Model", type: "string", width: "80px" },
        { key: "created_at", label: "Created", type: "date", width: "150px" },
      ],
      sortable: true,
      filterable: true,
    };
  }
  if (fnName === "modules") {
    return {
      columns: [
        { key: "name", label: "Name", type: "string", width: "150px" },
        { key: "description", label: "Description", type: "string" },
        { key: "version", label: "Version", type: "string", width: "80px" },
        { key: "category", label: "Category", type: "string", width: "100px" },
      ],
      sortable: true,
      filterable: true,
    };
  }
  if (fnName === "changelog") {
    return {
      columns: [
        { key: "version", label: "Version", type: "string", width: "100px" },
        { key: "description", label: "Description", type: "string" },
        { key: "date", label: "Date", type: "date", width: "150px" },
        { key: "cid", label: "CID", type: "string", width: "200px" },
      ],
      sortable: true,
      filterable: false,
    };
  }

  // Generic fallback
  return {
    columns: [
      { key: "key", label: "Key", type: "string", width: "200px" },
      { key: "value", label: "Value", type: "string" },
    ],
    sortable: false,
    filterable: false,
  };
}

// ── Main adapter ─────────────────────────────────────────────────────

export class ModHtmlAdapter {
  /**
   * Adapt an entire module config into a universal HTML representation.
   */
  static adapt(config: ModConfig, apiUrl: string = "http://localhost:8820"): AdaptedModule {
    const forms = ModProtocol.configToHtml(config);
    const tables: Record<string, TableSchema> = {};
    const nav: Array<{ fn: string; label: string; category: string }> = [];

    for (const fn of config.fns) {
      const schema = config.schema[fn];
      if (!schema) continue;

      const table = outputToTable(fn, schema);
      if (table) tables[fn] = table;

      nav.push({
        fn,
        label: fn.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()),
        category: categorize(fn),
      });
    }

    const html = ModProtocol.generateHtmlPage(forms, config, apiUrl);
    const css = ModHtmlAdapter.generateCss(config);

    return { config, forms, tables, nav, html, css };
  }

  /**
   * Adapt a SINGLE function from any config into an HtmlFormSchema.
   */
  static adaptFn(fnName: string, config: ModConfig): HtmlFormSchema | null {
    const schema = config.schema[fnName];
    if (!schema) return null;
    return ModProtocol.schemaToHtml(fnName, schema, config);
  }

  /**
   * Given arbitrary JSON data and a table schema, render an HTML table string.
   */
  static renderTable(data: unknown[], tableSchema: TableSchema): string {
    if (!data || data.length === 0) return "<p>No data</p>";

    const headers = tableSchema.columns
      .map((c) => `<th style="${c.width ? `width:${c.width}` : ""}">${c.label}</th>`)
      .join("");

    const rows = data
      .map((row: any) => {
        const cells = tableSchema.columns
          .map((c) => {
            let val = row[c.key];
            if (val === null || val === undefined) val = "—";
            if (c.type === "date" && typeof val === "number") {
              val = new Date(val * 1000).toLocaleString();
            }
            if (c.type === "object" && typeof val === "object") {
              val = JSON.stringify(val);
            }
            return `<td>${String(val)}</td>`;
          })
          .join("");
        return `<tr>${cells}</tr>`;
      })
      .join("\n");

    return `<table><thead><tr>${headers}</tr></thead><tbody>${rows}</tbody></table>`;
  }

  /**
   * Generate CSS scoped to a module.
   */
  static generateCss(config: ModConfig): string {
    return `.mod-${config.name} {
  --mod-accent: #60a5fa;
  --mod-bg: #111118;
  --mod-border: #1e293b;
  font-family: 'SF Mono', Monaco, 'Courier New', monospace;
}
.mod-${config.name} .mod-form { background: var(--mod-bg); border: 1px solid var(--mod-border); border-radius: 8px; padding: 1.5rem; margin-bottom: 1rem; }
.mod-${config.name} .mod-field input,
.mod-${config.name} .mod-field textarea,
.mod-${config.name} .mod-field select {
  width: 100%; padding: 0.5rem; background: rgba(255,255,255,0.04);
  border: 1px solid var(--mod-border); border-radius: 4px; color: #e0e0e0;
  font-family: inherit; font-size: 0.85rem;
}
.mod-${config.name} .mod-submit {
  background: var(--mod-accent); color: #000; border: none; padding: 0.6rem 1.5rem;
  border-radius: 4px; cursor: pointer; font-weight: 600;
}`;
  }

  /**
   * Validate form values against the schema before submission.
   */
  static validate(
    values: Record<string, unknown>,
    schema: SchemaFn
  ): { valid: boolean; errors: Record<string, string> } {
    const errors: Record<string, string> = {};

    for (const p of schema.input) {
      const val = values[p.name];
      const required = p.value === "_empty";

      if (required && (val === undefined || val === null || val === "")) {
        errors[p.name] = `${p.name} is required`;
        continue;
      }

      if (val === undefined || val === null || val === "") continue;

      switch (p.type) {
        case "int":
          if (isNaN(parseInt(String(val), 10))) errors[p.name] = `${p.name} must be an integer`;
          break;
        case "float":
          if (isNaN(parseFloat(String(val)))) errors[p.name] = `${p.name} must be a number`;
          break;
        case "bool":
          if (!["true", "false", "1", "0"].includes(String(val).toLowerCase()) && typeof val !== "boolean")
            errors[p.name] = `${p.name} must be true or false`;
          break;
        case "dict":
          if (typeof val === "string") {
            try { JSON.parse(val); } catch { errors[p.name] = `${p.name} must be valid JSON`; }
          }
          break;
        case "list":
          if (typeof val === "string") {
            try { JSON.parse(val); } catch { /* allow comma-separated */ }
          }
          break;
      }
    }

    return { valid: Object.keys(errors).length === 0, errors };
  }

  /**
   * Extract form defaults from a schema function definition.
   */
  static getDefaults(schema: SchemaFn): Record<string, unknown> {
    const defaults: Record<string, unknown> = {};
    for (const p of schema.input) {
      if (p.value !== null && p.value !== "_empty") {
        defaults[p.name] = p.value;
      }
    }
    return defaults;
  }
}

export default ModHtmlAdapter;
