use super::*;

pub(crate) fn load_session_cards_for_desktop() -> Vec<workspace::SessionCard> {
    match session_data::load_recent_session_cards() {
        Ok(cards) => cards,
        Err(error) => {
            desktop_log::error(format_args!(
                "jcode-desktop: failed to load session metadata: {error:#}"
            ));
            Vec::new()
        }
    }
}

pub(crate) fn load_crashed_session_cards_for_desktop() -> Vec<workspace::SessionCard> {
    match session_data::load_crashed_session_cards() {
        Ok(cards) => cards,
        Err(error) => {
            desktop_log::error(format_args!(
                "jcode-desktop: failed to load crashed session metadata: {error:#}"
            ));
            Vec::new()
        }
    }
}

pub(crate) fn spawn_recovery_session_count_scan(
    event_loop_proxy: EventLoopProxy<DesktopUserEvent>,
    startup_trace: DesktopStartupTrace,
) {
    if let Err(error) = spawn_bounded_desktop_async_job("jcode-desktop-recovery-scan", move || {
        startup_trace.mark("recovery scan started");
        let recovery_count = load_crashed_session_cards_for_desktop().len();
        startup_trace.mark(&format!(
            "recovery scan completed ({recovery_count} crashed)"
        ));
        if event_loop_proxy
            .send_event(DesktopUserEvent::RecoveryCount(recovery_count))
            .is_err()
        {
            desktop_log::warn(format_args!(
                "jcode-desktop: failed to deliver recovery count, event loop is closed"
            ));
        }
    }) {
        desktop_log::error(format_args!(
            "jcode-desktop: failed to start recovery scan: {error:#}"
        ));
    }
}

pub(crate) fn spawn_single_session_card_refresh(
    session_id: String,
    event_loop_proxy: EventLoopProxy<DesktopUserEvent>,
) {
    if let Err(error) =
        spawn_bounded_desktop_async_job("jcode-desktop-session-card-refresh", move || {
            let started = Instant::now();
            let card = load_session_cards_for_desktop()
                .into_iter()
                .find(|card| card.session_id == session_id);
            let loaded_in = started.elapsed();
            if event_loop_proxy
                .send_event(DesktopUserEvent::SessionCardLoaded {
                    session_id,
                    card,
                    loaded_in,
                })
                .is_err()
            {
                desktop_log::warn(format_args!(
                    "jcode-desktop: failed to deliver session card refresh, event loop is closed"
                ));
            }
        })
    {
        desktop_log::error(format_args!(
            "jcode-desktop: failed to start session card refresh: {error:#}"
        ));
    }
}

pub(crate) fn spawn_session_cards_load(
    purpose: DesktopSessionCardsPurpose,
    event_loop_proxy: EventLoopProxy<DesktopUserEvent>,
    delay: Duration,
) {
    if let Err(error) = spawn_bounded_desktop_async_job(
        format!("jcode-desktop-session-cards-{purpose:?}"),
        move || {
            if !delay.is_zero() {
                std::thread::sleep(delay);
            }
            let started = Instant::now();
            let cards = load_session_cards_for_desktop();
            let loaded_in = started.elapsed();
            if event_loop_proxy
                .send_event(DesktopUserEvent::SessionCardsLoaded {
                    purpose,
                    cards,
                    loaded_in,
                })
                .is_err()
            {
                desktop_log::warn(format_args!(
                    "jcode-desktop: failed to deliver session cards load, event loop is closed"
                ));
            }
        },
    ) {
        desktop_log::error(format_args!(
            "jcode-desktop: failed to start session card load: {error:#}"
        ));
    }
}

pub(crate) fn spawn_restore_crashed_sessions(event_loop_proxy: EventLoopProxy<DesktopUserEvent>) {
    if let Err(error) = spawn_bounded_desktop_async_job(
        "jcode-desktop-restore-crashed-sessions",
        move || {
            let started = Instant::now();
            let crashed = load_crashed_session_cards_for_desktop();
            let mut restored = 0usize;
            let mut errors = Vec::new();
            for card in crashed {
                match session_launch::launch_validated_resume_session(&card.session_id, &card.title)
                {
                    Ok(()) => restored += 1,
                    Err(error) => errors.push(format!("{}: {error:#}", card.session_id)),
                }
            }
            if event_loop_proxy
                .send_event(DesktopUserEvent::CrashedSessionsRestoreFinished {
                    restored,
                    errors,
                    elapsed: started.elapsed(),
                })
                .is_err()
            {
                desktop_log::warn(format_args!(
                    "jcode-desktop: failed to deliver crashed-session restore result, event loop is closed"
                ));
            }
        },
    ) {
        desktop_log::error(format_args!(
            "jcode-desktop: failed to start crashed-session restore: {error:#}"
        ));
    }
}

pub(crate) fn spawn_github_issue_sync(
    event_loop_proxy: EventLoopProxy<DesktopUserEvent>,
) -> Result<()> {
    spawn_bounded_desktop_async_job("jcode-desktop-github-issues-sync", move || {
        let result = desktop_issue_cache::sync_current_repo_issue_cache()
            .map_err(|error| format!("{error:#}"));
        match &result {
            Ok(summary) => desktop_log::info(format_args!(
                "jcode-desktop: synced {} GitHub issue(s) for {} in {}ms to {} (comment_threads={} comment_errors={})",
                summary.issue_count,
                summary.repo,
                summary.elapsed.as_millis(),
                summary.cache_path.display(),
                summary.fetched_comment_threads,
                summary.comment_fetch_errors
            )),
            Err(error) => desktop_log::warn(format_args!(
                "jcode-desktop: GitHub issue sync failed: {error}"
            )),
        }
        if event_loop_proxy
            .send_event(DesktopUserEvent::GitHubIssuesSyncFinished(result))
            .is_err()
        {
            desktop_log::warn(format_args!(
                "jcode-desktop: failed to deliver GitHub issue sync result"
            ));
        }
    })
}

pub(crate) fn start_pending_github_issue_sync(
    app: &mut DesktopApp,
    sync_running: &mut bool,
    event_loop_proxy: EventLoopProxy<DesktopUserEvent>,
) -> bool {
    if !app.take_github_issue_sync_request() {
        return false;
    }
    if *sync_running {
        app.note_github_issue_sync_already_running();
        return true;
    }
    match spawn_github_issue_sync(event_loop_proxy) {
        Ok(()) => {
            *sync_running = true;
            true
        }
        Err(error) => {
            app.apply_github_issue_sync_result(Err(format!("{error:#}")));
            true
        }
    }
}

/// Start an off-thread transcript load for a session resumed from the
/// switcher (or a promoted workspace card). The result is delivered back to
/// the event loop as `DesktopUserEvent::TranscriptHydrated`, so large
/// transcript parses never stall key handling. Falls back to a synchronous
/// load if the job slot or thread spawn fails.
pub(crate) fn start_pending_transcript_hydration(
    app: &mut DesktopApp,
    event_loop_proxy: EventLoopProxy<DesktopUserEvent>,
) -> bool {
    let Some(session_id) = app.take_pending_transcript_hydration() else {
        return false;
    };
    let job_session_id = session_id.clone();
    let spawned =
        spawn_bounded_desktop_async_job("jcode-desktop-transcript-hydration", move || {
            let started = Instant::now();
            let result = session_data::load_session_transcript_by_id(&job_session_id)
                .map_err(|error| format!("{error:#}"));
            if event_loop_proxy
                .send_event(DesktopUserEvent::TranscriptHydrated {
                    session_id: job_session_id,
                    result,
                    loaded_in: started.elapsed(),
                })
                .is_err()
            {
                desktop_log::warn(format_args!(
                    "jcode-desktop: failed to deliver hydrated transcript"
                ));
            }
        });
    if let Err(error) = spawned {
        desktop_log::warn(format_args!(
            "jcode-desktop: transcript hydration fell back to blocking load: {error:#}"
        ));
        let result = session_data::load_session_transcript_by_id(&session_id)
            .map_err(|error| format!("{error:#}"));
        app.apply_hydrated_transcript(&session_id, result);
    }
    true
}

pub(crate) fn spawn_desktop_preferences_saver()
-> Option<mpsc::Sender<workspace::DesktopPreferences>> {
    let (tx, rx) = mpsc::channel::<workspace::DesktopPreferences>();
    match std::thread::Builder::new()
        .name("jcode-desktop-preferences-saver".to_string())
        .spawn(move || {
            while let Ok(mut preferences) = rx.recv() {
                let received_at = Instant::now();
                let mut coalesced_saves = 1usize;
                while let Ok(next_preferences) = rx.try_recv() {
                    preferences = next_preferences;
                    coalesced_saves += 1;
                }
                save_desktop_preferences_off_ui_thread(
                    preferences,
                    coalesced_saves,
                    received_at.elapsed(),
                );
            }
        }) {
        Ok(_) => Some(tx),
        Err(error) => {
            desktop_log::error(format_args!(
                "jcode-desktop: failed to start preferences saver: {error:#}"
            ));
            None
        }
    }
}

pub(crate) fn queue_desktop_preferences_save(
    workspace: &Workspace,
    preferences_save_tx: &Option<mpsc::Sender<workspace::DesktopPreferences>>,
) {
    let preferences = workspace.preferences();
    if let Some(tx) = preferences_save_tx
        && tx.send(preferences.clone()).is_ok()
    {
        return;
    }

    if let Err(error) =
        spawn_bounded_desktop_async_job("jcode-desktop-preferences-save-once", move || {
            save_desktop_preferences_off_ui_thread(preferences, 1, Duration::ZERO);
        })
    {
        desktop_log::error(format_args!(
            "jcode-desktop: failed to queue preferences save: {error:#}"
        ));
    }
}

pub(crate) fn save_desktop_preferences_off_ui_thread(
    preferences: workspace::DesktopPreferences,
    coalesced_saves: usize,
    queued_for: Duration,
) {
    let started = Instant::now();
    let error = desktop_prefs::save_preferences(&preferences)
        .err()
        .map(|error| format!("{error:#}"));
    log_desktop_preferences_save_profile(
        started.elapsed(),
        queued_for,
        coalesced_saves,
        error.as_deref(),
    );
}
