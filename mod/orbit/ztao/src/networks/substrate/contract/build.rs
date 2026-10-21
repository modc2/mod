//! lsag.rs, fp.rs and poseidon.rs are the NEAR contract's files, verbatim.
//! They are std-agnostic but name Vec/String/format!/vec! from the std
//! prelude, which a no_std ink! crate does not have: copy them into OUT_DIR
//! with the alloc imports prepended (and their `//!` module docs dropped,
//! which include!() can not take). One source of truth, no fork.
use std::{env, fs, path::Path};

const SHARED: &str = "../../near/contract/src";
const PRELUDE: &str = "#[allow(unused_imports)]\nuse alloc::{format, vec, vec::Vec, string::String};\n";

fn main() {
    let out = env::var("OUT_DIR").unwrap();
    // relative to THIS file, not the cwd: cargo-contract runs the build
    // from a temp copy of the manifest (it passes build.rs absolute)
    let here = Path::new(file!()).parent().unwrap_or(Path::new(""));
    for name in ["fp.rs", "lsag.rs", "poseidon.rs"] {
        let src = here.join(SHARED).join(name);
        println!("cargo:rerun-if-changed={}", src.display());
        let body: String = fs::read_to_string(&src)
            .unwrap_or_else(|e| panic!("{}: {e}", src.display()))
            .lines()
            .filter(|l| !l.trim_start().starts_with("//!"))
            .map(|l| format!("{l}\n"))
            .collect();
        fs::write(Path::new(&out).join(name), format!("{PRELUDE}{body}")).unwrap();
    }
}
