//! wasmlower IN OUT — make a modern-rustc wasm loadable by nearcore.
//!
//! rustc >= 1.82 ships a std built with bulk-memory and reference-types, so
//! even `-C target-cpu=mvp` builds carry `memory.copy`/`memory.fill` and
//! overlong `call_indirect` encodings. nearcore's preparer rejects both with
//! `PrepareError: Deserialization`. This is the subset of `wasm-opt
//! --llvm-memory-copy-fill-lowering` that zuma needs, with no binaryen:
//!   - memory.copy d s n  ->  call $copy (word loop, memmove-safe)
//!   - memory.fill d v n  ->  call $fill (word loop)
//!   - every instruction re-encoded (call_indirect table index = one 0x00)
//!   - DataCount + custom sections dropped
//! The result is validated against MVP + sign-ext + mutable-global before
//! it is written, so a feature we do not lower fails here, not on chain.
use std::convert::Infallible;
use wasm_encoder::reencode::{utils, Error, Reencode};
use wasm_encoder::{BlockType, CodeSection, Function, FunctionSection, Instruction as I,
                   MemArg, Module, TypeSection, ValType};
use wasmparser::{Operator, Parser, Payload, Validator, WasmFeatures};

struct Lower {
    types: u32,   // existing type count -> our (i32,i32,i32)->() index
    funcs: u32,   // imported + defined -> $copy, $copy+1 = $fill
    lowered: u32,
}

impl Reencode for Lower {
    type Error = Infallible;

    fn instruction<'a>(&mut self, op: Operator<'a>) -> Result<I<'a>, Error<Infallible>> {
        match op {
            Operator::MemoryCopy { dst_mem: 0, src_mem: 0 } => {
                self.lowered += 1;
                Ok(I::Call(self.funcs))
            }
            Operator::MemoryFill { mem: 0 } => {
                self.lowered += 1;
                Ok(I::Call(self.funcs + 1))
            }
            op => utils::instruction(self, op),
        }
    }

    fn parse_type_section(&mut self, types: &mut TypeSection,
                          s: wasmparser::TypeSectionReader<'_>) -> Result<(), Error<Infallible>> {
        utils::parse_type_section(self, types, s)?;
        types.ty().function([ValType::I32; 3], []);
        Ok(())
    }

    fn parse_function_section(&mut self, f: &mut FunctionSection,
                              s: wasmparser::FunctionSectionReader<'_>) -> Result<(), Error<Infallible>> {
        utils::parse_function_section(self, f, s)?;
        f.function(self.types);
        f.function(self.types);
        Ok(())
    }

    fn parse_code_section(&mut self, code: &mut CodeSection,
                          s: wasmparser::CodeSectionReader<'_>) -> Result<(), Error<Infallible>> {
        utils::parse_code_section(self, code, s)?;
        code.function(&copy_fn());
        code.function(&fill_fn());
        Ok(())
    }
}

fn m(align: u32) -> MemArg {
    MemArg { offset: 0, align, memory_index: 0 }
}

/// $copy(d, s, n): forward when d <= s, else backward; 8 bytes at a time,
/// then bytes. Both directions read a word before writing it, so overlap is
/// safe (memmove semantics, as memory.copy requires).
fn copy_fn() -> Function {
    let (d, s, n) = (0, 1, 2);
    let mut f = Function::new([]);
    for i in [
        I::LocalGet(d), I::LocalGet(s), I::I32LeU,
        I::If(BlockType::Empty),
        // forward words
        I::Block(BlockType::Empty), I::Loop(BlockType::Empty),
        I::LocalGet(n), I::I32Const(8), I::I32LtU, I::BrIf(1),
        I::LocalGet(d), I::LocalGet(s), I::I64Load(m(0)), I::I64Store(m(0)),
        I::LocalGet(d), I::I32Const(8), I::I32Add, I::LocalSet(d),
        I::LocalGet(s), I::I32Const(8), I::I32Add, I::LocalSet(s),
        I::LocalGet(n), I::I32Const(8), I::I32Sub, I::LocalSet(n),
        I::Br(0), I::End, I::End,
        // forward bytes
        I::Block(BlockType::Empty), I::Loop(BlockType::Empty),
        I::LocalGet(n), I::I32Eqz, I::BrIf(1),
        I::LocalGet(d), I::LocalGet(s), I::I32Load8U(m(0)), I::I32Store8(m(0)),
        I::LocalGet(d), I::I32Const(1), I::I32Add, I::LocalSet(d),
        I::LocalGet(s), I::I32Const(1), I::I32Add, I::LocalSet(s),
        I::LocalGet(n), I::I32Const(1), I::I32Sub, I::LocalSet(n),
        I::Br(0), I::End, I::End,
        I::Else,
        // backward words: n -= 8; d[n..n+8] = s[n..n+8]
        I::Block(BlockType::Empty), I::Loop(BlockType::Empty),
        I::LocalGet(n), I::I32Const(8), I::I32LtU, I::BrIf(1),
        I::LocalGet(n), I::I32Const(8), I::I32Sub, I::LocalSet(n),
        I::LocalGet(d), I::LocalGet(n), I::I32Add,
        I::LocalGet(s), I::LocalGet(n), I::I32Add, I::I64Load(m(0)),
        I::I64Store(m(0)),
        I::Br(0), I::End, I::End,
        // backward bytes
        I::Block(BlockType::Empty), I::Loop(BlockType::Empty),
        I::LocalGet(n), I::I32Eqz, I::BrIf(1),
        I::LocalGet(n), I::I32Const(1), I::I32Sub, I::LocalSet(n),
        I::LocalGet(d), I::LocalGet(n), I::I32Add,
        I::LocalGet(s), I::LocalGet(n), I::I32Add, I::I32Load8U(m(0)),
        I::I32Store8(m(0)),
        I::Br(0), I::End, I::End,
        I::End,
        I::End,
    ] {
        f.instruction(&i);
    }
    f
}

/// $fill(d, v, n): v's low byte splatted to a word, 8 bytes at a time.
fn fill_fn() -> Function {
    let (d, v, n, w) = (0, 1, 2, 3);
    let mut f = Function::new([(1, ValType::I64)]);
    for i in [
        I::LocalGet(v), I::I64ExtendI32U, I::I64Const(0xff), I::I64And,
        I::I64Const(0x0101_0101_0101_0101), I::I64Mul, I::LocalSet(w),
        I::Block(BlockType::Empty), I::Loop(BlockType::Empty),
        I::LocalGet(n), I::I32Const(8), I::I32LtU, I::BrIf(1),
        I::LocalGet(d), I::LocalGet(w), I::I64Store(m(0)),
        I::LocalGet(d), I::I32Const(8), I::I32Add, I::LocalSet(d),
        I::LocalGet(n), I::I32Const(8), I::I32Sub, I::LocalSet(n),
        I::Br(0), I::End, I::End,
        I::Block(BlockType::Empty), I::Loop(BlockType::Empty),
        I::LocalGet(n), I::I32Eqz, I::BrIf(1),
        I::LocalGet(d), I::LocalGet(v), I::I32Store8(m(0)),
        I::LocalGet(d), I::I32Const(1), I::I32Add, I::LocalSet(d),
        I::LocalGet(n), I::I32Const(1), I::I32Sub, I::LocalSet(n),
        I::Br(0), I::End, I::End,
        I::End,
    ] {
        f.instruction(&i);
    }
    f
}

fn leb(b: &[u8], mut i: usize) -> (u32, usize) {
    let (mut v, mut sh) = (0u32, 0);
    loop {
        let x = b[i];
        i += 1;
        v |= ((x & 0x7f) as u32) << sh;
        if x & 0x80 == 0 {
            return (v, i);
        }
        sh += 7;
    }
}

/// Drop DataCount (12) and custom (0) sections from an encoded module.
fn strip(b: &[u8]) -> Vec<u8> {
    let mut out = b[..8].to_vec();
    let mut i = 8;
    while i < b.len() {
        let id = b[i];
        let (len, body) = leb(b, i + 1);
        let end = body + len as usize;
        if id != 0 && id != 12 {
            out.extend_from_slice(&b[i..end]);
        }
        i = end;
    }
    out
}

fn main() {
    let a: Vec<String> = std::env::args().collect();
    if a.len() != 3 {
        eprintln!("usage: wasmlower IN.wasm OUT.wasm");
        std::process::exit(2);
    }
    let wasm = std::fs::read(&a[1]).expect("read input");
    let (mut types, mut imported, mut defined) = (0u32, 0u32, 0u32);
    for p in Parser::new(0).parse_all(&wasm) {
        match p.expect("parse") {
            Payload::TypeSection(s) => types = s.count(),
            Payload::ImportSection(s) => {
                for imp in s.into_imports() {
                    if matches!(imp.expect("import").ty, wasmparser::TypeRef::Func(_)) {
                        imported += 1;
                    }
                }
            }
            Payload::FunctionSection(s) => defined = s.count(),
            _ => {}
        }
    }
    let mut lw = Lower { types, funcs: imported + defined, lowered: 0 };
    let mut module = Module::new();
    lw.parse_core_module(&mut module, Parser::new(0), &wasm).expect("reencode");
    let out = strip(&module.finish());
    let mvp = WasmFeatures::MUTABLE_GLOBAL | WasmFeatures::SIGN_EXTENSION | WasmFeatures::FLOATS;
    if let Err(e) = Validator::new_with_features(mvp).validate_all(&out) {
        eprintln!("still not MVP after lowering: {e}");
        std::process::exit(1);
    }
    std::fs::write(&a[2], &out).expect("write output");
    println!("{{\"in\":{},\"out\":{},\"lowered\":{}}}", wasm.len(), out.len(), lw.lowered);
}
