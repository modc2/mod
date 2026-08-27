//! The server: an axum router over one engine, plus a clock.

use std::net::SocketAddr;

use prerank_api::{now, App};
use tracing_subscriber::EnvFilter;

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(
            EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "prerank_api=info,tower_http=warn".into()),
        )
        .init();

    let port: u16 = std::env::var("PORT")
        .ok()
        .and_then(|s| s.parse().ok())
        .unwrap_or(50630);

    let app = App::boot()?;
    {
        let engine = app.engine.lock();
        let v = engine.verify();
        tracing::info!(
            events = v.events,
            rounds = v.rounds,
            head = %engine.head(),
            open_mode = engine.open_mode,
            ok = v.ok,
            "prerank: log replayed",
        );
        for problem in &v.problems {
            tracing::error!(problem = %problem, "prerank: the log does not check out");
        }
    }

    // The round clock. Phases are computed from timestamps on every read, so
    // this task is not what makes them correct — it is what makes a round
    // seal and settle when nobody happens to be looking. The period follows
    // the round length, because a ten-second tick against a compressed round
    // would leave it visibly sealed but unsealed for a third of its life.
    let period = {
        let engine = app.engine.lock();
        std::time::Duration::from_secs((engine.schedule.day_secs / 20).clamp(1, 10) as u64)
    };
    let ticker = app.clone();
    tokio::spawn(async move {
        loop {
            tokio::time::sleep(period).await;
            let did = {
                let mut engine = ticker.engine.lock();
                engine.tick(now())
            };
            match did {
                Ok(events) => {
                    for e in events {
                        tracing::info!(event = %e, "round clock");
                    }
                }
                Err(e) => tracing::error!(error = %e, "round clock failed"),
            }
        }
    });

    let addr = SocketAddr::from(([0, 0, 0, 0], port));
    let listener = tokio::net::TcpListener::bind(addr).await?;
    tracing::info!("prerank api on http://{addr}");
    axum::serve(listener, prerank_api::routes::router(app)).await?;
    Ok(())
}
