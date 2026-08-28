//! arena — a wasm storage and execution layer, and an arena built on it.
//!
//! The server stores modules and rates players. It does not run wasm: that
//! happens in the browser, or in the node runner, both of which use the same
//! execution layer under src/runtime/. Keeping execution out of the server is
//! what makes "store anything wasm" a safe promise to make.

// The MCP tool list is one big json! literal — a schema per tool blows the
// default macro recursion budget.
#![recursion_limit = "512"]

mod arena;
mod blobs;
mod http;
mod mcp;
mod players;
mod rating;
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
        .unwrap_or(50470);
    http::serve(port).await;
}
