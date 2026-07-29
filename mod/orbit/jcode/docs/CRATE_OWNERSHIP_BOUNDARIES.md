# Crate Ownership and Modularization Boundaries

This document defines the target structure for keeping `jcode` modular without turning shared crates into a dumping ground. It is intentionally practical: use it when deciding whether to move a type, helper, or behavior out of the root crate.

## Goals

Primary goal: make normal development and selfdev builds faster by shrinking the root crate's recompilation surface. Structural cleanliness is valuable because it supports that compile-time goal.

- Move stable DTOs and protocol-safe state into small crates so changes in root behavior do not recompile those contracts, and changes in contracts recompile only focused dependents.
- Keep dependency-light crates dependency-light so they compile quickly and do not pull large runtime/TUI/provider graphs into unrelated builds.
- Keep root-only behavior, storage, process, TUI, server, and provider runtime logic in the root crate until a full dependency boundary can move without increasing dependency fan-out.
- Avoid cyclic dependencies and hidden coupling through broad `jcode-core` re-exports.
- Preserve serde compatibility and root re-exports during migrations unless all call sites are intentionally updated.
- Measure success by compile impact: fewer root edits, fewer root-owned DTOs, smaller dependency fan-out, and faster `cargo check --profile selfdev` / `selfdev build` after common changes.

## Ownership rules

### Type crates own stable data contracts

A `*-types` crate should contain:

- Plain data structures used by multiple crates or protocol layers.
- Serialization shape and small pure helper methods tied to the data contract.
- No filesystem, network, process, TUI, provider client, global state, or storage access.
- Dependencies limited to serde, chrono, and other type crates where necessary.

Examples: `jcode-session-types`, `jcode-side-panel-types`, `jcode-selfdev-types`, `jcode-background-types`.

### Domain behavior modules own root runtime behavior

Root modules should keep behavior when it needs:

- `crate::storage`, `crate::config`, `crate::logging`, `crate::server`, or process spawning.
- Provider HTTP clients and auth managers.
- Tokio runtime, background tasks, channels, global caches, file locks, or PID registries.
- TUI rendering and crossterm/ratatui state.

If a type has inherent methods that need these APIs, either leave the type in root or move behavior and dependencies together into a domain crate. Do not move only the struct if that forces illegal inherent impls in root.

### `jcode-core` is for genuinely shared primitives

`jcode-core` should contain:

- Cross-domain primitives that do not have an obvious domain crate yet.
- Very small, dependency-light helpers used by many crates.
- Temporary DTO staging only when creating a new domain type crate would be premature.

`jcode-core` should not accumulate every extracted DTO indefinitely. Once a cluster grows, split it into a focused domain crate.

### Compile-speed decision rule

Prefer a split when it reduces root crate churn or dependency fan-out. Do not split just to make files look tidier if the new crate adds dependencies, increases rebuild fan-out, or forces frequent cross-crate edits. A good split has at least one of these compile-time benefits:

- Common root behavior edits no longer touch stable type definitions.
- A type-only change can be checked by compiling a small type crate plus focused dependents.
- Heavy dependencies stay out of DTO crates.
- Multiple downstream crates can use a small contract without depending on the root crate.

### Re-export policy

During migrations:

1. Move the type to the target crate.
2. Keep the old root path as `pub use ...` to preserve call sites.
3. Validate focused tests and selfdev build/reload.
4. Later, remove obsolete root re-exports only after downstream crates can depend directly on the domain crate.

## Move checklist

Use this checklist for every type or pure-helper migration. Copy it into the PR/commit notes when a move is non-trivial.

1. Classify the candidate.
   - [ ] Is it a stable data contract or pure helper rather than root runtime behavior?
   - [ ] Does it have inherent methods?
   - [ ] Do those methods require root-only APIs such as storage, network clients, TUI state, process management, or globals?
   - [ ] If behavior must move too, can the full dependency boundary move without increasing fan-out?
2. Check compatibility.
   - [ ] Does its serde representation stay identical?
   - [ ] Are defaults, skips, renames, and enum discriminants preserved?
   - [ ] Are all field visibilities still appropriate?
   - [ ] Can root keep a compatibility re-export?
3. Check crate health.
   - [ ] Does the target crate already have the needed dependency policy?
   - [ ] Are new dependencies limited to type-crate-appropriate libraries, usually `serde`, `serde_json`, `chrono`, or sibling type crates?
   - [ ] Is the target crate still acyclic?
   - [ ] Did `cargo metadata`/`cargo check` avoid pulling root, TUI, provider, storage, server, or process dependencies into the type crate?
4. Validate.
   - [ ] Is there a focused test filter that covers the moved type?
   - [ ] Did `cargo check --profile selfdev -p <type-crate> -p jcode --bin jcode` pass?
   - [ ] Did relevant focused root tests pass?
   - [ ] Did `cargo fmt` pass?
   - [ ] Did selfdev build and reload pass from a clean committed HEAD?

## Dependency boundary guard

Run this guard after adding or changing any type crate dependency:

```sh
python3 scripts/check_dependency_boundaries.py
```

The guard blocks direct dependencies from `jcode-*-types` crates to root/runtime-heavy internal crates such as `jcode`, `jcode-core`, provider crates, TUI crates, protocol/runtime crates, and desktop/mobile crates. Type crates may depend on external lightweight libraries and other type crates. If a new internal dependency is needed, first decide whether it should itself be a type crate.

## Test policy

Prefer focused filters for validation. Broad filters often select unrelated stateful, timing-sensitive, or benchmark tests.

Known broad-filter hazards observed during modularization:

- `side_panel` selects unrelated pinned UI/layout and latency benchmark tests.
- `usage` selects app-display tests in addition to pure usage tests.
- `session::` selects live-attach server tests and picker behavior beyond session persistence.
- `ambient` selects TUI/helper integration tests with config and schedule state beyond ambient module persistence/runtime tests.

Document precise filters next to each domain crate/module. Broad filters are still useful for periodic sweeps, but they should not block a DTO-only extraction when precise tests and compile checks pass.

Focused validation matrix after the current DTO splits:

| Area | Fast compile check | Focused root tests used during split | Notes |
| --- | --- | --- | --- |
| Usage DTOs | `cargo check --profile selfdev -p jcode-usage-types -p jcode --bin jcode` | Prefer exact tests under usage/copilot usage modules. Avoid bare `usage` as a required gate because it selects display/UI tests too. | DTO crate owns report and local counter contracts. Runtime fetch/cache/display stay root. |
| Gateway DTOs | `cargo check --profile selfdev -p jcode-gateway-types -p jcode --bin jcode` | Focus gateway persistence/auth tests by exact test names when available. | Pairing/token HTTP/WebSocket behavior stays root. |
| Ambient DTOs | `cargo check --profile selfdev -p jcode-ambient-types -p jcode --bin jcode` | Scheduler/type consumers only. | Ambient DTO crate owns usage records only. Queue/runtime/prompt behavior stays root. |
| Ambient behavior modules | `cargo check --profile selfdev -p jcode --bin jcode` | `cargo test --profile selfdev -p jcode ambient::ambient_tests --lib`; `cargo test --profile selfdev -p jcode ambient::scheduler::tests --lib`; `cargo test --profile selfdev -p jcode ambient::runner::runner_tests --lib` | Avoid bare `ambient` as a required gate for module-only refactors because it selects cross-module TUI/config state tests. |
| Memory activity DTOs | `cargo check --profile selfdev -p jcode-memory-types -p jcode-core -p jcode --bin jcode` | `cargo test --profile selfdev -p jcode runtime_memory_log --lib`; `cargo test --profile selfdev -p jcode tui::info_widget::tests --lib` | `memory::activity` currently matches no tests, so use consumer tests. |
| Goal/todo/catchup core DTOs | `cargo check --profile selfdev -p jcode-core -p jcode --bin jcode` | Exact goal/todo/catchup filters if behavior changes. | Currently small/stable enough to leave in `jcode-core`; revisit if churn grows. |


## Compile baseline observations

Measured on 2026-04-30 with `scripts/dev_cargo.sh check --profile selfdev -p jcode --bin jcode` after the compile-speed boundary doc commit. This is a coarse mtime-touch benchmark, not a full statistical study, but it is enough to guide priorities.

| Scenario | Observed time | Interpretation |
| --- | ---: | --- |
| No-op check after recent doc-only commit | ~65.8s | Environment/cache state can dominate a first check. Treat as warmup/noise baseline, not pure no-op steady state. |
| Touch root behavior module `src/usage.rs` | ~6.25s | A root-only behavior edit can be relatively cheap when dependencies are already built. |
| Touch `crates/jcode-core/src/usage_types.rs` | ~65.35s | Editing `jcode-core` invalidates broad downstream dependents. Avoid adding high-churn domain DTOs to `jcode-core`. |

Implication: the compile-speed target is not simply "move things out of root". Moving stable, low-churn contracts out of root is good, but putting many high-churn domain DTOs into `jcode-core` can be counterproductive because `jcode-core` has high fan-out. Prefer focused leaf crates such as `jcode-usage-types`, `jcode-gateway-types`, and `jcode-ambient-types` for domain DTOs that are likely to change.

## `jcode-core` fan-out audit

At this checkpoint, the root crate is the only direct Cargo dependency on `jcode-core`, but root re-exports many `jcode-core` modules and root is the high-cost recompilation target. A touch to `jcode-core` invalidated broad downstream checks in the baseline above. Therefore `jcode-core` should be treated as a high-fan-out crate even if Cargo.toml direct dependents are currently few.

Observed root re-export/use paths:

- `src/catchup.rs` -> `catchup_types`
- `src/goal.rs` -> `goal_types`
- `src/todo.rs` -> `todo_types`
- `src/env.rs`, `src/id.rs`, `src/stdin_detect.rs`, `src/util.rs`, and panic UI helpers -> general utilities

Compile-speed priority from this audit:

1. Move clustered, likely-changing domain DTOs from `jcode-core` to focused leaf crates.
2. Keep stable general utilities in `jcode-core`.
3. Avoid adding new domain DTOs to `jcode-core` unless they are very stable or temporary staging.

| Module | Current contents | Preferred long-term home | Notes |
| --- | --- | --- | --- |
| `ambient_usage_types` | Ambient scheduler usage records/rate limit DTOs | moved to `jcode-ambient-types` | Compatibility re-export remains in root module. |
| `catchup_types` | Catch-up persisted state and rendered brief DTOs | `jcode-catchup-types` or stay in core | Small and low churn. Split only if catch-up grows. |
| `copilot_usage_types` | Local Copilot usage counters | moved to `jcode-usage-types` | Compatibility re-export remains in root module. |
| `gateway_types` | Paired device and pairing code persisted records | moved to `jcode-gateway-types` | Pairing/token behavior remains root. |
| `goal_types` | Goal state, milestones, status, updates | `jcode-goal-types` or `jcode-task-types` | Larger domain. Worth splitting if goal/tool work grows. |
| `memory_types` | Memory activity DTOs | moved to `jcode-memory-types` | Memory has enough domain weight for its own type crate. |
| `todo_types` | Todo item DTO | `jcode-task-types`, `jcode-todo-types`, or core | Tiny. Could join goal/catchup task-state crate. |
| `usage_types` | Provider usage report DTOs | moved to `jcode-usage-types` | Runtime fetch/cache/display remain root. |
| `env` | Environment variable helpers | stay in core | General utility, no domain crate needed. |
| `id` | ID helpers | stay in core | General utility. |
| `panic_util` | Panic formatting helpers | stay in core | General runtime utility. |
| `stdin_detect` | stdin detection helpers | stay in core | General platform/runtime utility. |
| `util` | Misc utilities | audit later | Should not become a catch-all. |

## Target domain type crates

Completed/high-value domain type splits:

1. `jcode-usage-types`
   - `usage_types`
   - `copilot_usage_types`
   - pure account usage DTOs if/when separated from root formatting/runtime helpers

2. `jcode-gateway-types`
   - `gateway_types`
   - possibly `GatewayConfig` after deciding whether config owns it
   - mobile gateway protocol-safe DTOs if needed by mobile crates

3. `jcode-ambient-types`
   - `ambient_usage_types`
   - ambient state/request/result DTOs, but only after root-only `AmbientState::load/save/record_cycle` methods are separated into root free functions or a persistence layer

4. `jcode-memory-types`
   - `memory_types`
   - any memory protocol/activity DTOs used across server/TUI/tools

5. Optional task-state crate
   - `goal_types`
   - `todo_types`
   - `catchup_types` if the product model wants these grouped

## Big module refactor targets

These are not simple DTO moves. Refactor behavior boundaries first.

### `src/session.rs`

Target split:

- metadata/session model
- persistence and journal replay
- startup stubs and remote startup snapshots
- memory profiling/cache attribution
- rendering lives in existing `session/render.rs`
- crash recovery lives in existing `session/crash.rs`

### `src/ambient.rs`

Target split:

- visible cycle context I/O
- state persistence
- directive persistence
- schedule queue and locking
- prompt building
- manager/runtime orchestration

Do not move `AmbientState` as a DTO until load/save/record behavior is separated from the struct.

### `src/usage.rs`

Target split:

- API fetch providers
- provider response parsing
- local caches/sync
- display formatting
- account selection/guidance
- public report DTOs in `jcode-usage-types`

### `src/gateway.rs`

Target split:

- registry persistence
- pairing/token auth
- HTTP route handling
- WebSocket auth/extraction
- WebSocket relay
- public gateway DTOs in `jcode-gateway-types`

## Definition of “optimal enough”

The structure is good enough when:

- Each type crate has a clear domain and minimal dependency set.
- `jcode-core` contains only true primitives or documented temporary staging modules.
- Root modules no longer mix large DTO blocks, persistence, runtime orchestration, and rendering in one file.
- Every domain has focused validation commands.
- Selfdev build/reload works cleanly after every structural change.
