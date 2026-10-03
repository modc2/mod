//! A reader for the WebAssembly binary format.
//!
//! The registry accepts *any* wasm module, so before it stores one it has to
//! be able to say what the thing is: what it imports (which decides the host
//! shim the runner has to build), what it exports (which decides whether it is
//! a game, a player, a model or just a library), and how much memory it wants.
//!
//! This is a structural read, not a validator — sections are walked, code
//! bodies are skipped. A file that parses here is well-formed enough to
//! describe; whether it *runs* is the engine's call, and the engine is the
//! browser's.
//!
//! No dependencies on purpose. The format is small, and a parser you can read
//! end to end beats a crate you have to trust for the one thing this module is
//! about.

use serde_json::{json, Value};

const MAGIC: &[u8] = b"\0asm";

pub struct Reader<'a> {
    b: &'a [u8],
    pos: usize,
}

type R<T> = Result<T, String>;

impl<'a> Reader<'a> {
    fn new(b: &'a [u8]) -> Self {
        Reader { b, pos: 0 }
    }

    fn left(&self) -> usize {
        self.b.len().saturating_sub(self.pos)
    }

    fn byte(&mut self) -> R<u8> {
        let v = *self.b.get(self.pos).ok_or("unexpected end of module")?;
        self.pos += 1;
        Ok(v)
    }

    fn take(&mut self, n: usize) -> R<&'a [u8]> {
        if self.left() < n {
            return Err("unexpected end of module".into());
        }
        let s = &self.b[self.pos..self.pos + n];
        self.pos += n;
        Ok(s)
    }

    /// Unsigned LEB128, capped at five bytes like a u32 on the wire.
    fn u32(&mut self) -> R<u32> {
        let mut out: u64 = 0;
        for shift in 0..5 {
            let byte = self.byte()?;
            out |= ((byte & 0x7f) as u64) << (shift * 7);
            if byte & 0x80 == 0 {
                return u32::try_from(out).map_err(|_| "oversized LEB128 integer".to_string());
            }
        }
        Err("malformed LEB128 integer".into())
    }

    /// Signed LEB128 — only needed to skip past global initialisers.
    fn i64(&mut self) -> R<i64> {
        let mut out: i64 = 0;
        let mut shift = 0u32;
        loop {
            let byte = self.byte()?;
            out |= ((byte & 0x7f) as i64) << shift;
            shift += 7;
            if byte & 0x80 == 0 {
                if shift < 64 && byte & 0x40 != 0 {
                    out |= -1i64 << shift;
                }
                return Ok(out);
            }
            if shift >= 64 {
                return Err("malformed signed LEB128".into());
            }
        }
    }

    fn name(&mut self) -> R<String> {
        let n = self.u32()? as usize;
        let raw = self.take(n)?;
        Ok(String::from_utf8_lossy(raw).into_owned())
    }
}

fn valtype(code: u8) -> &'static str {
    match code {
        0x7f => "i32",
        0x7e => "i64",
        0x7d => "f32",
        0x7c => "f64",
        0x7b => "v128",
        0x70 => "funcref",
        0x6f => "externref",
        _ => "?",
    }
}

#[derive(Clone, Default)]
pub struct FuncType {
    pub params: Vec<&'static str>,
    pub results: Vec<&'static str>,
}

impl FuncType {
    /// `(i32, i32) -> i64` — the shape a caller has to satisfy, at a glance.
    pub fn signature(&self) -> String {
        format!(
            "({}) -> {}",
            self.params.join(", "),
            if self.results.is_empty() {
                "()".to_string()
            } else {
                self.results.join(", ")
            }
        )
    }
}

#[derive(Clone)]
pub struct Import {
    pub module: String,
    pub name: String,
    /// func | table | memory | global
    pub kind: &'static str,
    pub sig: Option<FuncType>,
}

#[derive(Clone)]
pub struct Export {
    pub name: String,
    pub kind: &'static str,
    pub index: u32,
    pub sig: Option<FuncType>,
}

#[derive(Default)]
pub struct Module {
    pub version: u32,
    pub imports: Vec<Import>,
    pub exports: Vec<Export>,
    /// Declared in pages of 64 KiB, either defined here or imported.
    pub memory_pages: Option<(u32, Option<u32>)>,
    pub memory_imported: bool,
    pub func_count: usize,
    pub start: Option<u32>,
    pub custom_sections: Vec<(String, usize)>,
    pub size: usize,
}

/// Section order in the spec, used only to name what we walked past.
fn section_name(id: u8) -> &'static str {
    match id {
        0 => "custom",
        1 => "type",
        2 => "import",
        3 => "function",
        4 => "table",
        5 => "memory",
        6 => "global",
        7 => "export",
        8 => "start",
        9 => "element",
        10 => "code",
        11 => "data",
        12 => "data count",
        13 => "tag",
        _ => "unknown",
    }
}

fn limits(r: &mut Reader) -> R<(u32, Option<u32>)> {
    let flags = r.byte()?;
    let min = r.u32()?;
    // Bit 0 is "has max"; the rest carry shared/64-bit markers we only skip.
    let max = if flags & 0x01 != 0 { Some(r.u32()?) } else { None };
    Ok((min, max))
}

fn functype(r: &mut Reader) -> R<FuncType> {
    if r.byte()? != 0x60 {
        return Err("type section holds something that is not a function type".into());
    }
    let mut t = FuncType::default();
    let n = r.u32()?;
    for _ in 0..n {
        t.params.push(valtype(r.byte()?));
    }
    let n = r.u32()?;
    for _ in 0..n {
        t.results.push(valtype(r.byte()?));
    }
    Ok(t)
}

/// Read a module's structure. Code bodies are skipped by length.
pub fn parse(bytes: &[u8]) -> R<Module> {
    let mut r = Reader::new(bytes);
    if r.take(4)? != MAGIC {
        return Err("not a WebAssembly module (bad magic — a .wat text file is not a .wasm)".into());
    }
    let version = u32::from_le_bytes(r.take(4)?.try_into().unwrap_or([0; 4]));
    if version != 1 {
        return Err(format!("unsupported wasm version {version}"));
    }

    let mut m = Module {
        version,
        size: bytes.len(),
        ..Default::default()
    };
    let mut types: Vec<FuncType> = Vec::new();
    // Exports index into the imported functions first, then the ones defined
    // here — so both lists are needed to give an export its signature.
    let mut imported_funcs: Vec<u32> = Vec::new();
    let mut local_funcs: Vec<u32> = Vec::new();

    while r.left() > 0 {
        let id = r.byte()?;
        let len = r.u32()? as usize;
        if r.left() < len {
            return Err(format!("{} section runs past the end of the file", section_name(id)));
        }
        let body = r.take(len)?;
        let mut s = Reader::new(body);

        match id {
            0 => {
                let name = s.name().unwrap_or_else(|_| "?".into());
                m.custom_sections.push((name, len));
            }
            1 => {
                let n = s.u32()?;
                for _ in 0..n {
                    types.push(functype(&mut s)?);
                }
            }
            2 => {
                let n = s.u32()?;
                for _ in 0..n {
                    let module = s.name()?;
                    let name = s.name()?;
                    let kind = s.byte()?;
                    let (kind, sig) = match kind {
                        0x00 => {
                            let idx = s.u32()?;
                            imported_funcs.push(idx);
                            ("func", types.get(idx as usize).cloned())
                        }
                        0x01 => {
                            s.byte()?; // reftype
                            limits(&mut s)?;
                            ("table", None)
                        }
                        0x02 => {
                            let (min, max) = limits(&mut s)?;
                            m.memory_imported = true;
                            m.memory_pages.get_or_insert((min, max));
                            ("memory", None)
                        }
                        0x03 => {
                            s.byte()?; // valtype
                            s.byte()?; // mutability
                            ("global", None)
                        }
                        other => return Err(format!("unknown import kind 0x{other:02x}")),
                    };
                    m.imports.push(Import { module, name, kind, sig });
                }
            }
            3 => {
                let n = s.u32()?;
                for _ in 0..n {
                    local_funcs.push(s.u32()?);
                }
            }
            5 => {
                let n = s.u32()?;
                for i in 0..n {
                    let lim = limits(&mut s)?;
                    if i == 0 && !m.memory_imported {
                        m.memory_pages = Some(lim);
                    }
                }
            }
            6 => {
                let n = s.u32()?;
                for _ in 0..n {
                    s.byte()?; // valtype
                    s.byte()?; // mutability
                    skip_const_expr(&mut s)?;
                }
            }
            7 => {
                let n = s.u32()?;
                for _ in 0..n {
                    let name = s.name()?;
                    let kind = s.byte()?;
                    let index = s.u32()?;
                    let kind = match kind {
                        0x00 => "func",
                        0x01 => "table",
                        0x02 => "memory",
                        0x03 => "global",
                        other => return Err(format!("unknown export kind 0x{other:02x}")),
                    };
                    let sig = if kind == "func" {
                        func_signature(index, &imported_funcs, &local_funcs, &types)
                    } else {
                        None
                    };
                    m.exports.push(Export { name, kind, index, sig });
                }
            }
            8 => m.start = Some(s.u32()?),
            10 => m.func_count = s.u32()? as usize,
            _ => {}
        }
    }

    if m.func_count == 0 {
        m.func_count = local_funcs.len();
    }
    Ok(m)
}

fn func_signature(
    index: u32,
    imported: &[u32],
    local: &[u32],
    types: &[FuncType],
) -> Option<FuncType> {
    let i = index as usize;
    let type_idx = if i < imported.len() {
        imported[i]
    } else {
        *local.get(i - imported.len())?
    };
    types.get(type_idx as usize).cloned()
}

/// Global initialisers are a tiny expression ending in `end` (0x0b). We only
/// need to step over them to reach the next entry.
fn skip_const_expr(r: &mut Reader) -> R<()> {
    loop {
        match r.byte()? {
            0x0b => return Ok(()),
            0x41 | 0x42 => {
                r.i64()?;
            }
            0x43 => {
                r.take(4)?;
            }
            0x44 => {
                r.take(8)?;
            }
            0x23 | 0xd2 => {
                r.u32()?;
            }
            0xd0 => {
                r.byte()?;
            }
            0xfd => {
                // v128.const and friends — one LEB opcode then 16 bytes.
                let op = r.u32()?;
                if op == 12 {
                    r.take(16)?;
                }
            }
            _ => {}
        }
    }
}

// ── what the arena makes of a module ─────────────────────────────────────

/// The export sets that give a module a role in the arena. Anything that
/// matches none of them is still storable and still runnable — it is just a
/// `wasm` module rather than a `game` or a `player`.
pub const GAME_EXPORTS: [&str; 5] = ["game_init", "game_view", "game_step", "game_done", "game_result"];
pub const PLAYER_EXPORTS: [&str; 1] = ["play"];

pub fn role(m: &Module) -> &'static str {
    let has = |n: &str| m.exports.iter().any(|e| e.name == n);
    if GAME_EXPORTS.iter().all(|n| has(n)) {
        "game"
    } else if PLAYER_EXPORTS.iter().all(|n| has(n)) {
        "player"
    } else if has("_start") {
        "command"
    } else {
        "wasm"
    }
}

/// The host surface this module needs, grouped by import namespace. The
/// runner reads this to decide which shim to build — `wasi_snapshot_preview1`
/// gets the WASI shim, `arena` gets the arena hostcalls, anything else is
/// stubbed and logged.
pub fn host_needs(m: &Module) -> Vec<(String, usize)> {
    let mut groups: Vec<(String, usize)> = Vec::new();
    for i in &m.imports {
        match groups.iter_mut().find(|(n, _)| *n == i.module) {
            Some((_, c)) => *c += 1,
            None => groups.push((i.module.clone(), 1)),
        }
    }
    groups.sort_by(|a, b| a.0.cmp(&b.0));
    groups
}

/// Everything the registry knows about a module from the bytes alone.
pub fn describe(bytes: &[u8]) -> R<Value> {
    let m = parse(bytes)?;
    let (min, max) = m.memory_pages.unwrap_or((0, None));
    Ok(json!({
        "role": role(&m),
        // Said out loud, because every reader here answers this question and
        // a folder's config.json is checked against the answer.
        "lang": "wasm",
        "wasm_version": m.version,
        "size": m.size,
        "functions": m.func_count,
        "start": m.start,
        "memory": {
            "declared": m.memory_pages.is_some(),
            "imported": m.memory_imported,
            "min_pages": min,
            "max_pages": max,
            "min_bytes": min as u64 * 65536,
        },
        "imports": m.imports.iter().map(|i| json!({
            "module": i.module, "name": i.name, "kind": i.kind,
            "signature": i.sig.as_ref().map(|s| s.signature()),
        })).collect::<Vec<_>>(),
        "exports": m.exports.iter().map(|e| json!({
            "name": e.name, "kind": e.kind, "index": e.index,
            "signature": e.sig.as_ref().map(|s| s.signature()),
        })).collect::<Vec<_>>(),
        "host_needs": host_needs(&m).iter().map(|(n, c)| json!({ "namespace": n, "imports": c }))
            .collect::<Vec<_>>(),
        "custom_sections": m.custom_sections.iter().map(|(n, l)| json!({ "name": n, "bytes": l }))
            .collect::<Vec<_>>(),
    }))
}

#[cfg(test)]
mod tests {
    use super::*;

    /// (module (memory 1) (func (export "add") (param i32 i32) (result i32)
    ///   local.get 0 local.get 1 i32.add))
    fn add_module() -> Vec<u8> {
        let mut b = Vec::new();
        b.extend_from_slice(b"\0asm\x01\x00\x00\x00");
        // type: one (i32,i32)->i32
        b.extend_from_slice(&[0x01, 0x07, 0x01, 0x60, 0x02, 0x7f, 0x7f, 0x01, 0x7f]);
        // function: func 0 has type 0
        b.extend_from_slice(&[0x03, 0x02, 0x01, 0x00]);
        // memory: one, min 1
        b.extend_from_slice(&[0x05, 0x03, 0x01, 0x00, 0x01]);
        // export "add" func 0
        b.extend_from_slice(&[0x07, 0x07, 0x01, 0x03, b'a', b'd', b'd', 0x00, 0x00]);
        // code: local.get 0, local.get 1, i32.add, end
        b.extend_from_slice(&[0x0a, 0x09, 0x01, 0x07, 0x00, 0x20, 0x00, 0x20, 0x01, 0x6a, 0x0b]);
        b
    }

    #[test]
    fn reads_exports_with_their_signature() {
        let m = parse(&add_module()).expect("parses");
        assert_eq!(m.exports.len(), 1);
        assert_eq!(m.exports[0].name, "add");
        assert_eq!(
            m.exports[0].sig.as_ref().unwrap().signature(),
            "(i32, i32) -> i32"
        );
        assert_eq!(m.memory_pages, Some((1, None)));
        assert_eq!(role(&m), "wasm");
    }

    #[test]
    fn refuses_something_that_is_not_a_module() {
        assert!(parse(b"(module)").is_err());
        assert!(parse(b"").is_err());
    }

    #[test]
    fn a_truncated_module_is_an_error_not_a_panic() {
        let full = add_module();
        for cut in 1..full.len() {
            let _ = parse(&full[..cut]);
        }
    }

    #[test]
    fn names_the_host_surface_a_module_needs() {
        let mut b = Vec::new();
        b.extend_from_slice(b"\0asm\x01\x00\x00\x00");
        b.extend_from_slice(&[0x01, 0x04, 0x01, 0x60, 0x00, 0x00]);
        // import "wasi_snapshot_preview1" "proc_exit" (func 0)
        let mut imp: Vec<u8> = vec![0x01];
        let ns = b"wasi_snapshot_preview1";
        imp.push(ns.len() as u8);
        imp.extend_from_slice(ns);
        let nm = b"proc_exit";
        imp.push(nm.len() as u8);
        imp.extend_from_slice(nm);
        imp.extend_from_slice(&[0x00, 0x00]);
        b.push(0x02);
        b.push(imp.len() as u8);
        b.extend_from_slice(&imp);

        let m = parse(&b).expect("parses");
        assert_eq!(host_needs(&m), vec![("wasi_snapshot_preview1".to_string(), 1)]);
    }
}
