//! arena — a storage and execution layer for uploaded code, and an arena on it.
//!
//! Three containers, one registry: a wasm binary, a Python class, or a Rust
//! class. `wasm.rs` reads the exports of the first, `klass.rs` reads the
//! `def`s of the second, `rsklass.rs` reads the `fn`s in the third's impl
//! block — and in every case the role, game or player or neither, comes out
//! of the bytes rather than out of what the uploader claimed.
//!
//! The server stores modules and rates players. It does not execute any of
//! them: wasm runs in the browser or the node runner, a Python class runs in a
//! sandboxed python subprocess the runner starts, and a Rust class is compiled
//! to wasm here (`rustc.rs`) and then runs wherever wasm does. All of it goes
//! through the same execution layer under src/runtime/, which is what makes
//! "store anything" a safe promise to make.
//!
//! Two more things every stored module is: a mod of its own, minted under
//! `orbit/arena/mods/` (`games.py`), and an MCP server of its own, served at
//! `/m/<name>/mcp` (`modmcp.rs`). A game you can open and play a turn at a
//! time; an agent you can ask for a move. And the traffic runs both ways —
//! `mcpout.rs` is the door a class calls *out* through.

// The MCP tool list is one big json! literal — a schema per tool blows the
// default macro recursion budget.
#![recursion_limit = "512"]

mod arena;
mod blobs;
mod folder;
mod generate;
mod http;
mod klass;
mod liquidai;
mod mcp;
mod mcpout;
mod modmcp;
mod players;
mod rating;
mod rsklass;
mod rustc;
mod store;
mod wasm;

#[tokio::main]
async fn main() {
    let args: Vec<String> = std::env::args().collect();
    if args.iter().any(|a| a == "--stdio") {
        mcp::run_stdio().await;
        return;
    }
    let port: u16 = std::env::var("PORT")
        .ok()
        .and_then(|p| p.parse().ok())
        .unwrap_or(50800);
    http::serve(port).await;
}
