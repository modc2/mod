mod chutes;
mod http;
mod mcp;
mod upstream;

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
        .unwrap_or(50300);
    http::serve(port).await;
}
