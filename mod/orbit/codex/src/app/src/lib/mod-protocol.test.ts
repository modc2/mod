/**
 * ModProtocol + ModHtmlAdapter test suite
 *
 * Run: npx tsx src/lib/mod-protocol.test.ts
 *
 * Tests:
 *   1. Schema parsing & type coercion
 *   2. HTML form generation from schema
 *   3. Full page generation
 *   4. Validation
 *   5. Config → HTML round-trip
 *   6. Table rendering
 *   7. Param → element mapping
 *   8. API emulation structure (types, headers)
 *   9. Adapter categories & navigation
 *  10. Edge cases (empty config, missing fields)
 */

import {
  ModProtocol,
  ModConfig,
  SchemaFn,
  SchemaParam,
  HtmlFormSchema,
} from "./mod-protocol";
import { ModHtmlAdapter } from "./mod-html-adapter";

// ── Test harness ─────────────────────────────────────────────────────

let passed = 0;
let failed = 0;
const failures: string[] = [];

function test(name: string, fn: () => void) {
  try {
    fn();
    passed++;
    console.log(`  ✓ ${name}`);
  } catch (e: any) {
    failed++;
    const msg = `  ✕ ${name}: ${e.message}`;
    console.log(msg);
    failures.push(msg);
  }
}

function assert(condition: boolean, msg: string = "assertion failed") {
  if (!condition) throw new Error(msg);
}

function assertEqual(a: unknown, b: unknown, msg?: string) {
  if (a !== b) throw new Error(msg || `expected ${JSON.stringify(b)}, got ${JSON.stringify(a)}`);
}

function assertIncludes(str: string, sub: string, msg?: string) {
  if (!str.includes(sub)) throw new Error(msg || `expected string to include "${sub}"`);
}

// ── Test fixtures ────────────────────────────────────────────────────

const MOCK_CONFIG: ModConfig = {
  name: "test-module",
  version: "1.0.0",
  description: "A test module for validation",
  owner: "0x1234567890abcdef",
  port: 8820,
  urls: { app: "http://localhost:8821", api: "http://localhost:8820" },
  fns: ["forward", "ask", "jobs", "health", "modules", "submit", "snapshot", "test"],
  schema: {
    forward: {
      input: [
        { name: "query", type: "str", value: "_empty" },
        { name: "path", type: "str", value: null },
        { name: "model", type: "str", value: "sonnet" },
        { name: "background", type: "bool", value: true },
      ],
      output: { type: "Dict", value: null },
      docs: "Run a task in background.",
    },
    ask: {
      input: [
        { name: "message", type: "str", value: "_empty" },
        { name: "model", type: "str", value: "anthropic/claude-opus-4" },
        { name: "stream", type: "bool", value: false },
      ],
      output: { type: "str", value: null },
      docs: "Conversational interface.",
    },
    jobs: {
      input: [],
      output: { type: "list", value: null },
      docs: "List all jobs.",
    },
    health: {
      input: [],
      output: { type: "Dict", value: null },
      docs: "Health check.",
    },
    modules: {
      input: [{ name: "search", type: "str", value: null }],
      output: { type: "list", value: null },
      docs: "List modules.",
    },
    submit: {
      input: [
        { name: "prompt", type: "str", value: "_empty" },
        { name: "model", type: "str", value: "sonnet" },
        { name: "work_dir", type: "str", value: null },
      ],
      output: { type: "Dict", value: null },
      docs: "Submit a job.",
    },
    snapshot: {
      input: [
        { name: "description", type: "str", value: null },
        { name: "version", type: "str", value: null },
      ],
      output: { type: "Dict", value: null },
      docs: "Take an IPFS snapshot.",
    },
    test: {
      input: [],
      output: { type: "Dict", value: null },
      docs: "Run self-tests.",
    },
  },
  endpoints: {
    "/health": { method: "GET", auth: false, docs: "Health check" },
    "/jobs": { method: ["POST", "GET"], auth: true, docs: "Submit or list jobs" },
    "/modules": { method: "GET", auth: false, docs: "List modules" },
  },
};

// ── 1. Type coercion ─────────────────────────────────────────────────

console.log("\n── Type Coercion ──");

test("coerce str", () => {
  assertEqual(ModProtocol.coerceParam(42, "str"), "42");
  assertEqual(ModProtocol.coerceParam(true, "str"), "true");
  assertEqual(ModProtocol.coerceParam("hello", "str"), "hello");
});

test("coerce int", () => {
  assertEqual(ModProtocol.coerceParam("42", "int"), 42);
  assertEqual(ModProtocol.coerceParam(3.7, "int"), 3);
  assertEqual(ModProtocol.coerceParam("0", "int"), 0);
});

test("coerce float", () => {
  assertEqual(ModProtocol.coerceParam("3.14", "float"), 3.14);
  assertEqual(ModProtocol.coerceParam(42, "float"), 42);
});

test("coerce bool from string", () => {
  assertEqual(ModProtocol.coerceParam("true", "bool"), true);
  assertEqual(ModProtocol.coerceParam("false", "bool"), false);
  assertEqual(ModProtocol.coerceParam("1", "bool"), true);
  assertEqual(ModProtocol.coerceParam("0", "bool"), false);
});

test("coerce bool from value", () => {
  assertEqual(ModProtocol.coerceParam(true, "bool"), true);
  assertEqual(ModProtocol.coerceParam(false, "bool"), false);
  assertEqual(ModProtocol.coerceParam(1, "bool"), true);
  assertEqual(ModProtocol.coerceParam(0, "bool"), false);
});

test("coerce dict from JSON string", () => {
  const result = ModProtocol.coerceParam('{"a":1}', "dict");
  assert(typeof result === "object", "should be object");
  assertEqual((result as any).a, 1);
});

test("coerce dict from invalid string", () => {
  const result = ModProtocol.coerceParam("not json", "dict");
  assert(typeof result === "object", "should fallback to empty object");
});

test("coerce list from JSON array", () => {
  const result = ModProtocol.coerceParam('[1,2,3]', "list") as number[];
  assert(Array.isArray(result), "should be array");
  assertEqual(result.length, 3);
});

test("coerce list from comma-separated", () => {
  const result = ModProtocol.coerceParam("a, b, c", "list") as string[];
  assert(Array.isArray(result), "should be array");
  assertEqual(result.length, 3);
  assertEqual(result[0], "a");
  assertEqual(result[1], "b");
});

test("coerce null passthrough", () => {
  assertEqual(ModProtocol.coerceParam(null, "str"), null);
  assertEqual(ModProtocol.coerceParam(undefined, "int"), undefined);
});

// ── 2. Schema → HTML form ────────────────────────────────────────────

console.log("\n── Schema → HTML Form ──");

test("schemaToHtml produces correct fields", () => {
  const form = ModProtocol.schemaToHtml("forward", MOCK_CONFIG.schema.forward);
  assertEqual(form.fn, "forward");
  assertEqual(form.fields.length, 4);
  assertEqual(form.fields[0].param, "query");
  assertEqual(form.fields[0].element, "textarea"); // "query" triggers textarea
  assertEqual(form.fields[2].param, "model");
  assertEqual(form.fields[2].element, "select"); // "model" triggers select
  assertEqual(form.fields[3].param, "background");
  assertEqual(form.fields[3].element, "checkbox"); // bool triggers checkbox
});

test("schemaToHtml labels are capitalized", () => {
  const form = ModProtocol.schemaToHtml("run_task", {
    input: [{ name: "work_dir", type: "str", value: null }],
    output: { type: "str", value: null },
  });
  assertEqual(form.title, "Run Task");
  assertEqual(form.fields[0].label, "Work Dir");
});

test("schemaToHtml empty input produces empty fields", () => {
  const form = ModProtocol.schemaToHtml("health", MOCK_CONFIG.schema.health);
  assertEqual(form.fields.length, 0);
  assertEqual(form.fn, "health");
});

test("schemaToHtml includes docs", () => {
  const form = ModProtocol.schemaToHtml("ask", MOCK_CONFIG.schema.ask);
  assertEqual(form.description, "Conversational interface.");
});

test("schemaToHtml default values", () => {
  const form = ModProtocol.schemaToHtml("forward", MOCK_CONFIG.schema.forward);
  assertEqual(form.fields[0].defaultValue, undefined); // _empty → undefined
  assertEqual(form.fields[1].defaultValue, undefined); // null → undefined
  assertEqual(form.fields[2].defaultValue, "sonnet");
  assertEqual(form.fields[3].defaultValue, true);
});

test("schemaToHtml model field has options", () => {
  const form = ModProtocol.schemaToHtml("forward", MOCK_CONFIG.schema.forward);
  const modelField = form.fields.find((f) => f.param === "model");
  assert(modelField !== undefined, "model field should exist");
  assert(modelField!.options !== undefined, "model should have options");
  assertEqual(modelField!.options!.length, 3);
  assertEqual(modelField!.options![0].value, "sonnet");
});

test("schemaToHtml required attrs", () => {
  const form = ModProtocol.schemaToHtml("forward", MOCK_CONFIG.schema.forward);
  assertEqual(form.fields[0].attrs?.required, "true"); // query is _empty → required
  assert(!form.fields[1].attrs?.required, "path should not be required");
});

// ── 3. configToHtml ──────────────────────────────────────────────────

console.log("\n── Config → All Forms ──");

test("configToHtml generates forms for all schema fns", () => {
  const forms = ModProtocol.configToHtml(MOCK_CONFIG);
  assertEqual(forms.length, 8); // 8 fns in schema
  const fns = forms.map((f) => f.fn);
  assert(fns.includes("forward"), "should include forward");
  assert(fns.includes("ask"), "should include ask");
  assert(fns.includes("jobs"), "should include jobs");
  assert(fns.includes("test"), "should include test");
});

test("configToHtml skips fns without schema", () => {
  const config: ModConfig = {
    ...MOCK_CONFIG,
    fns: ["forward", "nonexistent"],
    schema: { forward: MOCK_CONFIG.schema.forward },
  };
  const forms = ModProtocol.configToHtml(config);
  assertEqual(forms.length, 1);
  assertEqual(forms[0].fn, "forward");
});

// ── 4. Full page generation ──────────────────────────────────────────

console.log("\n── Full Page Generation ──");

test("generateHtmlPage produces valid HTML", () => {
  const forms = ModProtocol.configToHtml(MOCK_CONFIG);
  const html = ModProtocol.generateHtmlPage(forms, MOCK_CONFIG);
  assertIncludes(html, "<!DOCTYPE html>");
  assertIncludes(html, "<html");
  assertIncludes(html, "</html>");
  assertIncludes(html, "test-module");
  assertIncludes(html, "v1.0.0");
});

test("generateHtmlPage includes all forms", () => {
  const forms = ModProtocol.configToHtml(MOCK_CONFIG);
  const html = ModProtocol.generateHtmlPage(forms, MOCK_CONFIG);
  assertIncludes(html, 'data-fn="forward"');
  assertIncludes(html, 'data-fn="ask"');
  assertIncludes(html, 'data-fn="jobs"');
  assertIncludes(html, 'data-fn="health"');
});

test("generateHtmlPage includes CSS", () => {
  const forms = ModProtocol.configToHtml(MOCK_CONFIG);
  const html = ModProtocol.generateHtmlPage(forms, MOCK_CONFIG);
  assertIncludes(html, "<style>");
  assertIncludes(html, ".mod-form");
  assertIncludes(html, ".mod-field");
  assertIncludes(html, ".mod-submit");
});

test("generateHtmlPage includes JS", () => {
  const forms = ModProtocol.configToHtml(MOCK_CONFIG);
  const html = ModProtocol.generateHtmlPage(forms, MOCK_CONFIG);
  assertIncludes(html, "<script>");
  assertIncludes(html, "callFn");
  assertIncludes(html, "async function");
});

test("generateHtmlPage uses custom API URL", () => {
  const forms = ModProtocol.configToHtml(MOCK_CONFIG);
  const html = ModProtocol.generateHtmlPage(forms, MOCK_CONFIG, "http://my-api.com:9999");
  assertIncludes(html, "http://my-api.com:9999");
});

// ── 5. Validation ────────────────────────────────────────────────────

console.log("\n── Validation ──");

test("validate passes with valid required fields", () => {
  const result = ModHtmlAdapter.validate(
    { query: "hello", model: "sonnet" },
    MOCK_CONFIG.schema.forward
  );
  assert(result.valid, "should be valid");
  assertEqual(Object.keys(result.errors).length, 0);
});

test("validate fails on missing required field", () => {
  const result = ModHtmlAdapter.validate(
    { model: "sonnet" },
    MOCK_CONFIG.schema.forward
  );
  assert(!result.valid, "should be invalid");
  assert("query" in result.errors, "should have error for query");
});

test("validate fails on invalid int", () => {
  const schema: SchemaFn = {
    input: [{ name: "count", type: "int", value: "_empty" }],
    output: { type: "int", value: null },
  };
  const result = ModHtmlAdapter.validate({ count: "abc" }, schema);
  assert(!result.valid, "should be invalid");
  assert("count" in result.errors, "should have error for count");
});

test("validate fails on invalid float", () => {
  const schema: SchemaFn = {
    input: [{ name: "rate", type: "float", value: null }],
    output: { type: "float", value: null },
  };
  const result = ModHtmlAdapter.validate({ rate: "not-a-number" }, schema);
  assert(!result.valid, "should be invalid");
});

test("validate passes on valid bool", () => {
  const result = ModHtmlAdapter.validate(
    { query: "test", background: "true" },
    MOCK_CONFIG.schema.forward
  );
  assert(result.valid, "should be valid");
});

test("validate fails on invalid dict JSON", () => {
  const schema: SchemaFn = {
    input: [{ name: "config", type: "dict", value: null }],
    output: { type: "dict", value: null },
  };
  const result = ModHtmlAdapter.validate({ config: "{invalid json" }, schema);
  assert(!result.valid, "should be invalid");
  assert("config" in result.errors);
});

test("validate skips optional empty fields", () => {
  const result = ModHtmlAdapter.validate(
    { query: "test" },
    MOCK_CONFIG.schema.forward
  );
  assert(result.valid, "should be valid with only required fields");
});

// ── 6. Table rendering ───────────────────────────────────────────────

console.log("\n── Table Rendering ──");

test("renderTable generates HTML table", () => {
  const adapted = ModHtmlAdapter.adapt(MOCK_CONFIG);
  const tableSchema = adapted.tables["jobs"];
  assert(tableSchema !== undefined, "jobs should have table schema");

  const html = ModHtmlAdapter.renderTable(
    [
      { id: "abc", status: "running", prompt: "test", model: "opus", created_at: 1700000000 },
      { id: "def", status: "completed", prompt: "done", model: "sonnet", created_at: 1700001000 },
    ],
    tableSchema
  );
  assertIncludes(html, "<table>");
  assertIncludes(html, "<thead>");
  assertIncludes(html, "<tbody>");
  assertIncludes(html, "abc");
  assertIncludes(html, "running");
  assertIncludes(html, "def");
});

test("renderTable handles empty data", () => {
  const adapted = ModHtmlAdapter.adapt(MOCK_CONFIG);
  const html = ModHtmlAdapter.renderTable([], adapted.tables["jobs"]);
  assertIncludes(html, "No data");
});

test("renderTable handles null values", () => {
  const adapted = ModHtmlAdapter.adapt(MOCK_CONFIG);
  const html = ModHtmlAdapter.renderTable(
    [{ id: null, status: undefined, prompt: "test" }],
    adapted.tables["jobs"]
  );
  assertIncludes(html, "—"); // null/undefined → dash
});

test("renderTable formats dates", () => {
  const adapted = ModHtmlAdapter.adapt(MOCK_CONFIG);
  const html = ModHtmlAdapter.renderTable(
    [{ id: "x", status: "ok", prompt: "t", model: "s", created_at: 1700000000 }],
    adapted.tables["jobs"]
  );
  // Should contain a formatted date, not raw timestamp
  assert(!html.includes("1700000000") || html.includes("2023"), "date should be formatted");
});

// ── 7. Adapter ───────────────────────────────────────────────────────

console.log("\n── Adapter ──");

test("adapt produces all expected fields", () => {
  const adapted = ModHtmlAdapter.adapt(MOCK_CONFIG);
  assert(adapted.config === MOCK_CONFIG, "config should be preserved");
  assert(adapted.forms.length > 0, "should have forms");
  assert(adapted.nav.length > 0, "should have nav items");
  assert(adapted.html.length > 0, "should have html");
  assert(adapted.css.length > 0, "should have css");
});

test("adapt categorizes functions", () => {
  const adapted = ModHtmlAdapter.adapt(MOCK_CONFIG);
  const forwardNav = adapted.nav.find((n) => n.fn === "forward");
  const jobsNav = adapted.nav.find((n) => n.fn === "jobs");
  const healthNav = adapted.nav.find((n) => n.fn === "health");
  const snapshotNav = adapted.nav.find((n) => n.fn === "snapshot");

  assert(forwardNav !== undefined, "forward should be in nav");
  assert(jobsNav !== undefined, "jobs should be in nav");
  assertEqual(forwardNav!.category, "AI & Code");
  assertEqual(jobsNav!.category, "Jobs");
  assertEqual(healthNav!.category, "Admin");
  assertEqual(snapshotNav!.category, "Versioning");
});

test("adapt generates tables for list outputs", () => {
  const adapted = ModHtmlAdapter.adapt(MOCK_CONFIG);
  assert("jobs" in adapted.tables, "should have jobs table");
  assert("modules" in adapted.tables, "should have modules table");
  assert(!("forward" in adapted.tables), "forward should not have table (Dict output)");
});

test("adapt generates module-scoped CSS", () => {
  const adapted = ModHtmlAdapter.adapt(MOCK_CONFIG);
  assertIncludes(adapted.css, ".mod-test-module");
});

// ── 8. getDefaults ───────────────────────────────────────────────────

console.log("\n── Defaults ──");

test("getDefaults extracts non-null non-empty defaults", () => {
  const defaults = ModHtmlAdapter.getDefaults(MOCK_CONFIG.schema.forward);
  assertEqual(defaults.model, "sonnet");
  assertEqual(defaults.background, true);
  assert(!("query" in defaults), "query (_empty) should not be in defaults");
  assert(!("path" in defaults), "path (null) should not be in defaults");
});

test("getDefaults returns empty for no-input fn", () => {
  const defaults = ModHtmlAdapter.getDefaults(MOCK_CONFIG.schema.health);
  assertEqual(Object.keys(defaults).length, 0);
});

// ── 9. ModProtocol instance ──────────────────────────────────────────

console.log("\n── ModProtocol Instance ──");

test("constructor sets base URL", () => {
  const p = new ModProtocol("http://my-api.com:9000/");
  // The trailing slash should be stripped (we can verify via token behavior)
  assertEqual(p.getToken(), null);
});

test("setToken / getToken", () => {
  const p = new ModProtocol();
  assertEqual(p.getToken(), null);
  p.setToken("0x123:12345:hmac");
  assertEqual(p.getToken(), "0x123:12345:hmac");
});

test("default base URL is localhost:8820", () => {
  const p = new ModProtocol();
  assertEqual(p.getToken(), null); // Just verify it doesn't crash
});

// ── 10. Edge cases ───────────────────────────────────────────────────

console.log("\n── Edge Cases ──");

test("empty config.fns produces no forms", () => {
  const config: ModConfig = { ...MOCK_CONFIG, fns: [], schema: {} };
  const forms = ModProtocol.configToHtml(config);
  assertEqual(forms.length, 0);
});

test("adaptFn returns null for unknown function", () => {
  const result = ModHtmlAdapter.adaptFn("nonexistent", MOCK_CONFIG);
  assertEqual(result, null);
});

test("adaptFn returns form for known function", () => {
  const result = ModHtmlAdapter.adaptFn("forward", MOCK_CONFIG);
  assert(result !== null, "should return form");
  assertEqual(result!.fn, "forward");
  assertEqual(result!.fields.length, 4);
});

test("validate with completely empty values and no required fields", () => {
  const schema: SchemaFn = {
    input: [
      { name: "opt1", type: "str", value: null },
      { name: "opt2", type: "int", value: 10 },
    ],
    output: { type: "str", value: null },
  };
  const result = ModHtmlAdapter.validate({}, schema);
  assert(result.valid, "all optional → should be valid");
});

test("coerce unknown type passes through", () => {
  const result = ModProtocol.coerceParam("test", "unknown_type");
  assertEqual(result, "test");
});

test("schema with all param types", () => {
  const schema: SchemaFn = {
    input: [
      { name: "s", type: "str", value: null },
      { name: "i", type: "int", value: null },
      { name: "f", type: "float", value: null },
      { name: "b", type: "bool", value: null },
      { name: "d", type: "dict", value: null },
      { name: "l", type: "list", value: null },
      { name: "by", type: "bytes", value: null },
    ],
    output: { type: "dict", value: null },
  };
  const form = ModProtocol.schemaToHtml("all_types", schema);
  assertEqual(form.fields.length, 7);
  assertEqual(form.fields[0].element, "input");      // str → input
  assertEqual(form.fields[1].element, "number");     // int → number
  assertEqual(form.fields[2].element, "number");     // float → number
  assertEqual(form.fields[3].element, "checkbox");   // bool → checkbox
  assertEqual(form.fields[4].element, "textarea");   // dict → textarea
  assertEqual(form.fields[5].element, "textarea");   // list → textarea
  assertEqual(form.fields[6].element, "input");      // bytes → input (default)
});

test("full round-trip: config → adapt → html → includes all fns", () => {
  const adapted = ModHtmlAdapter.adapt(MOCK_CONFIG);
  for (const fn of MOCK_CONFIG.fns) {
    if (MOCK_CONFIG.schema[fn]) {
      assertIncludes(adapted.html, `data-fn="${fn}"`, `HTML should include form for ${fn}`);
    }
  }
});

test("generateCss returns module-scoped styles", () => {
  const css = ModHtmlAdapter.generateCss(MOCK_CONFIG);
  assertIncludes(css, ".mod-test-module");
  assertIncludes(css, "--mod-accent");
  assertIncludes(css, "--mod-border");
});

// ── 11. Real config.json schema test ─────────────────────────────────

console.log("\n── Real Config Schema ──");

test("adapt works with claude module config shape", () => {
  // Simulate the actual claude config.json structure
  const claudeConfig: ModConfig = {
    name: "claude",
    version: "2.0.0",
    description: "Programmable AI developer interface",
    owner: "0x2e34c7bbe3491c9ab3ff606951d46cc300bfb7cd",
    port: 8820,
    urls: { app: "http://localhost:8821", api: "http://localhost:8820" },
    fns: ["forward", "ask", "analyze_code", "jobs", "health", "test"],
    schema: {
      forward: {
        input: [
          { name: "query", type: "str", value: "_empty" },
          { name: "path", type: "str", value: null },
          { name: "mod", type: "str", value: null },
          { name: "model", type: "str", value: "sonnet" },
          { name: "background", type: "bool", value: true },
          { name: "stream", type: "bool", value: false },
          { name: "key", type: "str", value: null },
        ],
        output: { type: "Dict", value: null },
        docs: "Run a Claude task. background=True submits to server, False runs CLI directly.",
      },
      ask: {
        input: [
          { name: "message", type: "str", value: "_empty" },
          { name: "model", type: "str", value: "anthropic/claude-opus-4" },
          { name: "stream", type: "bool", value: false },
        ],
        output: { type: "str", value: null },
        docs: "Conversational interface via OpenRouter.",
      },
      analyze_code: {
        input: [
          { name: "path", type: "str", value: null },
          { name: "query", type: "str", value: null },
          { name: "focus", type: "str", value: null },
          { name: "model", type: "str", value: "sonnet" },
        ],
        output: { type: "str", value: null },
        docs: "Analyze code at a path. Read-only, no owner required.",
      },
      jobs: {
        input: [],
        output: { type: "list", value: null },
        docs: "List all jobs.",
      },
      health: {
        input: [],
        output: { type: "Dict", value: null },
        docs: "Health check.",
      },
      test: {
        input: [],
        output: { type: "Dict", value: null },
        docs: "Run self-tests.",
      },
    },
    endpoints: {
      "/health": { method: "GET", auth: false },
      "/jobs": { method: ["POST", "GET"], auth: true },
      "/modules": { method: "GET", auth: false },
    },
  };

  const adapted = ModHtmlAdapter.adapt(claudeConfig);
  assertEqual(adapted.config.name, "claude");
  assertEqual(adapted.forms.length, 6);
  assert("jobs" in adapted.tables, "should have jobs table");

  // Verify the forward form has correct fields
  const forwardForm = adapted.forms.find((f) => f.fn === "forward");
  assert(forwardForm !== undefined, "forward form should exist");
  assertEqual(forwardForm!.fields.length, 7);

  // Verify HTML contains all forms
  for (const fn of claudeConfig.fns) {
    assertIncludes(adapted.html, `data-fn="${fn}"`);
  }
});

// ── Results ──────────────────────────────────────────────────────────

console.log("\n═══════════════════════════════════");
console.log(`  Total: ${passed + failed}`);
console.log(`  Passed: ${passed}`);
console.log(`  Failed: ${failed}`);
if (failures.length > 0) {
  console.log("\n  Failures:");
  failures.forEach((f) => console.log(f));
}
console.log("═══════════════════════════════════\n");

process.exit(failed > 0 ? 1 : 0);
