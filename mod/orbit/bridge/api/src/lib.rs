// Library entry point — exposes the modules so integration tests can spin
// up the server in-process. The binary in main.rs uses these same modules.

pub mod config;
pub mod crypto;
pub mod ratelimit;
pub mod routes;
pub mod state;
pub mod store;
pub mod validate;
