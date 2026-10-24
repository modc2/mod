// The MCP tool list is one big json! literal — a schema per tool blows the
// default macro recursion budget.
#![recursion_limit = "512"]

mod arena;
mod bench;
mod http;
mod judge;
mod mcp;
mod players;
mod store;

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
        .unwrap_or(50400);
    http::serve(port).await;
}
