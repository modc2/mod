//! dev — a modular, OpenAI-API-standard multi-provider LLM gateway.
//!
//! Every provider (venice, openrouter, codex, claude, …) is an instance of a
//! single template: an OpenAI-compatible base URL exposing `/models` and
//! `/chat/completions`. New providers can be added at runtime by the owner with
//! no code change. Requests resolve a key in this order:
//!
//!   1. the caller's own BYOK key for that provider (encrypted at rest), else
//!   2. the provider's backend key from the environment (server-funded), else
//!   3. a 402-style "no key" error.
//!
//! Chat completions are streamed straight through (SSE `stream: true` works end
//! to end). Identity comes from a mod protocol-auth bearer token (wallet-signed).

pub mod auth;
pub mod keystore;
pub mod providers;
pub mod routes;
pub mod upstream;

pub use routes::{router, AppState};
