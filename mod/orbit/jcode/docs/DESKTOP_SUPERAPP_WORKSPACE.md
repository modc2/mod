# Desktop Superapp Workspace Direction

Status: Proposed
Updated: 2026-04-25

This document refines the Jcode Desktop product direction from a single chat-like app into a **Niri-like agent workspace superapp**.

The app should eventually host multiple kinds of surfaces:

- agent sessions
- task/activity views
- code/file surfaces
- diffs
- terminals or command output surfaces
- settings/auth/tools/debug surfaces

The goal is not to clone a general-purpose window manager. The goal is to give Jcode users a fast, keyboard-driven, spatial environment for supervising multiple agent sessions and related development tools inside one custom desktop app.

See also:

- [`DESKTOP_APP_ARCHITECTURE.md`](./DESKTOP_APP_ARCHITECTURE.md)
- [`DESKTOP_CODEBASE_ARCHITECTURE.md`](./DESKTOP_CODEBASE_ARCHITECTURE.md)
- [`MULTI_SESSION_CLIENT_ARCHITECTURE.md`](./MULTI_SESSION_CLIENT_ARCHITECTURE.md)

## Product thesis

Jcode Desktop should be a **local AI development superapp**:

```text
one native app
  many sessions
  many surfaces
  fast spatial navigation
  strong keyboard workflow
  agent-first activity visibility
  custom rendering and layout
```

The key UX is closer to:

- Niri / scrollable tiling workspace
- Vim-like keyboard navigation
- command palette
- agent mission control

And less like:

- a single chat window
- a conventional IDE clone
- a web-app shell
- a generic desktop window manager

## Why Niri-like

Niri's useful idea for Jcode is not the compositor implementation. It is the mental model:

- surfaces are arranged spatially
- focus moves predictably
- users navigate with keyboard-first commands
- new work appears in a structured place
- layout is persistent enough to build muscle memory
- many parallel tasks can be monitored without losing context

Jcode Desktop can bring that workflow to macOS users who do not have a Niri-like environment, while still working well on Linux.

## Workspace model

The desktop app should be built around these concepts:

```text
Workspace
  -> Rows / Workspaces / Lanes
    -> Columns
      -> Surfaces
```

Terminology can be adjusted, but the core model should support:

- multiple agent sessions visible or quickly reachable
- spatial navigation with `h/j/k/l`-style movement
- opening related surfaces next to a session
- moving surfaces between columns/lanes
- zooming/focusing one surface temporarily
- preserving layout per project/workspace

Suggested initial terms:

| Term | Meaning |
|---|---|
| Workspace | A project/repo-level desktop environment |
| Lane | A vertical grouping or Niri-like workspace row |
| Column | A horizontal focus/navigation unit |
| Surface | A visible app panel: session, file/code view, diff, activity, settings, debug, etc. |
| Session surface | A surface attached to a server-owned Jcode session |
| Tool surface | File/code/diff/activity/settings/debug/etc. |

## Surface types

The app should be architected around a generic surface registry from the start.

```rust
enum SurfaceKind {
    AgentSession,
    Activity,
    WorkspaceFiles,
    CodeView,
    Diff,
    TerminalOutput,
    Settings,
    Debug,
    Extension,
}
```

A surface should have:

```rust
struct SurfaceState {
    id: SurfaceId,
    kind: SurfaceKind,
    title: String,
    workspace_id: WorkspaceId,
    lane_id: LaneId,
    column_id: ColumnId,
    focus_state: FocusState,
    local_state: SurfaceLocalState,
}
```

The surface model should be independent from the renderer so it can support:

- one window with many surfaces
- multiple windows later
- pop-out surfaces later
- session surfaces and non-session utility surfaces using the same navigation model

## Agent sessions as first-class surfaces

An agent session should be one surface type, not the whole app.

```text
AgentSessionSurface
  - transcript timeline
  - composer
  - inline tool cards
  - session status
  - session-local queue/interrupts
```

This allows layouts like:

```text
[Session A] [Session B] [Diff     ]
[Activity ] [Files    ] [CodeView ]
```

Or:

```text
Lane 1: main task
  Column 1: coordinator session
  Column 2: implementation agent session
  Column 3: diff/editor

Lane 2: review
  Column 1: changed files
  Column 2: notes/session
```

## Navigation model

The app should have a modal/command-oriented keyboard model inspired by Vim, but adapted for macOS and desktop expectations.

### Important macOS constraint

Do not rely on plain `Cmd+H` for left navigation.

On macOS:

- `Cmd+H` hides the app
- `Cmd+M` minimizes
- `Cmd+Q` quits
- `Cmd+W` closes the current window/surface depending on app convention
- `Cmd+,` opens settings

Overriding these will make the app feel hostile to Mac users.

### Recommended navigation approach

Use one or both of these:

1. **Leader/command mode**
   - Press a leader key, then `h/j/k/l`.
   - Example: `Space h`, `Space j`, `Space k`, `Space l` when focus is not in text input.
   - Or `Cmd+K h/j/k/l` as a command chord.

2. **Direct advanced shortcuts**
   - `Cmd+Option+H/J/K/L` for focus movement on macOS.
   - `Ctrl+Alt+H/J/K/L` or `Super+Alt+H/J/K/L` on Linux.

The leader model is safer because it avoids macOS reserved shortcuts and works well with Vim muscle memory.

### Suggested initial keymap

```text
Focus movement:
  leader h      focus left
  leader j      focus down / next lane
  leader k      focus up / previous lane
  leader l      focus right

Surface movement:
  leader H      move surface left
  leader J      move surface down
  leader K      move surface up
  leader L      move surface right

Workspace/session:
  leader n      new agent session
  leader s      session switcher
  leader a      activity center
  leader e      editor/files surface
  leader d      diff surface
  leader /      command palette
  leader z      zoom focused surface
  leader x      close focused surface

Agent control:
  leader Enter  focus composer / submit depending mode
  leader Esc    cancel/stop focused agent run, with confirmation if risky
```

The exact leader key should be configurable. Reasonable defaults:

- `Space` when focus is not in a text input
- `Cmd+K` as a universal command chord
- `Ctrl+Space` as an alternate for users who prefer explicit mode entry

## Input modes

The app should distinguish between navigation mode and text-entry mode.

```text
Navigation mode
  - hjkl controls focus/layout
  - keys trigger commands
  - typing can open command palette or focused composer depending setting

Text-entry mode
  - keys edit composer/editor/input
  - Escape returns to navigation mode
  - platform shortcuts still work: copy/paste/select all
```

This is critical once the app has text-entry surfaces. Without explicit input modes, global Vim-like keys will conflict with text entry.

## Layout behavior

The first implementation does not need full Niri behavior. It should start with a simpler model that can evolve.

### MVP layout

```text
single app window
  left sidebar: workspaces/sessions
  central surface grid: columns with focused surface
  right activity/inspector panel optional
```

MVP navigation:

- focus next/previous surface
- move focus left/right between columns
- open new session to the right
- close surface
- zoom focused surface
- activity panel toggle

### Later layout

Niri-like scrollable layout:

- horizontal columns per lane
- vertical lane/workspace movement
- smooth animated focus movement
- persistent surface positions
- per-workspace layout restoration
- drag surfaces with mouse, but keyboard remains primary
- pop-out surface into native window
- dock pop-out surface back into workspace

## Surface lifecycle

Surface commands should be consistent across surface kinds.

```text
new-surface(kind)
close-surface(id)
focus-surface(direction)
move-surface(direction)
zoom-surface(id)
split-surface(kind, direction)
pop-out-surface(id)
dock-surface(id)
```

Agent session-specific commands become specialized actions on an `AgentSession` surface:

```text
send-message
cancel-run
soft-interrupt
background-tool
resume-session
fork-session
```

Non-session surfaces can add specialized commands later without changing generic surface lifecycle commands.

## Optional future surfaces

Do not preplan any large embedded app right now. The workspace model should stay generic enough to host future surface kinds, but the implementation plan should focus on agent sessions, activity, files, diffs, and command routing.

If a major embedded surface is considered later, it should go through its own design decision rather than being assumed by the initial desktop architecture.

## Built-in code editor direction

A built-in editor is a large system and should remain optional until the workspace/session workflow is strong.

Suggested levels:

### Level 1: file viewer and external editor

MVP-friendly:

- file tree / changed files
- read-only file preview
- open in external editor
- open diff externally
- copy paths/snippets

### Level 2: lightweight code viewer/diff editor

Useful and realistic:

- syntax-highlighted file view
- search within file
- inline diff viewer
- accept/reject generated changes later
- simple text selection/copy

### Level 3: real code editor

Large but possible later:

- rope text buffer
- multi-cursor maybe
- undo/redo
- syntax highlighting
- LSP integration
- diagnostics
- completion
- file save/reload conflict handling
- large-file performance

### Recommendation

Start with Level 1, then Level 2. Do not build a full editor before the agent workspace, transcript, activity, and diff workflow are excellent.

The architecture should support file/code surfaces generically, but should not commit to a full editor implementation early.

## Activity as a persistent surface

For a superapp, activity should not be just a small panel.

Activity should be a surface type that can be:

- pinned to the side
- opened as a full surface
- filtered by workspace/session/tool type
- navigated with the same surface commands
- used to jump to the relevant session/tool output

This is important because Jcode users may run many agents/tasks concurrently.

## Command palette as the universal router

The command palette should be the universal way to access everything:

- sessions
- surfaces
- files
- commands
- settings
- tools
- files and code views
- background tasks
- debug views

It should be backed by a shared command registry in `jcode-client-core`, not hardcoded separately per UI.

## Data model additions

`jcode-client-core` should include a workspace layout state model:

```rust
struct WorkspaceLayoutState {
    workspaces: Vec<WorkspaceNode>,
    active_workspace: WorkspaceId,
    active_surface: Option<SurfaceId>,
}

struct WorkspaceNode {
    id: WorkspaceId,
    name: String,
    lanes: Vec<LaneNode>,
}

struct LaneNode {
    id: LaneId,
    columns: Vec<ColumnNode>,
}

struct ColumnNode {
    id: ColumnId,
    surfaces: Vec<SurfaceId>,
    active_surface_index: usize,
}
```

Surface-local data should be separated by kind:

```rust
enum SurfaceLocalState {
    AgentSession(AgentSessionSurfaceState),
    Activity(ActivitySurfaceState),
    WorkspaceFiles(WorkspaceFilesSurfaceState),
    CodeView(CodeViewSurfaceState),
    Diff(DiffSurfaceState),
    TerminalOutput(TerminalOutputSurfaceState),
    Settings(SettingsSurfaceState),
    Debug(DebugSurfaceState),
    Extension(ExtensionSurfaceState),
}
```

This preserves the core rule:

> A session is server-owned runtime state. A surface is client-owned UI state.

## Renderer implications

A Niri-like superapp increases the importance of the custom UI engine.

The UI engine must support:

- nested split/column/lane layout
- animated or smooth focus movement later
- virtualized surfaces
- focus rings and active-surface indicators
- surface chrome/title bars that do not waste space
- zoom/focus mode
- drag-to-rearrange later
- stable IDs for accessibility/debugging
- cheap offscreen/inactive surface representation

Do not keep every surface fully rendered at all times. Inactive surfaces should keep state but avoid expensive layout/text/render work unless visible or prewarmed.

## Suggested first superapp milestone

Update the earlier fake-data desktop prototype to prove the superapp model, not only one transcript.

### Milestone: fake-data spatial workspace

Success criteria:

- one native window on Linux
- custom `wgpu` rendering
- workspace layout with multiple fake agent session surfaces
- focus movement with leader + `h/j/k/l`
- open/close/move/zoom fake surfaces
- activity surface with fake running tasks
- command palette can create session/activity/file/diff/debug placeholder surfaces
- transcript surfaces are virtualized independently
- debug HUD shows per-surface layout/render stats
- idle CPU remains near zero

Optional non-session surfaces can be placeholders at this stage. The important part is proving that the workspace model can host multiple surface kinds without committing to specific future apps.

## Product guardrails

Because “superapp” can explode in scope, keep these guardrails:

1. Agent sessions and activity are the core product.
2. Non-session surfaces are supporting tools, not the first milestone.
3. External integrations should come before embedded implementations.
4. Keyboard navigation must work before mouse drag layout polish.
5. Surface architecture must be generic from day one.
6. Do not build large embedded apps before diff/review workflows are excellent.
7. Keep the server as the source of truth for sessions and agents.

## Summary decision

Jcode Desktop should become a **keyboard-driven, Niri-like agent workspace superapp**.

The initial desktop app should prove:

- many session surfaces
- spatial navigation
- generic surface lifecycle
- command palette routing
- activity visibility
- performance under multiple visible surfaces

Then additional file/diff/tool surfaces can be added without changing the fundamental app model.
