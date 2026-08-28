use super::*;

pub(crate) fn headless_chat_smoke_message(args: &[String]) -> Option<String> {
    args.iter().enumerate().find_map(|(index, arg)| {
        arg.strip_prefix("--headless-chat-smoke=")
            .map(ToOwned::to_owned)
            .or_else(|| {
                (arg == "--headless-chat-smoke")
                    .then(|| args.get(index + 1).cloned())
                    .flatten()
            })
    })
}

/// Dev-only flag: `--simulate-stream` drives the live single-session app with
/// synthetic streaming deltas so the streaming reveal animation can be observed
/// and recorded without a real backend.
pub(crate) fn simulate_stream_requested(args: &[String]) -> bool {
    args.iter()
        .any(|arg| arg == "--simulate-stream" || arg == "--simulate-streaming")
}

pub(crate) const DESKTOP_STREAM_SIMULATOR_SCRIPT: &str = "Sure, let me walk through how the streaming text reveal works in the desktop app. \
When the provider sends tokens, they arrive in bursty chunks rather than a smooth flow, \
so the renderer keeps a `revealed_chars` cursor that eases toward the full response length. \
The trailing characters get a per-character alpha ramp called the tail fade, \
and a soft breathing cursor sits at the very end of the revealed text to signal activity.\n\n\
Here is a short list of the moving parts:\n\
- The reveal motion integrates a rate proportional to the backlog.\n\
- The body text buffer is rebuilt as the reveal advances.\n\
- A separate overlay buffer paints the streaming tail with its own opacity.\n\n\
Once the response finishes, the overlay hands off to the committed transcript message. \
That handoff should be seamless, with no visible jump or flicker as the text settles into place. \
This paragraph is intentionally long so the streaming text wraps across many lines and the \
viewport scrolls while new tokens keep arriving at the bottom of the transcript.";

/// Seed a small prior transcript so the simulated stream appends after existing
/// messages, mirroring the common case of streaming inside an active session.
pub(crate) fn seed_desktop_stream_simulator_transcript(app: &mut SingleSessionApp) {
    app.replace_session(Some(workspace::SessionCard {
        session_id: "simulate-stream".to_string(),
        title: "Streaming simulation".to_string(),
        subtitle: "dev stream harness".to_string(),
        detail: "fixture".to_string(),
        preview_lines: Vec::new(),
        detail_lines: Vec::new(),
        transcript_messages: Vec::new(),
    }));
    app.messages.push(SingleSessionMessage::user(
        "Explain how the desktop streaming text reveal works.",
    ));
    app.messages.push(SingleSessionMessage::assistant(
        "Earlier reply: the desktop renders streamed assistant text with an adaptive reveal so bursty provider chunks flow in smoothly instead of popping.",
    ));
    app.scroll_body_to_bottom();
}

/// Spawn a background thread that emits synthetic streaming events to exercise
/// the real desktop streaming animation pipeline.
pub(crate) fn spawn_desktop_stream_simulator(
    session_event_tx: mpsc::Sender<session_launch::DesktopSessionEvent>,
) {
    std::thread::Builder::new()
        .name("jcode-desktop-stream-simulator".to_string())
        .spawn(move || {
            // Give the window a moment to come up before streaming starts.
            std::thread::sleep(Duration::from_millis(900));
            if session_event_tx
                .send(session_launch::DesktopSessionEvent::SessionStarted {
                    session_id: "simulate-stream".to_string(),
                })
                .is_err()
            {
                return;
            }
            // Emit word-sized deltas, occasionally bursting several words at once
            // to mimic real provider chunking, with brief stalls between bursts.
            let words: Vec<&str> = DESKTOP_STREAM_SIMULATOR_SCRIPT
                .split_inclusive(' ')
                .collect();
            let mut index = 0usize;
            let mut burst_phase = 0usize;
            while index < words.len() {
                let burst = match burst_phase % 4 {
                    0 => 1,
                    1 => 3,
                    2 => 2,
                    _ => 5,
                };
                burst_phase += 1;
                let end = (index + burst).min(words.len());
                let chunk: String = words[index..end].concat();
                index = end;
                if session_event_tx
                    .send(session_launch::DesktopSessionEvent::TextDelta(chunk))
                    .is_err()
                {
                    return;
                }
                let pause = match burst_phase % 5 {
                    0 => Duration::from_millis(220),
                    3 => Duration::from_millis(120),
                    _ => Duration::from_millis(45),
                };
                std::thread::sleep(pause);
            }
            std::thread::sleep(Duration::from_millis(400));
            let _ = session_event_tx.send(session_launch::DesktopSessionEvent::Done);
        })
        .ok();
}

pub(crate) const DESKTOP_HELP_LINES: &[&str] = &[
    crate::DESKTOP_PRODUCT_NAME,
    "",
    "Usage:",
    "  jcode-desktop [OPTIONS]",
    "",
    "Options:",
    "  --fullscreen                 Start borderless fullscreen",
    "  --workspace                  Open the workspace prototype instead of the single-session chat",
    "  --desktop-process-role ROLE  Internal: standalone, host, or worker",
    "  --desktop-host               Internal alias for --desktop-process-role=host",
    "  --desktop-app-worker         Internal alias for --desktop-process-role=worker",
    "  --startup-log                Print launch timing milestones to stderr",
    "  --startup-benchmark          Print launch timings and exit after the first frame",
    "  --capture-hero-animation DIR Write deterministic hero animation PNG frames and exit",
    "  --capture-gallery-screens DIR Render gallery fixture states to PNGs headlessly and exit",
    "  --capture-keys KEYS          With --capture-gallery-screens: comma-separated keys to replay first",
    "  --capture-size WxH           With --capture-gallery-screens: render size in pixels",
    "  --resize-render-benchmark[N]  Print CPU resize/render benchmark JSON and exit",
    "  --scroll-render-benchmark[N]  Print CPU scroll/render benchmark JSON and exit",
    "  --real-transcript-scroll-benchmark[N]  Profile scrolling against your real on-disk transcripts and exit",
    "  --real-transcript-action-benchmark[N]  Profile mixed user actions (scroll/resize/typing/pickers/selection/streaming) on real transcripts and exit",
    "  --stream-e2e-benchmark[N]     Print stream event-to-paint guardrail JSON and exit",
    "  --headless-chat-smoke <MSG>  Run a hidden backend smoke test and print JSON events",
    "  --headless-chat-smoke=<MSG>  Same as above",
    "  -V, --version                Print version information",
    "  -h, --help                   Print this help",
    "",
];

pub(crate) fn desktop_help_text() -> String {
    DESKTOP_HELP_LINES.join("\n")
}

/// Request for a headless gallery screenshot capture.
///
/// `--capture-gallery-screens DIR` renders every gallery fixture state to a
/// PNG in DIR without opening a window. `--gallery-state STATE` (optional)
/// restricts the capture to a single state, and `--capture-keys KEYSPEC`
/// (optional) replays comma-separated key names against each state before
/// rendering, so arbitrary interaction states can be inspected visually.
pub(crate) struct GalleryScreenshotCaptureRequest {
    output_dir: PathBuf,
    state: Option<String>,
    keys: Vec<String>,
    size: Option<PhysicalSize<u32>>,
}

pub(crate) fn gallery_screenshot_capture_request(
    args: &[String],
) -> Option<GalleryScreenshotCaptureRequest> {
    let output_dir = args.iter().enumerate().find_map(|(index, arg)| {
        arg.strip_prefix("--capture-gallery-screens=")
            .map(PathBuf::from)
            .or_else(|| {
                (arg == "--capture-gallery-screens")
                    .then(|| args.get(index + 1).map(PathBuf::from))
                    .flatten()
            })
    })?;
    let keys = args
        .iter()
        .enumerate()
        .find_map(|(index, arg)| {
            arg.strip_prefix("--capture-keys=")
                .map(str::to_string)
                .or_else(|| {
                    (arg == "--capture-keys")
                        .then(|| args.get(index + 1).cloned())
                        .flatten()
                })
        })
        .map(|spec| {
            spec.split(',')
                .map(str::trim)
                .filter(|part| !part.is_empty())
                .map(str::to_string)
                .collect()
        })
        .unwrap_or_default();
    let size = args
        .iter()
        .enumerate()
        .find_map(|(index, arg)| {
            arg.strip_prefix("--capture-size=")
                .map(str::to_string)
                .or_else(|| {
                    (arg == "--capture-size")
                        .then(|| args.get(index + 1).cloned())
                        .flatten()
                })
        })
        .and_then(|spec| {
            let (width, height) = spec.split_once('x')?;
            Some(PhysicalSize::new(
                width.trim().parse().ok()?,
                height.trim().parse().ok()?,
            ))
        });
    Some(GalleryScreenshotCaptureRequest {
        output_dir,
        state: desktop_gallery::state_from_args(args),
        keys,
        size,
    })
}

/// Parse a key name from `--capture-keys` into a `KeyInput`.
pub(crate) fn capture_key_input(name: &str) -> Option<KeyInput> {
    Some(match name {
        "escape" => KeyInput::Escape,
        "enter" => KeyInput::Enter,
        "backspace" => KeyInput::Backspace,
        "tab" => KeyInput::Autocomplete,
        "submit" => KeyInput::SubmitDraft,
        "model-picker" => KeyInput::OpenModelPicker,
        "session-switcher" => KeyInput::OpenSessionSwitcher,
        "hotkey-help" => KeyInput::HotkeyHelp,
        "session-info" => KeyInput::ToggleSessionInfo,
        "scroll-up" => KeyInput::ScrollBodyLines(-3),
        "scroll-down" => KeyInput::ScrollBodyLines(3),
        "scroll-top" => KeyInput::ScrollBodyToTop,
        "scroll-bottom" => KeyInput::ScrollBodyToBottom,
        "page-up" => KeyInput::ScrollBodyPages(-1),
        "page-down" => KeyInput::ScrollBodyPages(1),
        "text-bigger" => KeyInput::AdjustTextScale(1),
        "text-smaller" => KeyInput::AdjustTextScale(-1),
        other => {
            let text = other.strip_prefix("char:")?;
            KeyInput::Character(text.to_string())
        }
    })
}

pub(crate) async fn run_gallery_screenshot_capture(
    request: &GalleryScreenshotCaptureRequest,
) -> Result<()> {
    std::fs::create_dir_all(&request.output_dir).with_context(|| {
        format!(
            "failed to create gallery screenshot directory {}",
            request.output_dir.display()
        )
    })?;
    let states: Vec<String> = match &request.state {
        Some(state) => vec![state.clone()],
        None => desktop_gallery::gallery_states()
            .iter()
            .map(|state| state.to_string())
            .collect(),
    };
    let keys = request
        .keys
        .iter()
        .map(|name| {
            capture_key_input(name).with_context(|| format!("unknown capture key name {name:?}"))
        })
        .collect::<Result<Vec<_>>>()?;
    let size = request.size.unwrap_or_else(|| {
        PhysicalSize::new(DEFAULT_WINDOW_WIDTH as u32, DEFAULT_WINDOW_HEIGHT as u32)
    });
    let mut manifest = Vec::new();
    for state in &states {
        let mut app = desktop_gallery::temporary_app(state);
        for key in &keys {
            app.handle_key(key.clone());
        }
        let DesktopApp::SingleSession(single) = &mut app else {
            anyhow::bail!("gallery screenshot capture only supports single-session states");
        };
        single.settle_animations_for_capture();
        let single = &*single;
        let rendered_lines = single_session_rendered_body_lines_for_tick(single, size, 4);
        let widget_geometry =
            inline_widget_capture_geometry(single, size, rendered_lines.len()).map(
                |(card, text_top, line_height, visible_text_bottom, visible_text_right)| {
                    serde_json::json!({
                        "card": { "x": card.x, "y": card.y, "width": card.width, "height": card.height },
                        "text_top": text_top,
                        "line_height": line_height,
                        "visible_text_bottom": visible_text_bottom,
                        "visible_text_right": visible_text_right,
                    })
                },
            );
        let (image, vertices) = render_hero_frame_to_image(single, size, 4, 1.0, false).await?;
        let filename = if request.keys.is_empty() {
            format!("gallery-{state}.png")
        } else {
            let key_part = request
                .keys
                .join("+")
                .chars()
                .map(|ch| {
                    if ch.is_ascii_alphanumeric() || matches!(ch, '+' | '-' | '_' | ':') {
                        ch
                    } else {
                        '_'
                    }
                })
                .collect::<String>();
            format!("gallery-{state}+{key_part}.png")
        };
        let path = request.output_dir.join(&filename);
        image
            .save(&path)
            .with_context(|| format!("failed to save {}", path.display()))?;
        manifest.push(serde_json::json!({
            "state": state,
            "file": filename,
            "keys": request.keys,
            "vertices": vertices,
            "inline_widget": widget_geometry,
            "snapshot": serde_json::to_value(app.snapshot())?,
        }));
    }
    println!(
        "{}",
        serde_json::json!({
            "output_dir": request.output_dir,
            "screens": manifest,
        })
    );
    Ok(())
}
