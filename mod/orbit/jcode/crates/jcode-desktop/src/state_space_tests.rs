//! Headless state-space walker for the desktop UI.
//!
//! Explores the reachable UI state graph by replaying every interesting
//! `KeyInput` against cloned app states (BFS with snapshot-based dedup) and
//! checks oracles on every transition:
//!
//! - no panic in `handle_key`
//! - draft cursor stays in bounds and on a char boundary
//! - snapshot serializes and round-trips through `restore_snapshot`
//! - single-session vertex build does not panic and produces finite vertices
//! - workspace focus always points at an existing surface
//! - overlays are escape-recoverable (soft finding, reported not fatal)
//! - per-transition latency budget (soft finding, reported not fatal)
//!
//! Run with:
//!   cargo test -p jcode-desktop state_space -- --nocapture

use super::desktop_app_driver::{DesktopAppDriver, DesktopSurfaceSnapshot};
use super::desktop_gallery;
use super::single_session::SingleSessionApp;
use super::single_session_render::build_single_session_vertices;
use super::workspace::{self, KeyInput, PanelSizePreset, Workspace};
use super::{
    DesktopApp, Vertex, WorkspaceVertexBuildParams, build_vertices_into, workspace_render_layout,
    workspace_status_bar_target_color,
};
use std::collections::{HashMap, HashSet, VecDeque};
use std::panic::{AssertUnwindSafe, catch_unwind};
use std::time::{Duration, Instant};
use winit::dpi::PhysicalSize;

const WALK_SIZE: PhysicalSize<u32> = PhysicalSize::new(1280, 800);
/// Additional sizes exercised by the render oracle to catch layout
/// panics/overflows that only reproduce at small or narrow windows.
const RENDER_ORACLE_SIZES: &[PhysicalSize<u32>] = &[
    WALK_SIZE,
    PhysicalSize::new(1000, 720),
    PhysicalSize::new(640, 400),
    PhysicalSize::new(320, 240),
];
const MAX_DEPTH: usize = 3;
const MAX_UNIQUE_STATES_PER_SEED: usize = 600;
const SEED_TIME_BUDGET: Duration = Duration::from_secs(20);

fn env_usize(name: &str, default: usize) -> usize {
    std::env::var(name)
        .ok()
        .and_then(|value| value.parse().ok())
        .unwrap_or(default)
}

struct WalkBudget {
    max_depth: usize,
    max_unique_states: usize,
    seed_time_budget: Duration,
}

fn walk_budget() -> WalkBudget {
    WalkBudget {
        max_depth: env_usize("JCODE_WALK_DEPTH", MAX_DEPTH),
        max_unique_states: env_usize("JCODE_WALK_STATES", MAX_UNIQUE_STATES_PER_SEED),
        seed_time_budget: Duration::from_secs(env_usize(
            "JCODE_WALK_SECONDS",
            SEED_TIME_BUDGET.as_secs() as usize,
        ) as u64),
    }
}
const ESCAPE_RECOVERY_PRESSES: usize = 8;
const SLOW_TRANSITION_BUDGET: Duration = Duration::from_millis(25);
const MIN_EXPECTED_VERTICES: usize = 6;

fn clone_app(app: &DesktopApp) -> DesktopApp {
    match app {
        DesktopApp::SingleSession(single) => DesktopApp::SingleSession(single.clone()),
        DesktopApp::Workspace(workspace) => DesktopApp::Workspace(workspace.clone()),
    }
}

/// Representative input alphabet. One value per equivalence class of inputs.
fn input_alphabet() -> Vec<KeyInput> {
    vec![
        KeyInput::Escape,
        KeyInput::Enter,
        KeyInput::Backspace,
        KeyInput::DeletePreviousWord,
        KeyInput::DeleteNextWord,
        KeyInput::DeleteNextChar,
        KeyInput::MoveCursorWordLeft,
        KeyInput::MoveCursorWordRight,
        KeyInput::MoveCursorLeft,
        KeyInput::MoveCursorRight,
        KeyInput::MoveToLineStart,
        KeyInput::MoveToLineEnd,
        KeyInput::DeleteToLineStart,
        KeyInput::DeleteToLineEnd,
        KeyInput::CutInputLine,
        KeyInput::UndoInput,
        KeyInput::Autocomplete,
        KeyInput::CancelGeneration,
        KeyInput::ScrollBodyLines(-3),
        KeyInput::ScrollBodyLines(3),
        KeyInput::ScrollBodyPages(-1),
        KeyInput::ScrollBodyPages(1),
        KeyInput::ScrollBodyToTop,
        KeyInput::ScrollBodyToBottom,
        KeyInput::JumpPrompt(-1),
        KeyInput::JumpPrompt(1),
        KeyInput::CopyLatestResponse,
        KeyInput::CopyLatestCodeBlock,
        KeyInput::CopyTranscript,
        KeyInput::OpenModelPicker,
        KeyInput::OpenSessionSwitcher,
        KeyInput::ModelPickerMove(-1),
        KeyInput::ModelPickerMove(1),
        KeyInput::CycleModel(1),
        KeyInput::CycleReasoningEffort(1),
        KeyInput::ClearAttachedImages,
        KeyInput::QueueDraft,
        KeyInput::RetrieveQueuedDraft,
        KeyInput::SubmitDraft,
        KeyInput::HotkeyHelp,
        KeyInput::ToggleInputMode,
        KeyInput::ToggleSessionInfo,
        KeyInput::RefreshSessions,
        KeyInput::AdjustTextScale(-1),
        KeyInput::AdjustTextScale(1),
        KeyInput::ResetTextScale,
        KeyInput::SetPanelSize(PanelSizePreset::Half),
        KeyInput::Character("a".to_string()),
        KeyInput::Character("j".to_string()),
        KeyInput::Character("k".to_string()),
        KeyInput::Character("h".to_string()),
        KeyInput::Character("l".to_string()),
        KeyInput::Character("g".to_string()),
        KeyInput::Character("/".to_string()),
        KeyInput::Character(" ".to_string()),
        KeyInput::Character("é".to_string()),
        KeyInput::Other,
    ]
}

fn gallery_seed_states() -> Vec<(String, DesktopApp)> {
    [
        "empty",
        "markdown",
        "tool-running",
        "tool-success",
        "tool-failed",
        "tool-stack",
        "stdin-request",
        "streaming",
        "error",
        "hotkey-help",
        "model-picker",
        "session-info",
        "session-switcher",
        "slash-suggestions",
        "long-transcript",
    ]
    .iter()
    .map(|state| {
        (
            format!("gallery-{state}"),
            desktop_gallery::temporary_app(state),
        )
    })
    .collect()
}

fn workspace_seed_states() -> Vec<(String, DesktopApp)> {
    let cards = (1..=3)
        .map(|index| workspace::SessionCard {
            session_id: format!("walker-session-{index}"),
            title: format!("Walker session {index}"),
            subtitle: "active".to_string(),
            detail: format!("{index} messages"),
            preview_lines: vec![format!("preview line {index}")],
            detail_lines: vec![format!("detail line {index}")],
            transcript_messages: Vec::new(),
        })
        .collect::<Vec<_>>();
    vec![
        (
            "workspace-3-sessions".to_string(),
            DesktopApp::Workspace(Workspace::from_session_cards(cards)),
        ),
        (
            "workspace-empty".to_string(),
            DesktopApp::Workspace(Workspace::from_session_cards(Vec::new())),
        ),
    ]
}

fn seed_states() -> Vec<(String, DesktopApp)> {
    let mut seeds = vec![(
        "fresh-single-session".to_string(),
        DesktopApp::SingleSession(SingleSessionApp::new(None)),
    )];
    seeds.extend(gallery_seed_states());
    seeds.extend(workspace_seed_states());
    seeds
}

fn state_signature(app: &DesktopApp) -> Result<String, String> {
    serde_json::to_string(&app.snapshot())
        .map_err(|error| format!("snapshot failed to serialize: {error}"))
}

fn key_label(key: &KeyInput) -> String {
    format!("{key:?}")
}

/// Hard invariants checked after every transition. Returns violation text.
fn check_invariants(app: &DesktopApp) -> Vec<String> {
    let mut violations = Vec::new();
    match app {
        DesktopApp::SingleSession(single) => {
            if single.draft_cursor > single.draft.len() {
                violations.push(format!(
                    "draft_cursor {} out of bounds for draft of len {}",
                    single.draft_cursor,
                    single.draft.len()
                ));
            } else if !single.draft.is_char_boundary(single.draft_cursor) {
                violations.push(format!(
                    "draft_cursor {} not on char boundary of draft {:?}",
                    single.draft_cursor, single.draft
                ));
            }
            if !single.body_scroll_lines.is_finite() {
                violations.push(format!(
                    "body_scroll_lines is not finite: {}",
                    single.body_scroll_lines
                ));
            }
        }
        DesktopApp::Workspace(_) => {}
    }

    let snapshot = app.snapshot();
    if let DesktopSurfaceSnapshot::Workspace(workspace_snapshot) = &snapshot.surface {
        let focus_exists = workspace_snapshot
            .surfaces
            .iter()
            .any(|surface| surface.id == workspace_snapshot.focused_surface_id);
        if !focus_exists {
            violations.push(format!(
                "workspace focused_surface_id {} not present among {} surfaces",
                workspace_snapshot.focused_surface_id,
                workspace_snapshot.surfaces.len()
            ));
        }
    }

    violations
}

/// Render oracle for single-session states: vertex build must not panic and
/// must produce finite vertex data.
fn check_render(app: &DesktopApp) -> Vec<String> {
    let mut violations = Vec::new();
    for &size in RENDER_ORACLE_SIZES {
        let built: std::thread::Result<Vec<Vertex>> = match app {
            DesktopApp::SingleSession(single) => {
                let single = single.clone();
                catch_unwind(AssertUnwindSafe(move || {
                    build_single_session_vertices(&single, size, 0.0, 4)
                }))
            }
            DesktopApp::Workspace(workspace) => {
                let workspace = workspace.clone();
                catch_unwind(AssertUnwindSafe(move || {
                    let layout = workspace_render_layout(&workspace, size, Some(size));
                    let mut vertices = Vec::new();
                    build_vertices_into(
                        WorkspaceVertexBuildParams {
                            workspace: &workspace,
                            size,
                            render_layout: layout,
                            focus_pulse: 0.0,
                            space_hold_progress: None,
                            surface_frames: None,
                            exiting_surfaces: &HashMap::new(),
                            workspace_panel_cache: None,
                            status_color: workspace_status_bar_target_color(&workspace),
                            status_text_frame: None,
                        },
                        &mut vertices,
                    );
                    vertices
                }))
            }
        };
        let vertices = match built {
            Ok(vertices) => vertices,
            Err(payload) => {
                violations.push(format!(
                    "vertex build panicked at {}x{}: {}",
                    size.width,
                    size.height,
                    panic_payload_text(&payload)
                ));
                continue;
            }
        };
        if vertices.len() < MIN_EXPECTED_VERTICES {
            violations.push(format!(
                "vertex build at {}x{} produced only {} vertices (frame likely blank)",
                size.width,
                size.height,
                vertices.len()
            ));
        }
        for vertex in &vertices {
            let [x, y] = vertex.position;
            if !x.is_finite() || !y.is_finite() {
                violations.push(format!(
                    "non-finite vertex position [{x}, {y}] at {}x{}",
                    size.width, size.height
                ));
                break;
            }
            if vertex.color.iter().any(|channel| !channel.is_finite()) {
                violations.push(format!(
                    "non-finite vertex color {:?} at {}x{}",
                    vertex.color, size.width, size.height
                ));
                break;
            }
        }
    }
    violations
}

fn panic_payload_text(payload: &Box<dyn std::any::Any + Send>) -> String {
    if let Some(text) = payload.downcast_ref::<&str>() {
        (*text).to_string()
    } else if let Some(text) = payload.downcast_ref::<String>() {
        text.clone()
    } else {
        "non-string panic payload".to_string()
    }
}

fn overlay_open(snapshot_surface: &DesktopSurfaceSnapshot) -> Option<&'static str> {
    match snapshot_surface {
        DesktopSurfaceSnapshot::SingleSession(single) => {
            if single.model_picker_open {
                Some("model_picker")
            } else if single.session_switcher_open {
                Some("session_switcher")
            } else if single.show_help {
                Some("hotkey_help")
            } else if single.show_session_info {
                Some("session_info")
            } else {
                None
            }
        }
        DesktopSurfaceSnapshot::Workspace(_) => None,
    }
}

/// Soft oracle: any overlay should close after a bounded number of Escapes.
fn check_escape_recovery(app: &DesktopApp) -> Option<String> {
    let snapshot = app.snapshot();
    let open = overlay_open(&snapshot.surface)?;
    let mut probe = clone_app(app);
    for _ in 0..ESCAPE_RECOVERY_PRESSES {
        let outcome = catch_unwind(AssertUnwindSafe(|| probe.handle_key(KeyInput::Escape)));
        if outcome.is_err() {
            return Some(format!("escape recovery from {open} panicked"));
        }
        overlay_open(&probe.snapshot().surface)?;
    }
    Some(format!(
        "overlay {open} still open after {ESCAPE_RECOVERY_PRESSES} Escape presses"
    ))
}

#[derive(Default)]
struct WalkReport {
    seeds: usize,
    transitions: usize,
    unique_states: usize,
    hard_violations: Vec<String>,
    soft_findings: Vec<String>,
    slow_transitions: Vec<String>,
    max_transition: Duration,
}

fn walk_seed(
    name: &str,
    seed: DesktopApp,
    alphabet: &[KeyInput],
    budget: &WalkBudget,
    report: &mut WalkReport,
) {
    let started = Instant::now();
    let mut visited: HashSet<String> = HashSet::new();
    let mut queue: VecDeque<(DesktopApp, usize, String)> = VecDeque::new();

    match state_signature(&seed) {
        Ok(signature) => {
            visited.insert(signature);
        }
        Err(error) => {
            report
                .hard_violations
                .push(format!("[{name}] seed snapshot failed: {error}"));
            return;
        }
    }

    for violation in check_invariants(&seed) {
        report
            .hard_violations
            .push(format!("[{name}] seed invariant: {violation}"));
    }
    for violation in check_render(&seed) {
        report
            .hard_violations
            .push(format!("[{name}] seed render: {violation}"));
    }
    if let Some(finding) = check_escape_recovery(&seed) {
        report
            .soft_findings
            .push(format!("[{name}] seed: {finding}"));
    }

    queue.push_back((seed, 0, "<seed>".to_string()));

    while let Some((state, depth, path)) = queue.pop_front() {
        if depth >= budget.max_depth
            || visited.len() >= budget.max_unique_states
            || started.elapsed() > budget.seed_time_budget
        {
            break;
        }
        for key in alphabet {
            if visited.len() >= budget.max_unique_states
                || started.elapsed() > budget.seed_time_budget
            {
                break;
            }
            let mut next = clone_app(&state);
            let key_for_panic = key.clone();
            let transition_started = Instant::now();
            let outcome = catch_unwind(AssertUnwindSafe(|| next.handle_key(key_for_panic)));
            let elapsed = transition_started.elapsed();
            report.transitions += 1;
            report.max_transition = report.max_transition.max(elapsed);
            let step_path = format!("{path} -> {}", key_label(key));
            if elapsed > SLOW_TRANSITION_BUDGET {
                report
                    .slow_transitions
                    .push(format!("[{name}] {step_path}: handle_key took {elapsed:?}"));
            }
            if let Err(payload) = outcome {
                report.hard_violations.push(format!(
                    "[{name}] {step_path}: handle_key panicked: {}",
                    panic_payload_text(&payload)
                ));
                continue;
            }

            let signature = match state_signature(&next) {
                Ok(signature) => signature,
                Err(error) => {
                    report
                        .hard_violations
                        .push(format!("[{name}] {step_path}: {error}"));
                    continue;
                }
            };
            if !visited.insert(signature) {
                continue;
            }

            for violation in check_invariants(&next) {
                report
                    .hard_violations
                    .push(format!("[{name}] {step_path}: {violation}"));
            }
            for violation in check_render(&next) {
                report
                    .hard_violations
                    .push(format!("[{name}] {step_path}: render: {violation}"));
            }
            if let Some(finding) = check_escape_recovery(&next) {
                report
                    .soft_findings
                    .push(format!("[{name}] {step_path}: {finding}"));
            }

            // Snapshot restore round-trip should accept the state's own snapshot.
            let snapshot = next.snapshot();
            let mut restore_target = clone_app(&next);
            if let Err(error) = restore_target.restore_snapshot(snapshot) {
                report.hard_violations.push(format!(
                    "[{name}] {step_path}: snapshot restore round-trip failed: {error}"
                ));
            }

            queue.push_back((next, depth + 1, step_path));
        }
    }

    report.unique_states += visited.len();
    report.seeds += 1;
}

#[test]
fn desktop_state_space_walk_holds_invariants() {
    let alphabet = input_alphabet();
    let budget = walk_budget();
    let mut report = WalkReport::default();

    for (name, seed) in seed_states() {
        walk_seed(&name, seed, &alphabet, &budget, &mut report);
    }

    println!(
        "state-space walk: {} seeds, {} unique states, {} transitions, max transition {:?}",
        report.seeds, report.unique_states, report.transitions, report.max_transition
    );
    if !report.slow_transitions.is_empty() {
        println!(
            "slow transitions over {SLOW_TRANSITION_BUDGET:?} ({}):",
            report.slow_transitions.len()
        );
        for slow in report.slow_transitions.iter().take(20) {
            println!("  {slow}");
        }
    }
    if !report.soft_findings.is_empty() {
        println!("soft findings ({}):", report.soft_findings.len());
        for finding in report.soft_findings.iter().take(50) {
            println!("  {finding}");
        }
    }
    if !report.hard_violations.is_empty() {
        println!("hard violations ({}):", report.hard_violations.len());
        for violation in report.hard_violations.iter().take(50) {
            println!("  {violation}");
        }
    }

    assert!(
        report.hard_violations.is_empty(),
        "state-space walk found {} hard violations (see test output)",
        report.hard_violations.len()
    );
}
