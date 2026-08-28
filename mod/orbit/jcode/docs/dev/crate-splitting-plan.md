# Compile-time crate splitting plan

## Goal

Minimize the amount of code that must be rechecked or rebuilt when iterating on
Jcode. The root `jcode` crate is still the integration shell, but stable leaf
code should live in small crates with one-way dependencies.

## Principles

1. Extract stable leaves first: filesystem/storage, protocol/types, parsers,
   provider request/stream codecs, and TUI render primitives.
2. Avoid cyclic domain crates. Root `jcode` may depend on leaf crates, but leaf
   crates must not call back into root logging/config/runtime directly. Use data
   types, callbacks, or explicit events at boundaries.
3. Split by recompilation volatility, not by directory names. Code edited often
   should not force heavy provider/TUI/server modules to rebuild unless needed.
4. Keep heavy optional dependencies behind crates/features. Embeddings, PDF,
   desktop/mobile, browser, and image/render pipelines should remain isolated.
5. Preserve compatibility facades during migration. `crate::storage::*` can
   re-export `jcode-storage::*` while callers move gradually.

## Current first step

`jcode-storage` is now a leaf crate for app paths, permission hardening, atomic
JSON writes, and append-only JSONL helpers. The root `src/storage.rs` module is a
thin compatibility facade that preserves existing logging behavior for backup
recovery.

Measured after extraction on this machine:

- `cargo check -p jcode-storage`: ~0.9s after initial dependencies were built.
- `cargo check -p jcode --lib`: ~14s in the current warm-cache state.

## Recommended next extractions

1. `jcode-provider-anthropic`: move Anthropic request/stream translation out of
   root `src/provider/anthropic.rs` and depend only on `jcode-provider-core`,
   `jcode-message-types`, and serde/reqwest primitives.
2. `jcode-provider-openai`: same for OpenAI request/stream handling. This
   reduces rebuilds when editing server/TUI code and makes provider tests cheap.
3. `jcode-session-core`: move session storage paths, journal metadata, and
   memory-profile pure transforms once dependencies on root prompt/logging are
   cut behind callbacks.
4. `jcode-tui-app-state`: split key/input/navigation state transitions from
   rendering. Keep ratatui rendering in `jcode-tui-render`/root while state tests
   compile without the whole root crate.
5. `jcode-server-protocol-runtime`: split websocket/client event fanout glue from
   agent execution so server tests do not rebuild TUI/provider internals.

## Anti-patterns to avoid

- Extracting crates that depend on root `jcode`. That preserves the compile-time
  bottleneck and creates dependency cycles.
- Tiny crates for every file. Too many crates increase metadata overhead and make
  refactors painful.
- Moving only type aliases while leaving implementations in root. The expensive
  compile units remain expensive.
