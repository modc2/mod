use super::*;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum DesktopMode {
    SingleSession,
    WorkspacePrototype,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum DesktopProcessRole {
    Standalone,
    StableHost,
    AppWorker,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum DesktopReloadStrategy {
    FullProcessHandoff,
    AppWorkerRestart,
}

impl DesktopProcessRole {
    pub(crate) fn as_str(self) -> &'static str {
        match self {
            Self::Standalone => "standalone",
            Self::StableHost => "stable_host",
            Self::AppWorker => "app_worker",
        }
    }

    pub(crate) fn reload_strategy(self) -> DesktopReloadStrategy {
        match self {
            Self::Standalone | Self::AppWorker => DesktopReloadStrategy::FullProcessHandoff,
            Self::StableHost => DesktopReloadStrategy::AppWorkerRestart,
        }
    }
}

impl DesktopMode {
    pub(crate) fn as_str(self) -> &'static str {
        match self {
            Self::SingleSession => "single_session",
            Self::WorkspacePrototype => "workspace",
        }
    }

    pub(crate) fn worker_mode(self) -> DesktopWorkerMode {
        match self {
            Self::SingleSession => DesktopWorkerMode::SingleSession,
            Self::WorkspacePrototype => DesktopWorkerMode::Workspace,
        }
    }
}

pub(crate) fn run_desktop_app_worker_process(desktop_mode: DesktopMode) -> Result<()> {
    desktop_log::info(format_args!(
        "jcode-desktop: app worker process started; pid={}",
        std::process::id()
    ));

    let mut stdout = std::io::stdout().lock();
    let ready = DesktopProtocolEnvelope::new(
        1,
        DesktopWorkerToHostMessage::Ready(DesktopWorkerReady {
            worker_pid: std::process::id(),
            mode: desktop_mode.worker_mode(),
        }),
    );
    write_desktop_ipc_frame(&mut stdout, &ready).context("failed to write worker ready frame")?;

    let stdin = std::io::stdin();
    let mut reader = BufReader::new(stdin.lock());
    let mut runtime: Option<DesktopAppRuntime<DesktopApp>> = None;
    let mut latest_window = DesktopWindowState {
        width: DEFAULT_WINDOW_WIDTH as u32,
        height: DEFAULT_WINDOW_HEIGHT as u32,
        scale_factor: 1.0,
        focused: true,
    };
    let mut next_worker_sequence = 2;
    loop {
        let frame: Option<DesktopHostToWorkerEnvelope> =
            desktop_ipc::read_desktop_ipc_frame(&mut reader)
                .context("failed to read host frame")?;
        let Some(frame) = frame else {
            break;
        };
        frame
            .validate_version()
            .context("host sent incompatible protocol frame")?;
        match frame.payload {
            DesktopHostToWorkerMessage::Initialize(init) => {
                latest_window = init.window.clone();
                let mut app = fresh_desktop_app_for_worker_mode(init.mode);
                if let Some(snapshot) = init.snapshot.clone()
                    && let Err(error) = app.restore_snapshot(snapshot)
                {
                    desktop_log::error(format_args!(
                        "jcode-desktop: app worker failed to restore host snapshot: {error:#}"
                    ));
                }
                let app_runtime = DesktopAppRuntime::new(app);
                let scene = desktop_scene_for_worker_runtime(&app_runtime, &latest_window);
                runtime = Some(app_runtime);
                let scene_update = DesktopProtocolEnvelope::new(
                    next_worker_sequence,
                    DesktopWorkerToHostMessage::Scene(DesktopSceneUpdate {
                        animation_active: scene.metadata.animation_active,
                        scene,
                    }),
                );
                next_worker_sequence += 1;
                write_desktop_ipc_frame(&mut stdout, &scene_update)
                    .context("failed to write worker initial scene")?;
            }
            DesktopHostToWorkerMessage::SnapshotRequest { request_id } => {
                if let Some(runtime) = runtime.as_ref() {
                    let snapshot = DesktopProtocolEnvelope::new(
                        next_worker_sequence,
                        DesktopWorkerToHostMessage::Snapshot(DesktopSnapshotResponse {
                            request_id,
                            snapshot: runtime.snapshot(),
                        }),
                    );
                    next_worker_sequence += 1;
                    write_desktop_ipc_frame(&mut stdout, &snapshot)
                        .context("failed to write worker snapshot response")?;
                } else {
                    desktop_log::info(format_args!(
                        "jcode-desktop: app worker received snapshot request {request_id} before initialization"
                    ));
                }
            }
            DesktopHostToWorkerMessage::Shutdown {
                reason:
                    DesktopWorkerShutdownReason::HostExit
                    | DesktopWorkerShutdownReason::Reload
                    | DesktopWorkerShutdownReason::ProtocolMismatch,
            } => break,
            DesktopHostToWorkerMessage::Input(input) => {
                let mut changed = false;
                match input {
                    DesktopInputEvent::Key(key) => {
                        if key.pressed
                            && let Some(runtime) = runtime.as_mut()
                        {
                            let outcome =
                                runtime.handle_key_input(desktop_key_event_to_key_input(&key));
                            runtime
                                .driver_mut()
                                .service_pending_transcript_hydration_blocking();
                            if matches!(outcome, KeyOutcome::ForceReload) {
                                let reload_requested = DesktopProtocolEnvelope::new(
                                    next_worker_sequence,
                                    DesktopWorkerToHostMessage::ReloadRequested,
                                );
                                next_worker_sequence += 1;
                                write_desktop_ipc_frame(&mut stdout, &reload_requested)
                                    .context("failed to write worker reload request")?;
                            } else {
                                changed = true;
                            }
                        }
                    }
                    DesktopInputEvent::Window(DesktopWindowEvent::Resized {
                        width,
                        height,
                        scale_factor,
                    }) => {
                        latest_window.width = width;
                        latest_window.height = height;
                        latest_window.scale_factor = scale_factor;
                        changed = true;
                    }
                    DesktopInputEvent::Window(DesktopWindowEvent::Focused(focused)) => {
                        latest_window.focused = focused;
                    }
                    DesktopInputEvent::Mouse(_) => {}
                }
                if changed && let Some(runtime) = runtime.as_ref() {
                    write_worker_scene_update(
                        &mut stdout,
                        &mut next_worker_sequence,
                        runtime,
                        &latest_window,
                    )
                    .context("failed to write worker input scene")?;
                }
            }
            DesktopHostToWorkerMessage::SessionEvents(batch) => {
                let mut changed = false;
                if let Some(runtime) = runtime.as_mut() {
                    for event in batch.events {
                        if let Some(session_event) =
                            desktop_wire_session_event_to_runtime_event(event)
                        {
                            runtime.apply_session_event(session_event);
                            changed = true;
                        }
                    }
                }
                if changed && let Some(runtime) = runtime.as_ref() {
                    write_worker_scene_update(
                        &mut stdout,
                        &mut next_worker_sequence,
                        runtime,
                        &latest_window,
                    )
                    .context("failed to write worker session event scene")?;
                }
            }
            DesktopHostToWorkerMessage::MetricsAck { .. } => {}
        }
    }

    Ok(())
}

#[cfg(test)]
pub(crate) fn desktop_scene_for_worker_init(init: &DesktopWorkerInit) -> DesktopScene {
    let mut scene = DesktopScene::new(DesktopSceneViewport::new(
        init.window.width as f32,
        init.window.height as f32,
        init.window.scale_factor,
    ));
    scene.metadata.title = init
        .snapshot
        .as_ref()
        .map(|snapshot| snapshot.title.clone());
    scene.metadata.content_ready = init.snapshot.is_some();
    scene.push(DesktopDisplayCommand::Clear(DesktopColor::rgba(
        0.02, 0.024, 0.03, 1.0,
    )));
    scene
}

pub(crate) fn desktop_scene_for_worker_runtime(
    runtime: &DesktopAppRuntime<DesktopApp>,
    window: &DesktopWindowState,
) -> DesktopScene {
    let mut scene = DesktopScene::new(DesktopSceneViewport::new(
        window.width as f32,
        window.height as f32,
        window.scale_factor,
    ));
    scene.push(DesktopDisplayCommand::Clear(DesktopColor::rgba(
        0.02, 0.024, 0.03, 1.0,
    )));
    runtime.build_scene(scene)
}

pub(crate) fn write_worker_scene_update(
    stdout: &mut impl Write,
    next_worker_sequence: &mut u64,
    runtime: &DesktopAppRuntime<DesktopApp>,
    window: &DesktopWindowState,
) -> Result<()> {
    let scene = desktop_scene_for_worker_runtime(runtime, window);
    let scene_update = DesktopProtocolEnvelope::new(
        *next_worker_sequence,
        DesktopWorkerToHostMessage::Scene(DesktopSceneUpdate {
            animation_active: scene.metadata.animation_active,
            scene,
        }),
    );
    *next_worker_sequence += 1;
    write_desktop_ipc_frame(stdout, &scene_update)?;
    Ok(())
}

pub(crate) fn desktop_mode_from_args<'a>(args: impl IntoIterator<Item = &'a str>) -> DesktopMode {
    if args.into_iter().any(|arg| arg == "--workspace") {
        DesktopMode::WorkspacePrototype
    } else {
        DesktopMode::SingleSession
    }
}

pub(crate) fn desktop_process_role_from_args<'a>(
    args: impl IntoIterator<Item = &'a str>,
) -> DesktopProcessRole {
    let mut args = args.into_iter();
    while let Some(arg) = args.next() {
        let role = arg
            .strip_prefix("--desktop-process-role=")
            .or_else(|| {
                (arg == "--desktop-process-role")
                    .then(|| args.next())
                    .flatten()
            })
            .or_else(|| {
                (arg == "--desktop-host")
                    .then_some("host")
                    .or_else(|| (arg == "--desktop-app-worker").then_some("worker"))
            });
        if let Some(role) = role {
            return match role {
                "host" | "stable-host" | "stable_host" => DesktopProcessRole::StableHost,
                "worker" | "app-worker" | "app_worker" => DesktopProcessRole::AppWorker,
                _ => DesktopProcessRole::Standalone,
            };
        }
    }
    DesktopProcessRole::StableHost
}

pub(crate) fn desktop_resume_session_id_from_args<'a>(
    args: impl IntoIterator<Item = &'a str>,
) -> Option<String> {
    let mut args = args.into_iter();
    while let Some(arg) = args.next() {
        if arg == "--resume" {
            return args.next().map(str::to_string);
        }
        if let Some(session_id) = arg.strip_prefix("--resume=") {
            return (!session_id.is_empty()).then(|| session_id.to_string());
        }
    }
    None
}
