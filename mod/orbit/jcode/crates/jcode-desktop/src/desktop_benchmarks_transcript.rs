use super::*;

/// Selection knobs for the real-transcript benchmarks.
///
/// Returns `(max_sessions, min_messages)`: how many of the largest on-disk
/// transcripts to profile, and the minimum message count for a transcript to
/// qualify. Both are overridable via environment variables so a run can target
/// more (or fewer) of the biggest transcripts without a rebuild:
///
/// - `JCODE_DESKTOP_BENCHMARK_SESSIONS` (default 8)
/// - `JCODE_DESKTOP_BENCHMARK_MIN_MESSAGES` (default 24)
pub(crate) fn real_transcript_benchmark_selection() -> (usize, usize) {
    let max_sessions = std::env::var("JCODE_DESKTOP_BENCHMARK_SESSIONS")
        .ok()
        .and_then(|value| value.trim().parse::<usize>().ok())
        .filter(|value| *value > 0)
        .unwrap_or(8);
    let min_messages = std::env::var("JCODE_DESKTOP_BENCHMARK_MIN_MESSAGES")
        .ok()
        .and_then(|value| value.trim().parse::<usize>().ok())
        .unwrap_or(24);
    (max_sessions, min_messages)
}

/// Profile scrolling against the user's real on-disk transcripts.
///
/// This loads the largest real session files (full, untruncated message lists)
/// and drives the exact production windowed-scroll render path: cached body
/// wrap, a sliding text-buffer window, viewport extraction, glyph shaping for
/// the visible window, text areas, and primitive geometry. Per-frame work is
/// reported per session and aggregated so we can attribute any scroll jank to a
/// specific stage on real content rather than synthetic fixtures.
pub(crate) fn run_real_transcript_scroll_benchmark(frames: usize) -> Result<()> {
    let frames = frames.max(1);
    let size = PhysicalSize::new(1200, 760);
    let (max_sessions, min_messages) = real_transcript_benchmark_selection();
    let transcripts = session_data::load_largest_real_transcripts(max_sessions, min_messages)
        .context("failed to load real transcripts for scroll benchmark")?;

    if transcripts.is_empty() {
        println!(
            "{}",
            serde_json::to_string_pretty(&serde_json::json!({
                "frames": frames,
                "sessions": [],
                "note": "no real transcripts with >=24 messages found under ~/.jcode/sessions",
            }))?
        );
        return Ok(());
    }

    let mut session_reports = Vec::new();
    let mut all_frame_samples: Vec<f64> = Vec::new();
    let mut worst_stage_us = 0.0_f64;
    let mut worst_stage_name = String::new();

    for transcript in &transcripts {
        let report = benchmark_real_transcript_scroll(transcript, size, frames);
        if report.worst_stage_us > worst_stage_us {
            worst_stage_us = report.worst_stage_us;
            worst_stage_name = report.worst_stage_name.clone();
        }
        all_frame_samples.extend_from_slice(&report.frame_samples);
        session_reports.push(report);
    }

    let budget_ms = duration_ms(DESKTOP_120FPS_FRAME_BUDGET);
    let aggregate_p50 = percentile_ms(&all_frame_samples, 0.50);
    let aggregate_p95 = percentile_ms(&all_frame_samples, 0.95);
    let aggregate_p99 = percentile_ms(&all_frame_samples, 0.99);
    let aggregate_max = max_sample_ms(&all_frame_samples);
    let passes_budget = aggregate_p99 <= budget_ms;

    let sessions_json = session_reports
        .iter()
        .map(RealTranscriptScrollReport::to_json)
        .collect::<Vec<_>>();

    println!(
        "{}",
        serde_json::to_string_pretty(&serde_json::json!({
            "frames": frames,
            "size": { "width": size.width, "height": size.height },
            "target_frame_budget_ms": budget_ms,
            "sessions_profiled": session_reports.len(),
            "aggregate_full_scroll_frame": {
                "frames": all_frame_samples.len(),
                "p50_ms": aggregate_p50,
                "p95_ms": aggregate_p95,
                "p99_ms": aggregate_p99,
                "max_ms": aggregate_max,
            },
            "worst_stage": { "name": worst_stage_name, "max_us_per_frame": worst_stage_us },
            "passes_120fps_scroll_cpu_budget": passes_budget,
            "sessions": sessions_json,
        }))?
    );
    Ok(())
}

pub(crate) struct RealTranscriptScrollReport {
    session_id: String,
    title: String,
    file_bytes: u64,
    message_count: usize,
    total_body_lines: usize,
    max_scroll_lines: usize,
    body_buffer_rebuilds: usize,
    frame_samples: Vec<f64>,
    stage_totals_us: Vec<(&'static str, f64)>,
    setup_full_relayout_ms: f64,
    worst_stage_name: String,
    worst_stage_us: f64,
    worst_rebuild_us: f64,
    worst_rebuild_window_lines: usize,
    worst_rebuild_max_line_chars: usize,
    worst_rebuild_advanced_lines: usize,
    worst_rebuild_segments: usize,
}

impl RealTranscriptScrollReport {
    fn to_json(&self) -> serde_json::Value {
        let frames = self.frame_samples.len().max(1);
        let total_ms = self.frame_samples.iter().sum::<f64>();
        let stages = self
            .stage_totals_us
            .iter()
            .map(|(name, total_us)| {
                serde_json::json!({
                    "name": name,
                    "mean_us_per_frame": total_us / frames as f64,
                    "total_ms": total_us / 1000.0,
                })
            })
            .collect::<Vec<_>>();
        serde_json::json!({
            "session_id": self.session_id,
            "title": self.title,
            "file_bytes": self.file_bytes,
            "message_count": self.message_count,
            "total_body_lines": self.total_body_lines,
            "max_scroll_lines": self.max_scroll_lines,
            "body_buffer_rebuilds": self.body_buffer_rebuilds,
            "setup_full_body_relayout_ms": self.setup_full_relayout_ms,
            "worst_window_rebuild": {
                "us": self.worst_rebuild_us,
                "window_lines": self.worst_rebuild_window_lines,
                "max_line_chars": self.worst_rebuild_max_line_chars,
                "advanced_shaping_lines": self.worst_rebuild_advanced_lines,
                "segments": self.worst_rebuild_segments,
            },
            "full_scroll_frame": {
                "frames": self.frame_samples.len(),
                "mean_ms_per_frame": total_ms / frames as f64,
                "p50_ms": percentile_ms(&self.frame_samples, 0.50),
                "p95_ms": percentile_ms(&self.frame_samples, 0.95),
                "p99_ms": percentile_ms(&self.frame_samples, 0.99),
                "max_ms": max_sample_ms(&self.frame_samples),
            },
            "subphases": stages,
        })
    }
}

/// Build a `SingleSessionApp` backed by a full real transcript, exactly the way
/// the production resume path hydrates one from disk.
pub(crate) fn real_transcript_scroll_app(
    transcript: &session_data::BenchmarkTranscript,
) -> SingleSessionApp {
    let mut app = SingleSessionApp::new(None);
    app.apply_resumed_session_transcript(transcript.messages.clone());
    app.set_status_label(format!("real transcript: {}", transcript.title));
    app
}

pub(crate) fn benchmark_real_transcript_scroll(
    transcript: &session_data::BenchmarkTranscript,
    size: PhysicalSize<u32>,
    frames: usize,
) -> RealTranscriptScrollReport {
    let mut app = real_transcript_scroll_app(transcript);
    let mut font_system = benchmark_font_system();

    // One-time full body wrap (the cost paid when a transcript is first loaded
    // or the window is resized). After this, scrolling must stay windowed.
    let setup_started = Instant::now();
    let body_lines = single_session_rendered_body_lines_for_tick(&app, size, 0);
    let setup_full_relayout_ms = setup_started.elapsed().as_secs_f64() * 1000.0;
    let total_body_lines = body_lines.len();

    let max_scroll_lines =
        single_session_body_scroll_metrics_for_total_lines(&app, size, total_body_lines)
            .map(|metrics| metrics.max_scroll_lines)
            .unwrap_or(0);

    // Prime the sliding text-buffer window at the bottom of the transcript, the
    // way the app does after hydrating a resumed session.
    app.scroll_body_to_bottom();
    let initial_viewport = single_session_body_viewport_from_lines(&app, size, 0.0, &body_lines);
    let initial_key =
        single_session_text_key_for_tick_with_rendered_body(&app, size, 0, 0.0, &body_lines);
    let mut buffers = single_session_text_buffers_from_key(&initial_key, size, &mut font_system);
    let (mut window_start, mut window_end) =
        single_session_body_text_window_bounds(&initial_viewport);
    if let Some(body_buffer) = buffers.get_mut(1) {
        *body_buffer = single_session_body_text_buffer_from_lines(
            &mut font_system,
            &body_lines[window_start..window_end],
            size,
            app.text_scale(),
        );
        body_buffer.set_scroll(
            initial_viewport
                .start_line
                .saturating_sub(window_start)
                .min(i32::MAX as usize) as i32,
        );
    }
    let mut last_scroll_start = initial_viewport.start_line;

    // Drive a long scroll sweep from bottom to top and back, one whole line per
    // frame, so every frame crosses a new line boundary (the worst realistic
    // continuous-scroll case).
    let span = max_scroll_lines.max(1);
    let mut viewport_us = 0.0;
    let mut window_rebuild_us = 0.0;
    let mut scroll_us = 0.0;
    let mut glyph_us = 0.0;
    let mut areas_us = 0.0;
    let mut vertices_us = 0.0;
    let mut body_buffer_rebuilds = 0usize;

    // Optional diagnostic: capture the single slowest window rebuild and describe
    // the window content so we can attribute the cost (line count, advanced
    // shaping triggers, longest line) rather than guessing.
    let diagnose = std::env::var_os("JCODE_DESKTOP_SCROLL_DIAG").is_some();
    let mut worst_rebuild_us = 0.0_f64;
    let mut worst_rebuild_window_lines = 0usize;
    let mut worst_rebuild_max_line_chars = 0usize;
    let mut worst_rebuild_advanced_lines = 0usize;
    let mut worst_rebuild_segments = 0usize;

    let (frame_samples, _checksum) = benchmark_frame_samples(frames, |frame| {
        // Triangle-wave scroll position covering the full transcript height.
        let phase = frame % (span * 2);
        let target = if phase <= span {
            phase
        } else {
            span * 2 - phase
        };
        app.body_scroll_lines = target as f32;
        let tick = frame as u64;

        let phase_started = Instant::now();
        let viewport = single_session_body_viewport_from_lines(&app, size, 0.0, &body_lines);
        viewport_us += phase_started.elapsed().as_secs_f64() * 1_000_000.0;

        let phase_started = Instant::now();
        if !single_session_body_text_window_contains(window_start, window_end, &viewport) {
            (window_start, window_end) = single_session_body_text_window_bounds(&viewport);
            let rebuild_started = Instant::now();
            if let Some(body_buffer) = buffers.get_mut(1) {
                *body_buffer = single_session_body_text_buffer_from_lines(
                    &mut font_system,
                    &body_lines[window_start..window_end],
                    size,
                    app.text_scale(),
                );
            }
            if diagnose {
                let rebuild_us = rebuild_started.elapsed().as_secs_f64() * 1_000_000.0;
                if rebuild_us > worst_rebuild_us {
                    worst_rebuild_us = rebuild_us;
                    let window = &body_lines[window_start..window_end];
                    worst_rebuild_window_lines = window.len();
                    worst_rebuild_max_line_chars = window
                        .iter()
                        .map(|l| l.text.chars().count())
                        .max()
                        .unwrap_or(0);
                    worst_rebuild_advanced_lines =
                        window.iter().filter(|l| !l.text.is_ascii()).count();
                    worst_rebuild_segments = window.iter().map(|l| l.inline_spans.len() + 1).sum();
                    if let Ok(path) = std::env::var("JCODE_DESKTOP_SCROLL_DIAG_DUMP") {
                        let text = window
                            .iter()
                            .map(|l| l.text.as_str())
                            .collect::<Vec<_>>()
                            .join("\n");
                        let _ = std::fs::write(format!("{path}.{}", transcript.session_id), text);
                    }
                }
            }
            body_buffer_rebuilds += 1;
            last_scroll_start = usize::MAX;
        }
        window_rebuild_us += phase_started.elapsed().as_secs_f64() * 1_000_000.0;

        let phase_started = Instant::now();
        if viewport.start_line != last_scroll_start {
            if let Some(body_buffer) = buffers.get_mut(1) {
                body_buffer.set_scroll(
                    viewport
                        .start_line
                        .saturating_sub(window_start)
                        .min(i32::MAX as usize) as i32,
                );
            }
            last_scroll_start = viewport.start_line;
        }
        scroll_us += phase_started.elapsed().as_secs_f64() * 1_000_000.0;

        let phase_started = Instant::now();
        let glyph_checksum = buffers
            .get(1)
            .map(|body_buffer| {
                body_buffer
                    .layout_runs()
                    .map(|run| run.glyphs.len())
                    .sum::<usize>()
            })
            .unwrap_or_default();
        glyph_us += phase_started.elapsed().as_secs_f64() * 1_000_000.0;

        let phase_started = Instant::now();
        let areas = single_session_text_areas_for_app_with_cached_body_viewport(
            &app, &buffers, size, 0.0, viewport,
        );
        areas_us += phase_started.elapsed().as_secs_f64() * 1_000_000.0;

        let phase_started = Instant::now();
        let vertices = build_single_session_vertices_with_cached_body(
            &app,
            size,
            0.0,
            tick,
            0.0,
            1.0,
            &body_lines,
        );
        vertices_us += phase_started.elapsed().as_secs_f64() * 1_000_000.0;

        buffers.len() ^ areas.len() ^ vertices.len() ^ glyph_checksum
    });

    let stage_totals_us = vec![
        ("viewport_extract", viewport_us),
        ("body_window_rebuild", window_rebuild_us),
        ("body_scroll_set", scroll_us),
        ("glyph_layout_count", glyph_us),
        ("text_areas", areas_us),
        ("primitive_vertices", vertices_us),
    ];
    let frames_f = frames.max(1) as f64;
    let (worst_stage_name, worst_stage_us) = stage_totals_us
        .iter()
        .map(|(name, total)| (name.to_string(), total / frames_f))
        .fold((String::new(), 0.0_f64), |acc, candidate| {
            if candidate.1 > acc.1 { candidate } else { acc }
        });

    RealTranscriptScrollReport {
        session_id: transcript.session_id.clone(),
        title: transcript.title.clone(),
        file_bytes: transcript.file_bytes,
        message_count: transcript.messages.len(),
        total_body_lines,
        max_scroll_lines,
        body_buffer_rebuilds,
        frame_samples,
        stage_totals_us,
        setup_full_relayout_ms,
        worst_stage_name,
        worst_stage_us,
        worst_rebuild_us,
        worst_rebuild_window_lines,
        worst_rebuild_max_line_chars,
        worst_rebuild_advanced_lines,
        worst_rebuild_segments,
    }
}

/// Profile a realistic mix of user *actions* (not just scrolling) against the
/// user's largest real on-disk transcripts. Each action phase is measured
/// separately as per-frame CPU samples and reported as p50/p95/p99/max, plus a
/// `passes_120fps_cpu_budget` flag against the existing frame budget. This is the
/// broad interaction-coverage companion to `--real-transcript-scroll-benchmark`.
pub(crate) fn run_real_transcript_action_benchmark(frames: usize) -> Result<()> {
    let frames = frames.max(1);
    let size = PhysicalSize::new(1200, 760);
    let (max_sessions, min_messages) = real_transcript_benchmark_selection();
    let transcripts = session_data::load_largest_real_transcripts(max_sessions, min_messages)
        .context("failed to load real transcripts for action benchmark")?;

    if transcripts.is_empty() {
        println!(
            "{}",
            serde_json::to_string_pretty(&serde_json::json!({
                "frames": frames,
                "sessions": [],
                "note": "no real transcripts with >=24 messages found under ~/.jcode/sessions",
            }))?
        );
        return Ok(());
    }

    let budget_ms = duration_ms(DESKTOP_120FPS_FRAME_BUDGET);
    // phase name -> all per-frame samples across every session
    let mut phase_samples: std::collections::BTreeMap<&'static str, Vec<f64>> =
        std::collections::BTreeMap::new();
    let mut session_json = Vec::new();

    for transcript in &transcripts {
        let phases = benchmark_real_transcript_actions(transcript, size, frames);
        let phase_json = phases
            .iter()
            .map(|(name, samples)| {
                phase_samples
                    .entry(name)
                    .or_default()
                    .extend_from_slice(samples);
                action_phase_json(name, samples, budget_ms)
            })
            .collect::<Vec<_>>();
        session_json.push(serde_json::json!({
            "session_id": transcript.session_id,
            "title": transcript.title,
            "message_count": transcript.messages.len(),
            "phases": phase_json,
        }));
    }

    let mut aggregate = Vec::new();
    let mut slowest_phase = String::new();
    let mut slowest_p99 = 0.0_f64;
    let mut all_pass = true;
    for (name, samples) in &phase_samples {
        let value = action_phase_json(name, samples, budget_ms);
        let p99 = percentile_ms(samples, 0.99);
        if p99 > slowest_p99 {
            slowest_p99 = p99;
            slowest_phase = (*name).to_string();
        }
        if p99 > budget_ms {
            all_pass = false;
        }
        aggregate.push(value);
    }

    println!(
        "{}",
        serde_json::to_string_pretty(&serde_json::json!({
            "frames_per_phase": frames,
            "size": { "width": size.width, "height": size.height },
            "target_frame_budget_ms": budget_ms,
            "sessions_profiled": transcripts.len(),
            "aggregate_phases": aggregate,
            "slowest_phase": { "name": slowest_phase, "p99_ms": slowest_p99 },
            "passes_120fps_cpu_budget": all_pass,
            "sessions": session_json,
        }))?
    );
    Ok(())
}

pub(crate) fn action_phase_json(name: &str, samples: &[f64], budget_ms: f64) -> serde_json::Value {
    let frames = samples.len().max(1);
    let total_ms = samples.iter().sum::<f64>();
    let p99 = percentile_ms(samples, 0.99);
    serde_json::json!({
        "name": name,
        "frames": samples.len(),
        "mean_ms": total_ms / frames as f64,
        "p50_ms": percentile_ms(samples, 0.50),
        "p95_ms": percentile_ms(samples, 0.95),
        "p99_ms": p99,
        "max_ms": max_sample_ms(samples),
        "passes_budget": p99 <= budget_ms,
    })
}

/// Run every simulated action phase for one transcript, returning per-phase
/// per-frame CPU samples (milliseconds). Each phase reproduces the production
/// render path: cached/wrapped body lines, viewport extraction, a windowed body
/// text buffer that is reused across frames, text areas, and primitive geometry.
pub(crate) fn benchmark_real_transcript_actions(
    transcript: &session_data::BenchmarkTranscript,
    size: PhysicalSize<u32>,
    frames: usize,
) -> Vec<(&'static str, Vec<f64>)> {
    let base_app = real_transcript_scroll_app(transcript);
    let body_lines = single_session_rendered_body_lines_for_tick(&base_app, size, 0);
    let total_lines = body_lines.len();
    let max_scroll =
        single_session_body_scroll_metrics_for_total_lines(&base_app, size, total_lines)
            .map(|metrics| metrics.max_scroll_lines)
            .unwrap_or(0)
            .max(1);

    let mut phases: Vec<(&'static str, Vec<f64>)> = Vec::new();

    // 1. Smooth (fractional) scroll: scroll position advances a whole line per
    //    frame with a fractional offset, the common trackpad-scroll case.
    phases.push((
        "smooth_scroll",
        action_windowed_render_phase(&base_app, &body_lines, size, frames, |app, frame| {
            let phase = frame % (max_scroll * 2);
            let target = if phase <= max_scroll {
                phase
            } else {
                max_scroll * 2 - phase
            };
            app.body_scroll_lines = target as f32;
            benchmark_smooth_scroll_lines(frame)
        }),
    ));

    // 2. Whole-line scroll: integer line steps, no fractional offset.
    phases.push((
        "whole_line_scroll",
        action_windowed_render_phase(&base_app, &body_lines, size, frames, |app, frame| {
            let phase = frame % (max_scroll * 2);
            let target = if phase <= max_scroll {
                phase
            } else {
                max_scroll * 2 - phase
            };
            app.body_scroll_lines = target as f32;
            0.0
        }),
    ));

    // 3. Selection drag across the visible transcript while parked mid-scroll.
    //    This mirrors the real mouse-handler input path, which calls
    //    single_session_visible_body (a full transcript wrap, now memoized) and
    //    hit-tests the cursor on every pointer move, then redraws.
    {
        let mut app = base_app.clone();
        app.body_scroll_lines = (max_scroll / 2) as f32;
        let initial_visible = single_session_visible_body(&app, size);
        if let Some(point) =
            single_session_body_point_at_position(size, 40.0, 80.0, &initial_visible)
        {
            app.begin_selection(point);
        } else {
            app.begin_selection(SelectionPoint { line: 0, column: 0 });
        }
        let mut font_system = benchmark_font_system();
        let (mut buffers, mut window_start, mut window_end, mut last_start) =
            action_prime_window(&app, &body_lines, size, &mut font_system);
        let (samples, _) = benchmark_frame_samples(frames, |frame| {
            // Real input path: resolve the cursor against the visible body
            // (full-transcript wrap, memoized) and update the selection.
            let visible = single_session_visible_body(&app, size);
            let y = 80.0 + (frame % 600) as f32;
            let x = 40.0 + (frame % 400) as f32;
            if let Some(point) = single_session_body_point_at_position(size, x, y, &visible) {
                app.update_selection(point);
            }
            action_render_window(
                &app,
                &body_lines,
                size,
                frame as u64,
                0.0,
                &mut font_system,
                &mut buffers,
                &mut window_start,
                &mut window_end,
                &mut last_start,
            )
        });
        phases.push(("selection_drag", samples));
    }

    // 3b. Pure input-side selection hit-test cost (no redraw). This isolates the
    //     real per-mouse-move work the desktop selection handler does:
    //     single_session_visible_body (a full-transcript wrap, now memoized) plus
    //     cursor hit-testing. The redraw it triggers is separately cached, so this
    //     phase exposes the wrap/memo cost that the combined selection_drag phase
    //     hides behind geometry building.
    {
        let mut app = base_app.clone();
        app.body_scroll_lines = (max_scroll / 2) as f32;
        app.begin_selection(SelectionPoint { line: 0, column: 0 });
        let (samples, _) = benchmark_frame_samples(frames, |frame| {
            let visible = single_session_visible_body(&app, size);
            let y = 80.0 + (frame % 600) as f32;
            let x = 40.0 + (frame % 400) as f32;
            if let Some(point) = single_session_body_point_at_position(size, x, y, &visible) {
                app.update_selection(point);
            }
            visible.len()
        });
        phases.push(("selection_input_hittest", samples));
    }

    // 4. Typing in the composer while parked at the bottom of the transcript.
    {
        let mut app = base_app.clone();
        app.scroll_body_to_bottom();
        app.draft.clear();
        app.draft_cursor = 0;
        let mut font_system = benchmark_font_system();
        let (mut buffers, mut window_start, mut window_end, mut last_start) =
            action_prime_window(&app, &body_lines, size, &mut font_system);
        let (samples, _) = benchmark_frame_samples(frames, |frame| {
            app.draft.push(benchmark_typing_char(frame));
            app.draft_cursor = app.draft.len();
            action_render_window(
                &app,
                &body_lines,
                size,
                frame as u64,
                0.0,
                &mut font_system,
                &mut buffers,
                &mut window_start,
                &mut window_end,
                &mut last_start,
            )
        });
        phases.push(("composer_typing", samples));
    }

    // 5. Model picker open/close toggling over the transcript: every other frame
    //    opens the inline picker card, invalidating the inline-widget geometry.
    {
        let mut app = base_app.clone();
        app.body_scroll_lines = (max_scroll / 3) as f32;
        let mut font_system = benchmark_font_system();
        let (mut buffers, mut window_start, mut window_end, mut last_start) =
            action_prime_window(&app, &body_lines, size, &mut font_system);
        let (samples, _) = benchmark_frame_samples(frames, |frame| {
            app.model_picker.open = frame % 2 == 0;
            app.model_picker.loading = app.model_picker.open;
            action_render_window(
                &app,
                &body_lines,
                size,
                frame as u64,
                0.0,
                &mut font_system,
                &mut buffers,
                &mut window_start,
                &mut window_end,
                &mut last_start,
            )
        });
        app.model_picker.open = false;
        phases.push(("model_picker_toggle", samples));
    }

    // 6. Session switcher open/close toggling over the transcript.
    {
        let mut app = base_app.clone();
        app.body_scroll_lines = (max_scroll / 3) as f32;
        let mut font_system = benchmark_font_system();
        let (mut buffers, mut window_start, mut window_end, mut last_start) =
            action_prime_window(&app, &body_lines, size, &mut font_system);
        let (samples, _) = benchmark_frame_samples(frames, |frame| {
            app.session_switcher.open = frame % 2 == 0;
            action_render_window(
                &app,
                &body_lines,
                size,
                frame as u64,
                0.0,
                &mut font_system,
                &mut buffers,
                &mut window_start,
                &mut window_end,
                &mut last_start,
            )
        });
        app.session_switcher.open = false;
        phases.push(("session_switcher_toggle", samples));
    }

    // 7. Window resize sweep: each frame is a different surface size, forcing a
    //    body re-wrap + window rebuild (the worst non-scroll case).
    //
    //    Mirrors production (`cached_single_session_body_lines` non-streaming
    //    branch): the raw styled lines (markdown parse) are generated ONCE and
    //    cached across sizes; only the width-dependent wrap re-runs per resize.
    {
        let app = base_app.clone();
        let raw_lines = app.body_styled_lines_for_tick(0);
        let mut font_system = benchmark_font_system();
        let (samples, _) = benchmark_frame_samples(frames, |frame| {
            let resize = benchmark_resize_size(frame);
            let lines = single_session_rendered_body_lines_from_raw_ref(&app, resize, &raw_lines);
            let viewport = single_session_body_viewport_from_lines(&app, resize, 0.0, &lines);
            let key =
                single_session_text_key_for_tick_with_rendered_body(&app, resize, 0, 0.0, &lines);
            let mut buffers = single_session_text_buffers_from_key(&key, resize, &mut font_system);
            let (window_start, window_end) = single_session_body_text_window_bounds(&viewport);
            if let Some(body_buffer) = buffers.get_mut(1) {
                *body_buffer = single_session_body_text_buffer_from_lines(
                    &mut font_system,
                    &lines[window_start..window_end],
                    resize,
                    app.text_scale(),
                );
            }
            let areas = single_session_text_areas_for_app_with_cached_body_viewport(
                &app, &buffers, resize, 0.0, viewport,
            );
            let vertices = build_single_session_vertices_with_cached_body(
                &app,
                resize,
                0.0,
                frame as u64,
                0.0,
                1.0,
                &lines,
            );
            buffers.len() ^ areas.len() ^ vertices.len()
        });
        phases.push(("window_resize", samples));
    }

    // 8. Streaming response growth while scrolled near the bottom: a synthetic
    //    assistant reply grows by a chunk each frame, the live-streaming case.
    //
    //    This mirrors the production renderer's incremental path
    //    (`cached_single_session_body_lines` for the streaming branch): the
    //    static transcript body is wrapped ONCE, then each frame only truncates
    //    back to the static base and appends the wrapped streaming tail, rather
    //    than re-wrapping the whole transcript every frame.
    {
        let mut app = base_app.clone();
        app.scroll_body_to_bottom();
        app.streaming_response
            .push_str("Streaming response starting. ");
        let mut font_system = benchmark_font_system();
        let static_base = single_session_rendered_static_body_lines_for_streaming(&app, size, 0)
            .unwrap_or_else(|| single_session_rendered_body_lines_for_tick(&app, size, 0));
        let static_len = static_base.len();
        let mut stream_lines = static_base.clone();
        let (samples, _) = benchmark_frame_samples(frames, |frame| {
            app.streaming_response.push_str(
                "Streaming update chunk with `inline code` and prose that wraps across lines. ",
            );
            if frame % 9 == 0 {
                app.streaming_response.push('\n');
            }
            // Incremental: reuse the wrapped static base, only re-wrap the tail.
            stream_lines.truncate(static_len);
            append_single_session_streaming_response_rendered_body_lines(
                &app,
                size,
                &mut stream_lines,
            );
            let viewport = single_session_body_viewport_from_lines(&app, size, 0.0, &stream_lines);
            let key = single_session_text_key_for_tick_with_rendered_body(
                &app,
                size,
                0,
                0.0,
                &stream_lines,
            );
            let mut buffers = single_session_text_buffers_from_key(&key, size, &mut font_system);
            let (window_start, window_end) = single_session_body_text_window_bounds(&viewport);
            if let Some(body_buffer) = buffers.get_mut(1) {
                *body_buffer = single_session_body_text_buffer_from_lines(
                    &mut font_system,
                    &stream_lines[window_start..window_end],
                    size,
                    app.text_scale(),
                );
            }
            let areas = single_session_text_areas_for_app_with_cached_body_viewport(
                &app, &buffers, size, 0.0, viewport,
            );
            let vertices = build_single_session_vertices_with_cached_body(
                &app,
                size,
                0.0,
                frame as u64,
                0.0,
                1.0,
                &stream_lines,
            );
            buffers.len() ^ areas.len() ^ vertices.len()
        });
        phases.push(("streaming_growth", samples));
    }

    phases
}

/// Prime a reusable text-buffer set and its windowed body buffer for `app`,
/// matching how the production renderer seeds the sliding window. Returns the
/// buffers plus the current (window_start, window_end, last_scroll_start).
pub(crate) fn action_prime_window(
    app: &SingleSessionApp,
    body_lines: &[SingleSessionStyledLine],
    size: PhysicalSize<u32>,
    font_system: &mut FontSystem,
) -> (Vec<Buffer>, usize, usize, usize) {
    let viewport = single_session_body_viewport_from_lines(app, size, 0.0, body_lines);
    let key = single_session_text_key_for_tick_with_rendered_body(app, size, 0, 0.0, body_lines);
    let mut buffers = single_session_text_buffers_from_key(&key, size, font_system);
    let (window_start, window_end) = single_session_body_text_window_bounds(&viewport);
    if let Some(body_buffer) = buffers.get_mut(1) {
        *body_buffer = single_session_body_text_buffer_from_lines(
            font_system,
            &body_lines[window_start..window_end],
            size,
            app.text_scale(),
        );
        body_buffer.set_scroll(
            viewport
                .start_line
                .saturating_sub(window_start)
                .min(i32::MAX as usize) as i32,
        );
    }
    (buffers, window_start, window_end, viewport.start_line)
}

/// Render one frame through the production windowed path, reusing the body text
/// buffer and only rebuilding/rescrolling the window when the viewport leaves it.
#[allow(clippy::too_many_arguments)]
pub(crate) fn action_render_window(
    app: &SingleSessionApp,
    body_lines: &[SingleSessionStyledLine],
    size: PhysicalSize<u32>,
    tick: u64,
    smooth_scroll_lines: f32,
    font_system: &mut FontSystem,
    buffers: &mut [Buffer],
    window_start: &mut usize,
    window_end: &mut usize,
    last_scroll_start: &mut usize,
) -> usize {
    let viewport =
        single_session_body_viewport_from_lines(app, size, smooth_scroll_lines, body_lines);
    if !single_session_body_text_window_contains(*window_start, *window_end, &viewport) {
        let (start, end) = single_session_body_text_window_bounds(&viewport);
        *window_start = start;
        *window_end = end;
        if let Some(body_buffer) = buffers.get_mut(1) {
            *body_buffer = single_session_body_text_buffer_from_lines(
                font_system,
                &body_lines[start..end],
                size,
                app.text_scale(),
            );
        }
        *last_scroll_start = usize::MAX;
    }
    if viewport.start_line != *last_scroll_start {
        if let Some(body_buffer) = buffers.get_mut(1) {
            body_buffer.set_scroll(
                viewport
                    .start_line
                    .saturating_sub(*window_start)
                    .min(i32::MAX as usize) as i32,
            );
        }
        *last_scroll_start = viewport.start_line;
    }
    let areas = single_session_text_areas_for_app_with_cached_body_viewport(
        app,
        buffers,
        size,
        smooth_scroll_lines,
        viewport,
    );
    let vertices = build_single_session_vertices_with_cached_body(
        app,
        size,
        0.0,
        tick,
        smooth_scroll_lines,
        1.0,
        body_lines,
    );
    buffers.len() ^ areas.len() ^ vertices.len()
}

/// Drive a windowed-scroll render phase, calling `prepare` each frame to mutate
/// the app's scroll position (and return any fractional smooth-scroll offset).
pub(crate) fn action_windowed_render_phase(
    base_app: &SingleSessionApp,
    body_lines: &[SingleSessionStyledLine],
    size: PhysicalSize<u32>,
    frames: usize,
    mut prepare: impl FnMut(&mut SingleSessionApp, usize) -> f32,
) -> Vec<f64> {
    let mut app = base_app.clone();
    let mut font_system = benchmark_font_system();
    let (mut buffers, mut window_start, mut window_end, mut last_start) =
        action_prime_window(&app, body_lines, size, &mut font_system);
    let (samples, _) = benchmark_frame_samples(frames, |frame| {
        let smooth = prepare(&mut app, frame);
        action_render_window(
            &app,
            body_lines,
            size,
            frame as u64,
            smooth,
            &mut font_system,
            &mut buffers,
            &mut window_start,
            &mut window_end,
            &mut last_start,
        )
    });
    samples
}
