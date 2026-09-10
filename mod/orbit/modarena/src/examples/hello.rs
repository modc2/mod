// A WASI command — the proof that "anything wasm" includes things that were
// never written for this arena.
//
// Nothing in here knows about the arena ABI. It is an ordinary program: it
// reads argv, reads stdin, prints to stdout and exits. The console runs it
// through the WASI shim in host.mjs, in a worker, with no filesystem and no
// network — which is the whole point of testing the shim with something that
// only speaks preview1.
//
//     rustc --target wasm32-wasip1 -O hello.rs -o wasm/hello.wasm

use std::io::Read;

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let who = if args.is_empty() { "world".to_string() } else { args.join(" ") };
    println!("hello, {who}");

    let mut input = String::new();
    if std::io::stdin().read_to_string(&mut input).is_ok() && !input.trim().is_empty() {
        println!("stdin: {} byte(s), {} line(s)", input.len(), input.lines().count());
    }

    // A filesystem the shim deliberately does not provide — reported rather
    // than hidden, so the sandbox is visible from inside it.
    match std::fs::read_to_string("/etc/passwd") {
        Ok(_) => println!("filesystem: readable (this arena would call that a bug)"),
        Err(e) => println!("filesystem: {} — sandboxed, as intended", e.kind()),
    }

    eprintln!("done");
}
