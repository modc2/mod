use super::*;

pub(crate) fn run_scroll_render_benchmark(frames: usize) -> Result<()> {
    let frames = frames.max(1);
    let size = PhysicalSize::new(1200, 760);
    let mut app = desktop_scroll_benchmark_app();
    if let Some(metrics) = single_session_body_scroll_metrics(&app, size, 0) {
        app.body_scroll_lines = metrics.max_scroll_lines as f32 / 2.0;
    }

    let mut setup_font_system = benchmark_font_system();
    let setup_started = Instant::now();
    let setup_key = single_session_text_key_for_tick_with_scroll(&app, size, 0, 0.0);
    let setup_buffers =
        single_session_text_buffers_from_key(&setup_key, size, &mut setup_font_system);
    let setup_areas =
        single_session_text_areas_for_app_with_scroll(&app, &setup_buffers, size, 0, 0.0);
    let setup_vertices =
        build_single_session_vertices_with_scroll_and_reveal(&app, size, 0.0, 0, 0.0, 1.0);
    let setup_elapsed = setup_started.elapsed();
    let setup_checksum =
        setup_key.body.len() ^ setup_buffers.len() ^ setup_areas.len() ^ setup_vertices.len();

    let cold_fresh_app = SingleSessionApp::new(None);
    let cold_fresh_started = Instant::now();
    let cold_phase_started = Instant::now();
    let mut cold_fresh_font_system = benchmark_font_system();
    let cold_fresh_font_ms = cold_phase_started.elapsed().as_secs_f64() * 1000.0;
    let cold_phase_started = Instant::now();
    let cold_fresh_key =
        single_session_text_key_for_tick_with_scroll(&cold_fresh_app, size, 0, 0.0);
    let cold_fresh_key_ms = cold_phase_started.elapsed().as_secs_f64() * 1000.0;
    let cold_phase_started = Instant::now();
    let cold_fresh_buffers =
        single_session_text_buffers_from_key(&cold_fresh_key, size, &mut cold_fresh_font_system);
    let cold_fresh_buffers_ms = cold_phase_started.elapsed().as_secs_f64() * 1000.0;
    let cold_phase_started = Instant::now();
    let cold_fresh_areas = single_session_text_areas_for_app_with_scroll(
        &cold_fresh_app,
        &cold_fresh_buffers,
        size,
        0,
        0.0,
    );
    let cold_fresh_areas_ms = cold_phase_started.elapsed().as_secs_f64() * 1000.0;
    let cold_phase_started = Instant::now();
    let cold_fresh_vertices = build_single_session_vertices_with_scroll_and_reveal(
        &cold_fresh_app,
        size,
        0.0,
        0,
        0.0,
        1.0,
    );
    let cold_fresh_vertices_ms = cold_phase_started.elapsed().as_secs_f64() * 1000.0;
    let cold_fresh_ms = cold_fresh_started.elapsed().as_secs_f64() * 1000.0;
    let cold_fresh_checksum = cold_fresh_key.body.len()
        ^ cold_fresh_buffers.len()
        ^ cold_fresh_areas.len()
        ^ cold_fresh_vertices.len();

    let prewarmed_fresh_app = SingleSessionApp::new(None);
    let mut prewarmed_fresh_font_system = benchmark_font_system();
    let prewarmed_fresh_started = Instant::now();
    let prewarmed_fresh_key =
        single_session_text_key_for_tick_with_scroll(&prewarmed_fresh_app, size, 0, 0.0);
    let prewarmed_fresh_buffers = single_session_text_buffers_from_key(
        &prewarmed_fresh_key,
        size,
        &mut prewarmed_fresh_font_system,
    );
    let prewarmed_fresh_areas = single_session_text_areas_for_app_with_scroll(
        &prewarmed_fresh_app,
        &prewarmed_fresh_buffers,
        size,
        0,
        0.0,
    );
    let prewarmed_fresh_vertices = build_single_session_vertices_with_scroll_and_reveal(
        &prewarmed_fresh_app,
        size,
        0.0,
        0,
        0.0,
        1.0,
    );
    let prewarmed_fresh_ms = prewarmed_fresh_started.elapsed().as_secs_f64() * 1000.0;
    let prewarmed_fresh_checksum = prewarmed_fresh_key.body.len()
        ^ prewarmed_fresh_buffers.len()
        ^ prewarmed_fresh_areas.len()
        ^ prewarmed_fresh_vertices.len();

    let warm_fresh_app = SingleSessionApp::new(None);
    let mut warm_fresh_font_system = benchmark_font_system();
    let warm_fresh_initial_key =
        single_session_text_key_for_tick_with_scroll(&warm_fresh_app, size, 0, 0.0);
    let warm_fresh_initial_buffers = single_session_text_buffers_from_key(
        &warm_fresh_initial_key,
        size,
        &mut warm_fresh_font_system,
    );
    let warm_fresh_started = Instant::now();
    let warm_fresh_next_key =
        single_session_text_key_for_tick_with_scroll(&warm_fresh_app, size, 1, 0.0);
    let warm_fresh_buffers = single_session_text_buffers_from_key_reusing_unchanged(
        &warm_fresh_next_key,
        Some(&warm_fresh_initial_key),
        warm_fresh_initial_buffers,
        true,
        size,
        &mut warm_fresh_font_system,
    );
    let warm_fresh_areas = single_session_text_areas_for_app_with_scroll(
        &warm_fresh_app,
        &warm_fresh_buffers,
        size,
        1,
        0.0,
    );
    let warm_fresh_vertices = build_single_session_vertices_with_scroll_and_reveal(
        &warm_fresh_app,
        size,
        0.0,
        1,
        0.0,
        1.0,
    );
    let warm_fresh_ms = warm_fresh_started.elapsed().as_secs_f64() * 1000.0;
    let warm_fresh_checksum = warm_fresh_next_key.body.len()
        ^ warm_fresh_buffers.len()
        ^ warm_fresh_areas.len()
        ^ warm_fresh_vertices.len();

    let mut legacy_font_system = benchmark_font_system();
    let (legacy_smooth_text_ms, legacy_smooth_text_checksum) = benchmark_phase(frames, |frame| {
        let tick = frame as u64;
        let smooth_scroll_lines = benchmark_smooth_scroll_lines(frame);
        let key =
            single_session_text_key_for_tick_with_scroll(&app, size, tick, smooth_scroll_lines);
        let buffers = single_session_text_buffers_from_key(&key, size, &mut legacy_font_system);
        let areas = single_session_text_areas_for_app_with_scroll(
            &app,
            &buffers,
            size,
            tick,
            smooth_scroll_lines,
        );
        let vertices = build_single_session_vertices_with_scroll_and_reveal(
            &app,
            size,
            0.0,
            tick,
            smooth_scroll_lines,
            1.0,
        );
        key.body.len() ^ buffers.len() ^ areas.len() ^ vertices.len()
    });

    let mut optimized_font_system = benchmark_font_system();
    let optimized_key = single_session_text_key_for_tick_with_scroll(&app, size, 0, 0.0);
    let optimized_buffers =
        single_session_text_buffers_from_key(&optimized_key, size, &mut optimized_font_system);
    let optimized_areas =
        single_session_text_areas_for_app_with_scroll(&app, &optimized_buffers, size, 0, 0.0);
    let optimized_body_lines = single_session_rendered_body_lines_for_tick(&app, size, 0);
    let (optimized_smooth_geometry_ms, optimized_smooth_geometry_checksum) =
        benchmark_phase(frames, |frame| {
            let tick = frame as u64;
            let smooth_scroll_lines = benchmark_smooth_scroll_lines(frame);
            let vertices = build_single_session_vertices_with_cached_body(
                &app,
                size,
                0.0,
                tick,
                smooth_scroll_lines,
                1.0,
                &optimized_body_lines,
            );
            optimized_key.body.len()
                ^ optimized_buffers.len()
                ^ optimized_areas.len()
                ^ vertices.len()
        });

    let mut whole_line_app = app.clone();
    let mut whole_line_font_system = benchmark_font_system();
    let whole_line_body_lines =
        single_session_rendered_body_lines_for_tick(&whole_line_app, size, 0);
    let (whole_line_text_ms, whole_line_text_checksum) = benchmark_phase(frames, |frame| {
        whole_line_app.scroll_body_lines(if frame % 2 == 0 { 1 } else { -1 });
        let tick = frame as u64;
        let key = single_session_text_key_for_tick_with_rendered_body(
            &whole_line_app,
            size,
            tick,
            0.0,
            &whole_line_body_lines,
        );
        let buffers = single_session_text_buffers_from_key(&key, size, &mut whole_line_font_system);
        let areas = single_session_text_areas_for_app_with_cached_body(
            &whole_line_app,
            &buffers,
            size,
            0.0,
            &whole_line_body_lines,
        );
        let vertices = build_single_session_vertices_with_cached_body(
            &whole_line_app,
            size,
            0.0,
            tick,
            0.0,
            1.0,
            &whole_line_body_lines,
        );
        key.body.len() ^ buffers.len() ^ areas.len() ^ vertices.len()
    });

    let mut visible_whole_line_app = app.clone();
    let mut visible_whole_line_font_system = benchmark_font_system();
    let visible_whole_line_body_lines =
        single_session_rendered_body_lines_for_tick(&visible_whole_line_app, size, 0);
    let visible_whole_line_key = single_session_text_key_for_tick_with_rendered_body(
        &visible_whole_line_app,
        size,
        0,
        0.0,
        &visible_whole_line_body_lines,
    );
    let mut visible_whole_line_buffers = single_session_text_buffers_from_key(
        &visible_whole_line_key,
        size,
        &mut visible_whole_line_font_system,
    );
    let mut visible_whole_line_start = single_session_body_viewport_from_lines(
        &visible_whole_line_app,
        size,
        0.0,
        &visible_whole_line_body_lines,
    )
    .start_line;
    let initial_visible_viewport = single_session_body_viewport_from_lines(
        &visible_whole_line_app,
        size,
        0.0,
        &visible_whole_line_body_lines,
    );
    let (mut visible_window_start, mut visible_window_end) =
        single_session_body_text_window_bounds(&initial_visible_viewport);
    if let Some(body_buffer) = visible_whole_line_buffers.get_mut(1) {
        *body_buffer = single_session_body_text_buffer_from_lines(
            &mut visible_whole_line_font_system,
            &visible_whole_line_body_lines[visible_window_start..visible_window_end],
            size,
            visible_whole_line_app.text_scale(),
        );
        body_buffer.set_scroll(
            initial_visible_viewport
                .start_line
                .saturating_sub(visible_window_start)
                .min(i32::MAX as usize) as i32,
        );
    }
    let mut visible_viewport_ms = 0.0;
    let mut visible_window_ms = 0.0;
    let mut visible_scroll_ms = 0.0;
    let mut visible_glyph_ms = 0.0;
    let mut visible_areas_ms = 0.0;
    let mut visible_vertices_ms = 0.0;
    let (visible_whole_line_text_ms, visible_whole_line_text_checksum) =
        benchmark_phase(frames, |frame| {
            visible_whole_line_app.scroll_body_lines(if frame % 2 == 0 { 1 } else { -1 });
            let tick = frame as u64;
            let phase_started = Instant::now();
            let viewport = single_session_body_viewport_from_lines(
                &visible_whole_line_app,
                size,
                0.0,
                &visible_whole_line_body_lines,
            );
            visible_viewport_ms += phase_started.elapsed().as_secs_f64() * 1000.0;
            let phase_started = Instant::now();
            if !single_session_body_text_window_contains(
                visible_window_start,
                visible_window_end,
                &viewport,
            ) {
                (visible_window_start, visible_window_end) =
                    single_session_body_text_window_bounds(&viewport);
                if let Some(body_buffer) = visible_whole_line_buffers.get_mut(1) {
                    *body_buffer = single_session_body_text_buffer_from_lines(
                        &mut visible_whole_line_font_system,
                        &visible_whole_line_body_lines[visible_window_start..visible_window_end],
                        size,
                        visible_whole_line_app.text_scale(),
                    );
                }
                visible_whole_line_start = usize::MAX;
            }
            visible_window_ms += phase_started.elapsed().as_secs_f64() * 1000.0;
            let phase_started = Instant::now();
            if viewport.start_line != visible_whole_line_start {
                if let Some(body_buffer) = visible_whole_line_buffers.get_mut(1) {
                    body_buffer.set_scroll(
                        viewport
                            .start_line
                            .saturating_sub(visible_window_start)
                            .min(i32::MAX as usize) as i32,
                    );
                }
                visible_whole_line_start = viewport.start_line;
            }
            visible_scroll_ms += phase_started.elapsed().as_secs_f64() * 1000.0;
            let phase_started = Instant::now();
            let glyph_checksum = visible_whole_line_buffers
                .get(1)
                .map(|body_buffer| {
                    body_buffer
                        .layout_runs()
                        .map(|run| run.glyphs.len())
                        .sum::<usize>()
                })
                .unwrap_or_default();
            visible_glyph_ms += phase_started.elapsed().as_secs_f64() * 1000.0;
            let phase_started = Instant::now();
            let areas = single_session_text_areas_for_app_with_cached_body_viewport(
                &visible_whole_line_app,
                &visible_whole_line_buffers,
                size,
                0.0,
                viewport,
            );
            visible_areas_ms += phase_started.elapsed().as_secs_f64() * 1000.0;
            let phase_started = Instant::now();
            let vertices = build_single_session_vertices_with_cached_body(
                &visible_whole_line_app,
                size,
                0.0,
                tick,
                0.0,
                1.0,
                &visible_whole_line_body_lines,
            );
            visible_vertices_ms += phase_started.elapsed().as_secs_f64() * 1000.0;
            visible_whole_line_key.body.len()
                ^ visible_whole_line_buffers.len()
                ^ areas.len()
                ^ vertices.len()
                ^ glyph_checksum
        });

    let mut typing_app = app.clone();
    typing_app.scroll_body_to_bottom();
    typing_app.draft.clear();
    typing_app.draft_cursor = 0;
    let typing_body_lines = single_session_rendered_body_lines_for_tick(&typing_app, size, 0);
    let mut typing_font_system = benchmark_font_system();
    let typing_initial_key = single_session_text_key_for_tick_with_rendered_body(
        &typing_app,
        size,
        0,
        0.0,
        &typing_body_lines,
    );
    let mut typing_buffers =
        single_session_text_buffers_from_key(&typing_initial_key, size, &mut typing_font_system);
    let typing_initial_viewport =
        single_session_body_viewport_from_lines(&typing_app, size, 0.0, &typing_body_lines);
    let (typing_window_start, typing_window_end) =
        single_session_body_text_window_bounds(&typing_initial_viewport);
    if let Some(body_buffer) = typing_buffers.get_mut(1) {
        *body_buffer = single_session_body_text_buffer_from_lines(
            &mut typing_font_system,
            &typing_body_lines[typing_window_start..typing_window_end],
            size,
            typing_app.text_scale(),
        );
    }
    let mut typing_previous_key = Some(typing_initial_key);
    let mut typing_text_cache_ms = 0.0;
    let mut typing_areas_ms = 0.0;
    let mut typing_vertices_ms = 0.0;
    let (typing_redraw_ms, typing_redraw_checksum) = benchmark_phase(frames, |frame| {
        let ch = benchmark_typing_char(frame);
        typing_app.draft.push(ch);
        typing_app.draft_cursor = typing_app.draft.len();
        let tick = frame as u64;

        let phase_started = Instant::now();
        let key = single_session_text_key_for_tick_with_rendered_body(
            &typing_app,
            size,
            tick,
            0.0,
            &typing_body_lines,
        );
        let draft_len = key.draft.len();
        let previous_key = typing_previous_key.take();
        let old_buffers = std::mem::take(&mut typing_buffers);
        typing_buffers = single_session_text_buffers_from_key_reusing_unchanged(
            &key,
            previous_key.as_ref(),
            old_buffers,
            true,
            size,
            &mut typing_font_system,
        );
        typing_previous_key = Some(key);
        typing_text_cache_ms += phase_started.elapsed().as_secs_f64() * 1000.0;

        let phase_started = Instant::now();
        let areas = single_session_text_areas_for_app_with_cached_body(
            &typing_app,
            &typing_buffers,
            size,
            0.0,
            &typing_body_lines,
        );
        typing_areas_ms += phase_started.elapsed().as_secs_f64() * 1000.0;

        let phase_started = Instant::now();
        let vertices = build_single_session_vertices_with_cached_body(
            &typing_app,
            size,
            0.0,
            tick,
            0.0,
            1.0,
            &typing_body_lines,
        );
        typing_vertices_ms += phase_started.elapsed().as_secs_f64() * 1000.0;

        draft_len ^ typing_buffers.len() ^ areas.len() ^ vertices.len()
    });

    let mut fresh_typing_app = SingleSessionApp::new(None);
    fresh_typing_app.draft.clear();
    fresh_typing_app.draft_cursor = 0;
    let mut fresh_typing_font_system = benchmark_font_system();
    let mut fresh_typing_text_cache_ms = 0.0;
    let mut fresh_typing_areas_ms = 0.0;
    let mut fresh_typing_vertices_ms = 0.0;
    let (fresh_typing_ms, fresh_typing_checksum) = benchmark_phase(frames, |frame| {
        let ch = benchmark_typing_char(frame);
        fresh_typing_app.draft.push(ch);
        fresh_typing_app.draft_cursor = fresh_typing_app.draft.len();
        let tick = frame as u64;

        let phase_started = Instant::now();
        let key = single_session_text_key_for_tick_with_scroll(&fresh_typing_app, size, tick, 0.0);
        let buffers =
            single_session_text_buffers_from_key(&key, size, &mut fresh_typing_font_system);
        fresh_typing_text_cache_ms += phase_started.elapsed().as_secs_f64() * 1000.0;

        let phase_started = Instant::now();
        let areas = single_session_text_areas_for_app_with_scroll(
            &fresh_typing_app,
            &buffers,
            size,
            tick,
            0.0,
        );
        fresh_typing_areas_ms += phase_started.elapsed().as_secs_f64() * 1000.0;

        let phase_started = Instant::now();
        let vertices = build_single_session_vertices_with_scroll_and_reveal(
            &fresh_typing_app,
            size,
            0.0,
            tick,
            0.0,
            1.0,
        );
        fresh_typing_vertices_ms += phase_started.elapsed().as_secs_f64() * 1000.0;

        key.draft.len() ^ buffers.len() ^ areas.len() ^ vertices.len()
    });

    let mut streaming_app = app.clone();
    streaming_app.scroll_body_to_bottom();
    streaming_app.streaming_response.clear();
    let mut streaming_font_system = benchmark_font_system();
    let mut streaming_body_lines =
        single_session_rendered_body_lines_for_tick(&streaming_app, size, 0);
    let mut streaming_base_key = None;
    let mut streaming_base_len = 0usize;
    let streaming_initial_key = single_session_text_key_for_tick_with_rendered_body(
        &streaming_app,
        size,
        0,
        0.0,
        &streaming_body_lines,
    );
    let mut streaming_buffers = single_session_text_buffers_from_key(
        &streaming_initial_key,
        size,
        &mut streaming_font_system,
    );
    let streaming_initial_viewport =
        single_session_body_viewport_from_lines(&streaming_app, size, 0.0, &streaming_body_lines);
    let (mut streaming_window_start, mut streaming_window_end) =
        single_session_body_text_window_bounds(&streaming_initial_viewport);
    if let Some(body_buffer) = streaming_buffers.get_mut(1) {
        *body_buffer = single_session_body_text_buffer_from_lines(
            &mut streaming_font_system,
            &streaming_body_lines[streaming_window_start..streaming_window_end],
            size,
            streaming_app.text_scale(),
        );
        body_buffer.set_scroll(
            streaming_initial_viewport
                .start_line
                .saturating_sub(streaming_window_start)
                .min(i32::MAX as usize) as i32,
        );
    }
    let mut streaming_previous_key = Some(streaming_initial_key);
    let mut streaming_tail_text_key = None;
    let mut streaming_tail_text_start_line = None;
    let mut streaming_tail_text_buffer = None;
    let mut streaming_body_ms = 0.0;
    let mut streaming_text_cache_ms = 0.0;
    let mut streaming_areas_ms = 0.0;
    let mut streaming_vertices_ms = 0.0;
    let mut streaming_static_base_rebuilds = 0usize;
    let mut streaming_tail_text_buffer_rebuilds = 0usize;
    let (streaming_delta_ms, streaming_delta_checksum) = benchmark_phase(frames, |frame| {
        streaming_app
            .streaming_response
            .push(benchmark_typing_char(frame));
        if frame % 17 == 0 {
            streaming_app.streaming_response.push('\n');
        }
        let tick = frame as u64;

        let phase_started = Instant::now();
        if !streaming_app.streaming_response.is_empty() {
            let base_key = streaming_app.rendered_body_static_cache_key((size.width, size.height));
            if streaming_base_key != Some(base_key) {
                streaming_static_base_rebuilds += 1;
                streaming_body_lines = single_session_rendered_static_body_lines_for_streaming(
                    &streaming_app,
                    size,
                    tick,
                )
                .unwrap_or_else(|| {
                    single_session_rendered_body_lines_for_tick(&streaming_app, size, tick)
                });
                streaming_base_len = streaming_body_lines.len();
                streaming_base_key = Some(base_key);
            } else {
                streaming_body_lines.truncate(streaming_base_len);
            }
            append_single_session_streaming_response_rendered_body_lines(
                &streaming_app,
                size,
                &mut streaming_body_lines,
            );
        } else {
            streaming_body_lines =
                single_session_rendered_body_lines_for_tick(&streaming_app, size, tick);
            streaming_base_key = None;
            streaming_base_len = 0;
        }
        streaming_body_ms += phase_started.elapsed().as_secs_f64() * 1000.0;

        let phase_started = Instant::now();
        let key = single_session_text_key_for_tick_with_rendered_body(
            &streaming_app,
            size,
            tick,
            0.0,
            &streaming_body_lines,
        );
        let viewport = single_session_body_viewport_from_lines(
            &streaming_app,
            size,
            0.0,
            &streaming_body_lines,
        );
        let visible_static_start = viewport.start_line.min(streaming_base_len);
        let visible_static_end = viewport
            .start_line
            .saturating_add(viewport.lines.len())
            .min(streaming_base_len);
        let desired_streaming_window_start = visible_static_start
            .saturating_sub(SINGLE_SESSION_STREAMING_BODY_TEXT_WINDOW_BEFORE_LINES);
        let desired_streaming_window_end = visible_static_end
            .saturating_add(SINGLE_SESSION_STREAMING_BODY_TEXT_WINDOW_AFTER_LINES)
            .min(streaming_base_len)
            .max(desired_streaming_window_start);
        let body_window_contains = streaming_window_start == desired_streaming_window_start
            && streaming_window_end == desired_streaming_window_end;
        let previous_key = streaming_previous_key.take();
        let mut old_buffers = std::mem::take(&mut streaming_buffers);
        if old_buffers.len() > 1 && !body_window_contains {
            streaming_window_start = desired_streaming_window_start;
            streaming_window_end = desired_streaming_window_end;
            old_buffers[1] = single_session_body_text_buffer_from_lines(
                &mut streaming_font_system,
                &streaming_body_lines[streaming_window_start..streaming_window_end],
                size,
                streaming_app.text_scale(),
            );
        }
        let can_reuse_body_buffer = old_buffers.len() > 1;
        streaming_buffers = single_session_text_buffers_from_key_reusing_unchanged(
            &key,
            previous_key.as_ref(),
            old_buffers,
            can_reuse_body_buffer,
            size,
            &mut streaming_font_system,
        );
        if let Some(body_buffer) = streaming_buffers.get_mut(1) {
            body_buffer.set_scroll(
                viewport
                    .start_line
                    .saturating_sub(streaming_window_start)
                    .min(i32::MAX as usize) as i32,
            );
        }
        let streaming_start_line =
            streaming_base_len.saturating_add(usize::from(!streaming_app.messages.is_empty()));
        let visible_start = viewport.start_line;
        let visible_end = viewport.start_line.saturating_add(viewport.lines.len());
        let streaming_visible_start = streaming_start_line.max(visible_start);
        let streaming_visible_end = streaming_body_lines.len().min(visible_end);
        if streaming_visible_start < streaming_visible_end {
            let mut hasher = DefaultHasher::new();
            (size.width, size.height).hash(&mut hasher);
            streaming_app.text_scale().to_bits().hash(&mut hasher);
            streaming_visible_start.hash(&mut hasher);
            streaming_visible_end.hash(&mut hasher);
            streaming_body_lines[streaming_visible_start..streaming_visible_end].hash(&mut hasher);
            let tail_key = hasher.finish();
            if streaming_tail_text_key != Some(tail_key) {
                streaming_tail_text_buffer_rebuilds += 1;
                streaming_tail_text_buffer = Some(single_session_body_text_buffer_from_lines(
                    &mut streaming_font_system,
                    &streaming_body_lines[streaming_visible_start..streaming_visible_end],
                    size,
                    streaming_app.text_scale(),
                ));
                streaming_tail_text_key = Some(tail_key);
                streaming_tail_text_start_line = Some(streaming_visible_start);
            }
        } else {
            streaming_tail_text_key = None;
            streaming_tail_text_start_line = None;
            streaming_tail_text_buffer = None;
        }
        streaming_previous_key = Some(key);
        streaming_text_cache_ms += phase_started.elapsed().as_secs_f64() * 1000.0;

        let phase_started = Instant::now();
        let mut areas = single_session_text_areas_for_app_with_cached_body_viewport(
            &streaming_app,
            &streaming_buffers,
            size,
            0.0,
            viewport.clone(),
        );
        if let (Some(buffer), Some(start_line)) = (
            streaming_tail_text_buffer.as_ref(),
            streaming_tail_text_start_line,
        ) {
            areas.push(single_session_streaming_text_area_for_cached_body_viewport(
                &streaming_app,
                buffer,
                size,
                viewport,
                start_line,
                1.0,
                0.0,
            ));
        }
        streaming_areas_ms += phase_started.elapsed().as_secs_f64() * 1000.0;

        let phase_started = Instant::now();
        let vertices = build_single_session_vertices_with_cached_body(
            &streaming_app,
            size,
            0.0,
            tick,
            0.0,
            1.0,
            &streaming_body_lines,
        );
        streaming_vertices_ms += phase_started.elapsed().as_secs_f64() * 1000.0;

        streaming_body_lines.len()
            ^ streaming_buffers.len()
            ^ streaming_tail_text_buffer.is_some() as usize
            ^ streaming_static_base_rebuilds
            ^ streaming_tail_text_buffer_rebuilds
            ^ areas.len()
            ^ vertices.len()
    });

    let mut long_streaming_app = app.clone();
    long_streaming_app.scroll_body_to_bottom();
    long_streaming_app.streaming_response = (0..512)
        .map(|index| {
            format!(
                "### partial heading {index}\n- live item with **bold** text and `code` span number {index}\n"
            )
        })
        .collect::<String>();
    let (long_streaming_body_wrap_ms, long_streaming_body_wrap_checksum) =
        benchmark_phase(frames, |frame| {
            long_streaming_app
                .streaming_response
                .push(benchmark_typing_char(frame));
            if frame % 29 == 0 {
                long_streaming_app.streaming_response.push('\n');
            }
            let mut rendered_lines = Vec::new();
            append_single_session_streaming_response_rendered_body_lines(
                &long_streaming_app,
                size,
                &mut rendered_lines,
            );
            rendered_lines.len() ^ long_streaming_app.streaming_response.len()
        });
    let mut long_streaming_line_count_app = long_streaming_app.clone();
    let (long_streaming_line_count_ms, long_streaming_line_count_checksum) =
        benchmark_phase(frames, |frame| {
            long_streaming_line_count_app
                .streaming_response
                .push(benchmark_typing_char(frame));
            if frame % 31 == 0 {
                long_streaming_line_count_app.streaming_response.push('\n');
            }
            single_session_streaming_response_rendered_body_line_count(
                &long_streaming_line_count_app,
                size,
            ) ^ long_streaming_line_count_app.streaming_response.len()
        });
    let mut long_unbroken_streaming_app = app.clone();
    long_unbroken_streaming_app.streaming_response = "x".repeat(8192);
    let (long_unbroken_streaming_wrap_ms, long_unbroken_streaming_wrap_checksum) =
        benchmark_phase(frames, |frame| {
            long_unbroken_streaming_app
                .streaming_response
                .push(benchmark_typing_char(frame));
            let mut rendered_lines = Vec::new();
            append_single_session_streaming_response_rendered_body_lines(
                &long_unbroken_streaming_app,
                size,
                &mut rendered_lines,
            );
            rendered_lines.len() ^ long_unbroken_streaming_app.streaming_response.len()
        });

    let mut event_batch_app = DesktopApp::SingleSession(SingleSessionApp::new(None));
    let (event_batch_ms, event_batch_checksum) = benchmark_phase(frames, |frame| {
        let events = (0..128)
            .map(|offset| {
                session_launch::DesktopSessionEvent::TextDelta(
                    benchmark_typing_char(frame + offset).to_string(),
                )
            })
            .collect::<Vec<_>>();
        let original_events = events.len();
        let coalesced = coalesce_desktop_session_events(events);
        let coalesced_events = coalesced.len();
        apply_desktop_session_event_batch(&mut event_batch_app, coalesced);
        original_events ^ coalesced_events
    });

    let (event_forwarder_flood_ms, event_forwarder_flood_checksum) =
        benchmark_phase(frames, |frame| {
            let (tx, rx) = mpsc::channel();
            for offset in 0..(BACKEND_EVENT_FORWARD_MAX_RAW_EVENTS * 3) {
                // Receiver lives for the whole closure; ignore the
                // impossible-error instead of panicking in a benchmark.
                let _ = tx.send(session_launch::DesktopSessionEvent::TextDelta(
                    benchmark_typing_char(frame + offset).to_string(),
                ));
            }
            let batch = collect_desktop_session_event_batch(
                session_launch::DesktopSessionEvent::TextDelta(
                    benchmark_typing_char(frame).to_string(),
                ),
                &rx,
            );
            let remaining_is_queued = rx.try_recv().is_ok();
            batch.raw_event_count
                ^ batch.raw_payload_bytes
                ^ batch.events.len()
                ^ usize::from(remaining_is_queued)
        });
    let end_to_end_stream_flood =
        run_desktop_stream_end_to_end_benchmark(BACKEND_EVENT_FORWARD_MAX_RAW_EVENTS * 6);

    let mut hero_app = desktop_scroll_benchmark_app_with_turns(24);
    let hero_body_lines = single_session_rendered_body_lines_for_tick(&hero_app, size, 0);
    let hero_boundary_scroll =
        benchmark_hero_boundary_scroll_lines(&hero_app, size, &hero_body_lines);
    hero_app.body_scroll_lines = hero_boundary_scroll;
    let mut hero_font_system = benchmark_font_system();
    let hero_initial_key = single_session_text_key_for_tick_with_rendered_body(
        &hero_app,
        size,
        0,
        0.0,
        &hero_body_lines,
    );
    let mut hero_buffers =
        single_session_text_buffers_from_key(&hero_initial_key, size, &mut hero_font_system);
    let hero_initial_viewport =
        single_session_body_viewport_from_lines(&hero_app, size, 0.0, &hero_body_lines);
    let (mut hero_window_start, mut hero_window_end) =
        single_session_body_text_window_bounds(&hero_initial_viewport);
    if let Some(body_buffer) = hero_buffers.get_mut(1) {
        *body_buffer = single_session_body_text_buffer_from_lines(
            &mut hero_font_system,
            &hero_body_lines[hero_window_start..hero_window_end],
            size,
            hero_app.text_scale(),
        );
    }
    let mut hero_previous_key = Some(hero_initial_key);
    let mut hero_viewport_key_ms = 0.0;
    let mut hero_window_rebuild_ms = 0.0;
    let mut hero_buffer_reuse_ms = 0.0;
    let mut hero_body_buffer_rebuilds = 0usize;
    let mut hero_text_cache_ms = 0.0;
    let mut hero_areas_ms = 0.0;
    let mut hero_vertices_ms = 0.0;
    let (hero_boundary_scroll_ms, hero_boundary_checksum) = benchmark_phase(frames, |frame| {
        let tick = frame as u64;
        let scroll_offset = (frame % 24) as f32 - 12.0;
        hero_app.body_scroll_lines = (hero_boundary_scroll + scroll_offset).max(0.0);
        let smooth_scroll_lines = benchmark_smooth_scroll_lines(frame);

        let phase_started = Instant::now();
        let subphase_started = Instant::now();
        let viewport = single_session_body_viewport_from_lines(
            &hero_app,
            size,
            smooth_scroll_lines,
            &hero_body_lines,
        );
        let key = single_session_text_key_for_tick_with_rendered_body(
            &hero_app,
            size,
            tick,
            smooth_scroll_lines,
            &hero_body_lines,
        );
        hero_viewport_key_ms += subphase_started.elapsed().as_secs_f64() * 1000.0;

        let subphase_started = Instant::now();
        let previous_key = hero_previous_key.take();
        let mut old_buffers = std::mem::take(&mut hero_buffers);
        if old_buffers.len() > 1
            && !single_session_body_text_window_contains(
                hero_window_start,
                hero_window_end,
                &viewport,
            )
        {
            hero_body_buffer_rebuilds += 1;
            (hero_window_start, hero_window_end) =
                single_session_body_text_window_bounds(&viewport);
            old_buffers[1] = single_session_body_text_buffer_from_lines(
                &mut hero_font_system,
                &hero_body_lines[hero_window_start..hero_window_end],
                size,
                hero_app.text_scale(),
            );
        }
        hero_window_rebuild_ms += subphase_started.elapsed().as_secs_f64() * 1000.0;

        let subphase_started = Instant::now();
        let can_reuse_body_buffer = old_buffers.len() > 1;
        hero_buffers = single_session_text_buffers_from_key_reusing_unchanged(
            &key,
            previous_key.as_ref(),
            old_buffers,
            can_reuse_body_buffer,
            size,
            &mut hero_font_system,
        );
        if let Some(body_buffer) = hero_buffers.get_mut(1) {
            body_buffer.set_scroll(
                viewport
                    .start_line
                    .saturating_sub(hero_window_start)
                    .min(i32::MAX as usize) as i32,
            );
        }
        let hero_visible = key.fresh_welcome_visible;
        hero_previous_key = Some(key);
        hero_buffer_reuse_ms += subphase_started.elapsed().as_secs_f64() * 1000.0;
        hero_text_cache_ms += phase_started.elapsed().as_secs_f64() * 1000.0;

        let phase_started = Instant::now();
        let areas = single_session_text_areas_for_app_with_cached_body_viewport(
            &hero_app,
            &hero_buffers,
            size,
            smooth_scroll_lines,
            viewport,
        );
        hero_areas_ms += phase_started.elapsed().as_secs_f64() * 1000.0;

        let phase_started = Instant::now();
        let vertices = build_single_session_vertices_with_cached_body(
            &hero_app,
            size,
            0.0,
            tick,
            smooth_scroll_lines,
            1.0,
            &hero_body_lines,
        );
        hero_vertices_ms += phase_started.elapsed().as_secs_f64() * 1000.0;

        hero_buffers.len() ^ areas.len() ^ vertices.len() ^ usize::from(hero_visible)
    });

    let mut action_input_app = DesktopApp::SingleSession(SingleSessionApp::new(None));
    let (action_input_ms, action_input_checksum) = benchmark_phase(frames, |frame| {
        let events = (0..128)
            .map(|offset| session_launch::DesktopSessionEvent::ToolInput {
                id: None,
                delta: benchmark_typing_char(frame + offset).to_string(),
            })
            .collect::<Vec<_>>();
        let coalesced = coalesce_desktop_session_events(events);
        let visible_changed = apply_desktop_session_event_batch(&mut action_input_app, coalesced);
        usize::from(visible_changed)
    });

    let mut action_app = desktop_scroll_benchmark_app_with_turns(64);
    action_app.scroll_body_to_bottom();
    action_app.apply_session_event(session_launch::DesktopSessionEvent::ToolStarted {
        id: None,
        name: "bash".to_string(),
    });
    let mut action_font_system = benchmark_font_system();
    let mut action_body_key = action_app.rendered_body_cache_key((size.width, size.height));
    let mut action_body_lines = single_session_rendered_body_lines_for_tick(&action_app, size, 0);
    let action_initial_key = single_session_text_key_for_tick_with_rendered_body(
        &action_app,
        size,
        0,
        0.0,
        &action_body_lines,
    );
    let mut action_buffers =
        single_session_text_buffers_from_key(&action_initial_key, size, &mut action_font_system);
    let action_initial_viewport =
        single_session_body_viewport_from_lines(&action_app, size, 0.0, &action_body_lines);
    let (mut action_window_start, mut action_window_end) =
        single_session_body_text_window_bounds(&action_initial_viewport);
    if let Some(body_buffer) = action_buffers.get_mut(1) {
        *body_buffer = single_session_body_text_buffer_from_lines(
            &mut action_font_system,
            &action_body_lines[action_window_start..action_window_end],
            size,
            action_app.text_scale(),
        );
    }
    let mut action_previous_key = Some(action_initial_key);
    let mut action_apply_ms = 0.0;
    let mut action_body_ms = 0.0;
    let mut action_text_cache_ms = 0.0;
    let mut action_areas_ms = 0.0;
    let mut action_vertices_ms = 0.0;
    let (action_visible_ms, action_visible_checksum) = benchmark_phase(frames, |frame| {
        let phase_started = Instant::now();
        action_app.apply_session_event(session_launch::DesktopSessionEvent::ToolInput {
            id: None,
            delta: format!(" chunk-{frame}"),
        });
        action_app.apply_session_event(session_launch::DesktopSessionEvent::ToolExecuting {
            id: None,
            name: "bash".to_string(),
        });
        action_apply_ms += phase_started.elapsed().as_secs_f64() * 1000.0;
        let tick = frame as u64;

        let phase_started = Instant::now();
        let next_body_key = action_app.rendered_body_cache_key((size.width, size.height));
        let action_body_changed = action_body_key != next_body_key;
        if action_body_changed {
            action_body_lines =
                single_session_rendered_body_lines_for_tick(&action_app, size, tick);
            action_body_key = next_body_key;
        }
        action_body_ms += phase_started.elapsed().as_secs_f64() * 1000.0;

        let phase_started = Instant::now();
        let key = single_session_text_key_for_tick_with_rendered_body(
            &action_app,
            size,
            tick,
            0.0,
            &action_body_lines,
        );
        let viewport =
            single_session_body_viewport_from_lines(&action_app, size, 0.0, &action_body_lines);
        let previous_key = action_previous_key.take();
        let mut old_buffers = std::mem::take(&mut action_buffers);
        if old_buffers.len() > 1
            && (action_body_changed
                || !single_session_body_text_window_contains(
                    action_window_start,
                    action_window_end,
                    &viewport,
                ))
        {
            (action_window_start, action_window_end) =
                single_session_body_text_window_bounds(&viewport);
            old_buffers[1] = single_session_body_text_buffer_from_lines(
                &mut action_font_system,
                &action_body_lines[action_window_start..action_window_end],
                size,
                action_app.text_scale(),
            );
        }
        let can_reuse_body_buffer = old_buffers.len() > 1;
        action_buffers = single_session_text_buffers_from_key_reusing_unchanged(
            &key,
            previous_key.as_ref(),
            old_buffers,
            can_reuse_body_buffer,
            size,
            &mut action_font_system,
        );
        if let Some(body_buffer) = action_buffers.get_mut(1) {
            body_buffer.set_scroll(
                viewport
                    .start_line
                    .saturating_sub(action_window_start)
                    .min(i32::MAX as usize) as i32,
            );
        }
        action_previous_key = Some(key);
        action_text_cache_ms += phase_started.elapsed().as_secs_f64() * 1000.0;

        let phase_started = Instant::now();
        let areas = single_session_text_areas_for_app_with_cached_body_viewport(
            &action_app,
            &action_buffers,
            size,
            0.0,
            viewport,
        );
        action_areas_ms += phase_started.elapsed().as_secs_f64() * 1000.0;

        let phase_started = Instant::now();
        let vertices = build_single_session_vertices_with_cached_body(
            &action_app,
            size,
            0.0,
            tick,
            0.0,
            1.0,
            &action_body_lines,
        );
        action_vertices_ms += phase_started.elapsed().as_secs_f64() * 1000.0;

        action_body_lines.len() ^ action_buffers.len() ^ areas.len() ^ vertices.len()
    });

    let mut workspace_app = Workspace::from_session_cards(benchmark_workspace_session_cards(128));
    let mut workspace_layout_ms = 0.0;
    let mut workspace_vertices_ms = 0.0;
    let mut workspace_visible_surfaces = 0usize;
    let mut workspace_navigation_font_system = benchmark_font_system();
    let mut workspace_navigation_text_pane_cache = HashMap::new();
    let (workspace_navigation_ms, workspace_navigation_checksum) =
        benchmark_phase(frames, |frame| {
            let key = if frame % 2 == 0 { "l" } else { "h" };
            let _ = workspace_app.handle_key(KeyInput::Character(key.to_string()));
            let phase_started = Instant::now();
            let layout = workspace_render_layout(&workspace_app, size, Some(size));
            workspace_layout_ms += phase_started.elapsed().as_secs_f64() * 1000.0;
            let panes = build_workspace_single_session_text_panes(
                &mut workspace_navigation_text_pane_cache,
                &workspace_app,
                size,
                layout,
                None,
                &mut workspace_navigation_font_system,
            );
            let pane_count = panes.len();
            drop(panes);
            let phase_started = Instant::now();
            let mut vertices = Vec::with_capacity(workspace_vertex_capacity_hint(&workspace_app));
            build_vertices_into(
                WorkspaceVertexBuildParams {
                    workspace: &workspace_app,
                    size,
                    render_layout: layout,
                    focus_pulse: 0.0,
                    space_hold_progress: None,
                    surface_frames: None,
                    exiting_surfaces: &HashMap::new(),
                    workspace_panel_cache: Some(&workspace_navigation_text_pane_cache),
                    status_color: workspace_status_bar_target_color(&workspace_app),
                    status_text_frame: None,
                },
                &mut vertices,
            );
            workspace_vertices_ms += phase_started.elapsed().as_secs_f64() * 1000.0;
            workspace_visible_surfaces +=
                workspace_visible_surface_count(&workspace_app, size, layout);
            vertices.len()
                ^ pane_count
                ^ (workspace_app.focused_id as usize)
                ^ workspace_app.surfaces.len()
        });

    let mut workspace_full_app =
        Workspace::from_session_cards(benchmark_workspace_session_cards(512));
    let mut workspace_full_layout_ms = 0.0;
    let mut workspace_full_vertices_ms = 0.0;
    let mut workspace_full_text_panes_ms = 0.0;
    let mut workspace_full_text_areas_ms = 0.0;
    let mut workspace_full_visible_surfaces = 0usize;
    let mut workspace_full_text_pane_count = 0usize;
    let mut workspace_full_text_area_count = 0usize;
    let mut workspace_full_font_system = benchmark_font_system();
    let mut workspace_full_text_pane_cache = HashMap::new();
    let (workspace_full_frame_ms, workspace_full_frame_checksum) =
        benchmark_phase(frames, |frame| {
            let key = match frame % 4 {
                0 | 1 => "j",
                _ => "k",
            };
            let _ = workspace_full_app.handle_key(KeyInput::Character(key.to_string()));
            let phase_started = Instant::now();
            let layout = workspace_render_layout(&workspace_full_app, size, Some(size));
            workspace_full_layout_ms += phase_started.elapsed().as_secs_f64() * 1000.0;
            workspace_full_visible_surfaces +=
                workspace_visible_surface_count(&workspace_full_app, size, layout);
            let phase_started = Instant::now();
            let panes = build_workspace_single_session_text_panes(
                &mut workspace_full_text_pane_cache,
                &workspace_full_app,
                size,
                layout,
                None,
                &mut workspace_full_font_system,
            );
            workspace_full_text_panes_ms += phase_started.elapsed().as_secs_f64() * 1000.0;
            let pane_count = panes.len();
            workspace_full_text_pane_count += pane_count;
            let phase_started = Instant::now();
            let areas = workspace_single_session_text_areas(&panes);
            workspace_full_text_areas_ms += phase_started.elapsed().as_secs_f64() * 1000.0;
            let area_count = areas.len();
            workspace_full_text_area_count += area_count;
            drop(areas);
            drop(panes);
            let phase_started = Instant::now();
            let mut vertices =
                Vec::with_capacity(workspace_vertex_capacity_hint(&workspace_full_app));
            build_vertices_into(
                WorkspaceVertexBuildParams {
                    workspace: &workspace_full_app,
                    size,
                    render_layout: layout,
                    focus_pulse: 0.0,
                    space_hold_progress: None,
                    surface_frames: None,
                    exiting_surfaces: &HashMap::new(),
                    workspace_panel_cache: Some(&workspace_full_text_pane_cache),
                    status_color: workspace_status_bar_target_color(&workspace_full_app),
                    status_text_frame: None,
                },
                &mut vertices,
            );
            workspace_full_vertices_ms += phase_started.elapsed().as_secs_f64() * 1000.0;
            vertices.len() ^ pane_count ^ area_count ^ workspace_full_app.surfaces.len()
        });

    let mut large_app = desktop_large_transcript_benchmark_app();
    let large_body_started = Instant::now();
    let large_body_lines = single_session_rendered_body_lines_for_tick(&large_app, size, 0);
    if let Some(metrics) =
        single_session_body_scroll_metrics_for_total_lines(&large_app, size, large_body_lines.len())
    {
        large_app.body_scroll_lines = metrics.max_scroll_lines as f32 / 2.0;
    }
    let large_body_elapsed = large_body_started.elapsed();
    let (large_scroll_ms, large_scroll_checksum) = benchmark_phase(frames, |frame| {
        large_app.scroll_body_lines(if frame % 2 == 0 { 1 } else { -1 });
        let viewport =
            single_session_body_viewport_from_lines(&large_app, size, 0.0, &large_body_lines);
        let areas = single_session_text_areas_for_app_with_cached_body_viewport(
            &large_app,
            &visible_whole_line_buffers,
            size,
            0.0,
            viewport,
        );
        let vertices = build_single_session_vertices_with_cached_body(
            &large_app,
            size,
            0.0,
            frame as u64,
            0.0,
            1.0,
            &large_body_lines,
        );
        large_body_lines.len() ^ areas.len() ^ vertices.len()
    });
    let (large_cache_key_ms, large_cache_key_checksum) = benchmark_phase(frames, |frame| {
        let key = large_app.rendered_body_cache_key((size.width, size.height));
        (key as usize) ^ frame ^ large_app.messages.len()
    });

    let mut workspace_focus_app =
        Workspace::from_session_cards(benchmark_workspace_session_cards(128));
    let workspace_focus_layout = workspace_render_layout(&workspace_focus_app, size, Some(size));
    let mut workspace_focus_font_system = benchmark_font_system();
    let mut workspace_focus_text_pane_cache = HashMap::new();
    let _ = build_workspace_single_session_text_panes(
        &mut workspace_focus_text_pane_cache,
        &workspace_focus_app,
        size,
        workspace_focus_layout,
        None,
        &mut workspace_focus_font_system,
    );
    let (workspace_focus_pulse_ms, workspace_focus_pulse_checksum) =
        benchmark_phase(frames, |frame| {
            let key = match frame % 4 {
                0 => "l",
                1 => "j",
                2 => "h",
                _ => "k",
            };
            let _ = workspace_focus_app.handle_key(KeyInput::Character(key.to_string()));
            let layout = workspace_render_layout(&workspace_focus_app, size, Some(size));
            let focus_pulse = 0.35 + 0.45 * ((frame as f32) * 0.19).sin().abs();
            let mut vertices =
                Vec::with_capacity(workspace_vertex_capacity_hint(&workspace_focus_app));
            build_vertices_into(
                WorkspaceVertexBuildParams {
                    workspace: &workspace_focus_app,
                    size,
                    render_layout: layout,
                    focus_pulse,
                    space_hold_progress: None,
                    surface_frames: None,
                    exiting_surfaces: &HashMap::new(),
                    workspace_panel_cache: Some(&workspace_focus_text_pane_cache),
                    status_color: workspace_status_bar_target_color(&workspace_focus_app),
                    status_text_frame: None,
                },
                &mut vertices,
            );
            vertices.len() ^ workspace_focus_app.focused_id as usize
        });

    let mut workspace_transition_app =
        Workspace::from_session_cards(benchmark_workspace_session_cards(128));
    let mut workspace_transition_animator = SurfaceTransitionAnimator::default();
    let mut workspace_transition_exit_cache = HashMap::new();
    let transition_base_layout =
        workspace_render_layout(&workspace_transition_app, size, Some(size));
    let transition_targets = workspace_surface_transition_targets(
        &workspace_transition_app,
        size,
        transition_base_layout,
    );
    let transition_start = Instant::now();
    let _ = workspace_transition_animator.frame(transition_targets, transition_start);
    let mut workspace_transition_font_system = benchmark_font_system();
    let mut workspace_transition_text_pane_cache = HashMap::new();
    let _ = build_workspace_single_session_text_panes(
        &mut workspace_transition_text_pane_cache,
        &workspace_transition_app,
        size,
        transition_base_layout,
        None,
        &mut workspace_transition_font_system,
    );
    let (workspace_surface_transition_ms, workspace_surface_transition_checksum) =
        benchmark_phase(frames, |frame| {
            if frame == 0 || frame % 20 == 0 {
                let _ = workspace_transition_app.handle_key(KeyInput::Character("l".to_string()));
            } else if frame % 20 == 10 {
                let _ = workspace_transition_app.handle_key(KeyInput::Character("h".to_string()));
            }
            let layout = workspace_render_layout(&workspace_transition_app, size, Some(size));
            let targets =
                workspace_surface_transition_targets(&workspace_transition_app, size, layout);
            let now = transition_start + Duration::from_millis((frame as u64 + 1) * 8);
            let surface_frames = WorkspaceSurfaceTransitionFrames::new(
                workspace_transition_animator.frame(targets, now),
                workspace_transition_animator.is_animating(),
            );
            update_workspace_surface_exit_cache(
                &mut workspace_transition_exit_cache,
                &workspace_transition_app,
                &surface_frames,
            );
            let mut vertices =
                Vec::with_capacity(workspace_vertex_capacity_hint(&workspace_transition_app));
            build_vertices_into(
                WorkspaceVertexBuildParams {
                    workspace: &workspace_transition_app,
                    size,
                    render_layout: layout,
                    focus_pulse: 0.0,
                    space_hold_progress: None,
                    surface_frames: Some(&surface_frames),
                    exiting_surfaces: &workspace_transition_exit_cache,
                    workspace_panel_cache: Some(&workspace_transition_text_pane_cache),
                    status_color: workspace_status_bar_target_color(&workspace_transition_app),
                    status_text_frame: None,
                },
                &mut vertices,
            );
            vertices.len() ^ surface_frames.frames.len() ^ usize::from(surface_frames.animating)
        });

    let mut selection_drag_app = desktop_scroll_benchmark_app_with_turns(120);
    let selection_body_lines =
        single_session_rendered_body_lines_for_tick(&selection_drag_app, size, 0);
    if let Some(metrics) = single_session_body_scroll_metrics_for_total_lines(
        &selection_drag_app,
        size,
        selection_body_lines.len(),
    ) {
        selection_drag_app.body_scroll_lines = metrics.max_scroll_lines as f32 / 2.0;
    }
    selection_drag_app.begin_selection(SelectionPoint { line: 0, column: 0 });
    let (selection_drag_ms, selection_drag_checksum) = benchmark_phase(frames, |frame| {
        let viewport = single_session_body_viewport_from_lines(
            &selection_drag_app,
            size,
            0.0,
            &selection_body_lines,
        );
        let line = (frame * 3).min(viewport.lines.len().saturating_sub(1));
        selection_drag_app.update_selection(SelectionPoint {
            line,
            column: viewport
                .lines
                .get(line)
                .map(|line| line.text.len())
                .unwrap_or_default(),
        });
        let vertices = build_single_session_vertices_with_cached_body(
            &selection_drag_app,
            size,
            0.0,
            frame as u64,
            0.0,
            1.0,
            &selection_body_lines,
        );
        viewport.lines.len() ^ vertices.len()
    });

    let mut streaming_scroll_app = app.clone();
    streaming_scroll_app.streaming_response.clear();
    let mut streaming_scroll_body_lines =
        single_session_rendered_body_lines_for_tick(&streaming_scroll_app, size, 0);
    let (streaming_while_scrolling_ms, streaming_while_scrolling_checksum) =
        benchmark_phase(frames, |frame| {
            streaming_scroll_app
                .streaming_response
                .push(benchmark_typing_char(frame));
            if frame % 13 == 0 {
                streaming_scroll_app.streaming_response.push('\n');
            }
            streaming_scroll_app.scroll_body_lines(if frame % 2 == 0 { 1 } else { -1 });
            streaming_scroll_body_lines = single_session_rendered_body_lines_for_tick(
                &streaming_scroll_app,
                size,
                frame as u64,
            );
            let viewport = single_session_body_viewport_from_lines(
                &streaming_scroll_app,
                size,
                benchmark_smooth_scroll_lines(frame),
                &streaming_scroll_body_lines,
            );
            let vertices = build_single_session_vertices_with_cached_body(
                &streaming_scroll_app,
                size,
                0.0,
                frame as u64,
                benchmark_smooth_scroll_lines(frame),
                1.0,
                &streaming_scroll_body_lines,
            );
            streaming_scroll_body_lines.len() ^ viewport.lines.len() ^ vertices.len()
        });

    let mut many_tools_app = desktop_scroll_benchmark_app_with_turns(16);
    for index in 0..320usize {
        let id = Some(format!("tool-{index}"));
        many_tools_app.apply_session_event(session_launch::DesktopSessionEvent::ToolStarted {
            id: id.clone(),
            name: "bash".to_string(),
        });
        many_tools_app.apply_session_event(session_launch::DesktopSessionEvent::ToolInput {
            id: id.clone(),
            delta: format!("echo card {index}\n"),
        });
        many_tools_app.apply_session_event(session_launch::DesktopSessionEvent::ToolExecuting {
            id: id.clone(),
            name: "bash".to_string(),
        });
        many_tools_app.apply_session_event(session_launch::DesktopSessionEvent::ToolFinished {
            id,
            name: "bash".to_string(),
            summary: format!("output line for tool card {index}\n"),
            is_error: false,
        });
    }
    let many_tools_body_lines =
        single_session_rendered_body_lines_for_tick(&many_tools_app, size, 0);
    if let Some(metrics) = single_session_body_scroll_metrics_for_total_lines(
        &many_tools_app,
        size,
        many_tools_body_lines.len(),
    ) {
        many_tools_app.body_scroll_lines = metrics.max_scroll_lines as f32 / 2.0;
    }
    let (many_tool_cards_ms, many_tool_cards_checksum) = benchmark_phase(frames, |frame| {
        many_tools_app.scroll_body_lines(if frame % 2 == 0 { 4 } else { -4 });
        let viewport = single_session_body_viewport_from_lines(
            &many_tools_app,
            size,
            0.0,
            &many_tools_body_lines,
        );
        let vertices = build_single_session_vertices_with_cached_body(
            &many_tools_app,
            size,
            0.0,
            frame as u64,
            0.0,
            1.0,
            &many_tools_body_lines,
        );
        many_tools_body_lines.len() ^ viewport.lines.len() ^ vertices.len()
    });

    let mut large_composer_app = app.clone();
    large_composer_app.scroll_body_to_bottom();
    large_composer_app.draft = (0..40).map(|index| format!("large pasted paragraph {index}: lorem ipsum dolor sit amet, consectetur adipiscing elit. ")).collect::<String>();
    large_composer_app.draft_cursor = large_composer_app.draft.len();
    let large_composer_body_lines =
        single_session_rendered_body_lines_for_tick(&large_composer_app, size, 0);
    let mut large_composer_font_system = benchmark_font_system();
    let mut large_composer_previous_key = None;
    let mut large_composer_buffers: Vec<Buffer> = Vec::new();
    let (large_composer_draft_ms, large_composer_draft_checksum) =
        benchmark_phase(frames, |frame| {
            large_composer_app.draft.push(benchmark_typing_char(frame));
            if frame % 41 == 0 {
                large_composer_app.draft.push('\n');
            }
            large_composer_app.draft_cursor = large_composer_app.draft.len();
            let key = single_session_text_key_for_tick_with_rendered_body(
                &large_composer_app,
                size,
                frame as u64,
                0.0,
                &large_composer_body_lines,
            );
            let previous_key = large_composer_previous_key.take();
            let old_buffers = std::mem::take(&mut large_composer_buffers);
            large_composer_buffers = single_session_text_buffers_from_key_reusing_unchanged(
                &key,
                previous_key.as_ref(),
                old_buffers,
                true,
                size,
                &mut large_composer_font_system,
            );
            large_composer_previous_key = Some(key);
            let areas = single_session_text_areas_for_app_with_cached_body(
                &large_composer_app,
                &large_composer_buffers,
                size,
                0.0,
                &large_composer_body_lines,
            );
            let vertices = build_single_session_vertices_with_cached_body(
                &large_composer_app,
                size,
                0.0,
                frame as u64,
                0.0,
                1.0,
                &large_composer_body_lines,
            );
            large_composer_app.draft.len()
                ^ large_composer_buffers.len()
                ^ areas.len()
                ^ vertices.len()
        });

    let target_budget_ms = duration_ms(DESKTOP_120FPS_FRAME_BUDGET);
    let critical_phase_means_ms = [
        visible_whole_line_text_ms / frames as f64,
        typing_redraw_ms / frames as f64,
        fresh_typing_ms / frames as f64,
        streaming_delta_ms / frames as f64,
        long_streaming_body_wrap_ms / frames as f64,
        long_streaming_line_count_ms / frames as f64,
        long_unbroken_streaming_wrap_ms / frames as f64,
        event_batch_ms / frames as f64,
        event_forwarder_flood_ms / frames as f64,
        hero_boundary_scroll_ms / frames as f64,
        action_input_ms / frames as f64,
        action_visible_ms / frames as f64,
        workspace_navigation_ms / frames as f64,
        workspace_full_frame_ms / frames as f64,
        workspace_focus_pulse_ms / frames as f64,
        workspace_surface_transition_ms / frames as f64,
        streaming_while_scrolling_ms / frames as f64,
        many_tool_cards_ms / frames as f64,
        large_scroll_ms / frames as f64,
        large_cache_key_ms / frames as f64,
    ];
    let passes_interaction_cpu_budget = critical_phase_means_ms
        .iter()
        .all(|mean_ms| *mean_ms <= target_budget_ms);
    let metrics = single_session_body_scroll_metrics(&app, size, 0);
    println!(
        "{}",
        serde_json::to_string_pretty(&serde_json::json!({
            "frames": frames,
            "target_frame_budget_ms": target_budget_ms,
            "passes_120fps_scroll_cpu_budget": (visible_whole_line_text_ms / frames as f64)
                <= target_budget_ms,
            "passes_120fps_interaction_cpu_budget": passes_interaction_cpu_budget,
            "passes_no_paint_watchdog_budget": end_to_end_stream_flood.passes_no_paint_budget(),
            "passes_streaming_incremental_wrap_guard": streaming_static_base_rebuilds <= 1,
            "passes_long_streaming_body_wrap_budget": (long_streaming_body_wrap_ms / frames as f64) <= target_budget_ms,
            "passes_long_streaming_line_count_budget": (long_streaming_line_count_ms / frames as f64) <= target_budget_ms,
            "passes_long_unbroken_streaming_wrap_budget": (long_unbroken_streaming_wrap_ms / frames as f64) <= target_budget_ms,
            "passes_workspace_focus_pulse_budget": (workspace_focus_pulse_ms / frames as f64) <= target_budget_ms,
            "passes_workspace_surface_transition_budget": (workspace_surface_transition_ms / frames as f64) <= target_budget_ms,
            "passes_selection_drag_budget": (selection_drag_ms / frames as f64) <= 16.0,
            "passes_streaming_while_scrolling_budget": (streaming_while_scrolling_ms / frames as f64) <= target_budget_ms,
            "passes_many_tool_cards_budget": (many_tool_cards_ms / frames as f64) <= target_budget_ms,
            "passes_large_composer_draft_budget": (large_composer_draft_ms / frames as f64) <= 16.0,
            "event_delivery": {
                "previous_background_poll_interval_ms": duration_ms(BACKGROUND_POLL_INTERVAL),
                "backend_redraw_frame_interval_ms": duration_ms(BACKEND_REDRAW_FRAME_INTERVAL),
                "backend_event_forward_interval_ms": duration_ms(BACKEND_EVENT_FORWARD_INTERVAL),
                "backend_event_forward_max_raw_events": BACKEND_EVENT_FORWARD_MAX_RAW_EVENTS,
                "backend_event_forward_max_payload_bytes": BACKEND_EVENT_FORWARD_MAX_PAYLOAD_BYTES,
                "backend_events_wake_event_loop": true,
                "coalesces_consecutive_text_delta_events": true,
                "bounded_stream_flood_forwarding": true,
            },
            "no_paint_watchdog": {
                "budget_ms": duration_ms(DESKTOP_NO_PAINT_BUDGET),
                "log_event": "jcode-desktop-no-paint-profile",
                "requests_recovery_redraw": true,
            },
            "end_to_end_stream_flood": end_to_end_stream_flood.to_json(),
            "size": { "width": size.width, "height": size.height },
            "messages": app.messages.len(),
            "scroll_metrics": metrics.map(|metrics| serde_json::json!({
                "total_lines": metrics.total_lines,
                "visible_lines": metrics.visible_lines,
                "max_scroll_lines": metrics.max_scroll_lines,
                "start_scroll_lines": app.body_scroll_lines,
            })),
            "setup": benchmark_phase_json(
                "setup_text_and_geometry",
                setup_elapsed.as_secs_f64() * 1000.0,
                1,
                setup_checksum,
            ),
            "phases": [
                benchmark_phase_json(
                    "legacy_smooth_scroll_text_relayout",
                    legacy_smooth_text_ms,
                    frames,
                    legacy_smooth_text_checksum,
                ),
                benchmark_phase_json(
                    "optimized_smooth_scroll_geometry_only",
                    optimized_smooth_geometry_ms,
                    frames,
                    optimized_smooth_geometry_checksum,
                ),
                benchmark_phase_json(
                    "legacy_whole_line_scroll_text_relayout",
                    whole_line_text_ms,
                    frames,
                    whole_line_text_checksum,
                ),
                benchmark_phase_json(
                    "optimized_whole_line_scroll_visible_body_only",
                    visible_whole_line_text_ms,
                    frames,
                    visible_whole_line_text_checksum,
                ),
                benchmark_phase_json(
                    "typing_redraw_reuse_body_cache",
                    typing_redraw_ms,
                    frames,
                    typing_redraw_checksum,
                ),
                benchmark_phase_json(
                    "fresh_welcome_typing_redraw",
                    fresh_typing_ms,
                    frames,
                    fresh_typing_checksum,
                ),
                benchmark_phase_json(
                    "streaming_delta_redraw",
                    streaming_delta_ms,
                    frames,
                    streaming_delta_checksum,
                ),
                benchmark_phase_json(
                    "long_streaming_response_body_wrap",
                    long_streaming_body_wrap_ms,
                    frames,
                    long_streaming_body_wrap_checksum,
                ),
                benchmark_phase_json(
                    "long_streaming_response_line_count",
                    long_streaming_line_count_ms,
                    frames,
                    long_streaming_line_count_checksum,
                ),
                benchmark_phase_json(
                    "long_unbroken_streaming_line_wrap",
                    long_unbroken_streaming_wrap_ms,
                    frames,
                    long_unbroken_streaming_wrap_checksum,
                ),
                benchmark_phase_json(
                    "background_event_batch_coalesce_apply",
                    event_batch_ms,
                    frames,
                    event_batch_checksum,
                ),
                benchmark_phase_json(
                    "background_event_forwarder_flood_collect",
                    event_forwarder_flood_ms,
                    frames,
                    event_forwarder_flood_checksum,
                ),
                benchmark_phase_json(
                    "hero_boundary_scroll_redraw",
                    hero_boundary_scroll_ms,
                    frames,
                    hero_boundary_checksum,
                ),
                benchmark_phase_json(
                    "action_tool_input_batch_no_redraw",
                    action_input_ms,
                    frames,
                    action_input_checksum,
                ),
                benchmark_phase_json(
                    "action_tool_visible_redraw",
                    action_visible_ms,
                    frames,
                    action_visible_checksum,
                ),
                benchmark_phase_json(
                    "workspace_navigation_geometry",
                    workspace_navigation_ms,
                    frames,
                    workspace_navigation_checksum,
                ),
                benchmark_phase_json(
                    "workspace_full_frame_scroll_attributed",
                    workspace_full_frame_ms,
                    frames,
                    workspace_full_frame_checksum,
                ),
                benchmark_phase_json(
                    "workspace_focus_pulse_animation",
                    workspace_focus_pulse_ms,
                    frames,
                    workspace_focus_pulse_checksum,
                ),
                benchmark_phase_json(
                    "workspace_surface_enter_exit_transition",
                    workspace_surface_transition_ms,
                    frames,
                    workspace_surface_transition_checksum,
                ),
                benchmark_phase_json(
                    "selection_drag_large_transcript",
                    selection_drag_ms,
                    frames,
                    selection_drag_checksum,
                ),
                benchmark_phase_json(
                    "streaming_while_scrolling",
                    streaming_while_scrolling_ms,
                    frames,
                    streaming_while_scrolling_checksum,
                ),
                benchmark_phase_json(
                    "many_tool_cards_scroll",
                    many_tool_cards_ms,
                    frames,
                    many_tool_cards_checksum,
                ),
                benchmark_phase_json(
                    "large_composer_draft_edit",
                    large_composer_draft_ms,
                    frames,
                    large_composer_draft_checksum,
                ),
                benchmark_phase_json(
                    "large_transcript_scroll_visible_body_only",
                    large_scroll_ms,
                    frames,
                    large_scroll_checksum,
                ),
                benchmark_phase_json(
                    "large_transcript_cache_key",
                    large_cache_key_ms,
                    frames,
                    large_cache_key_checksum,
                ),
            ],
            "workspace_navigation_subphases": {
                "visible_surface_count_mean": workspace_visible_surfaces as f64 / frames as f64,
                "layout": benchmark_phase_json("workspace_layout", workspace_layout_ms, frames, 0),
                "vertices": benchmark_phase_json("workspace_vertices", workspace_vertices_ms, frames, 0),
            },
            "workspace_full_frame_subphases": {
                "surfaces_total": workspace_full_app.surfaces.len(),
                "visible_surface_count_mean": workspace_full_visible_surfaces as f64 / frames as f64,
                "text_pane_count_mean": workspace_full_text_pane_count as f64 / frames as f64,
                "text_area_count_mean": workspace_full_text_area_count as f64 / frames as f64,
                "layout": benchmark_phase_json("workspace_full_layout", workspace_full_layout_ms, frames, 0),
                "vertices": benchmark_phase_json("workspace_full_vertices", workspace_full_vertices_ms, frames, 0),
                "text_panes": benchmark_phase_json("workspace_full_text_panes", workspace_full_text_panes_ms, frames, 0),
                "text_areas": benchmark_phase_json("workspace_full_text_areas", workspace_full_text_areas_ms, frames, 0),
            },
            "visible_whole_line_subphases": [
                benchmark_phase_json("viewport", visible_viewport_ms, frames, 0),
                benchmark_phase_json("window", visible_window_ms, frames, 0),
                benchmark_phase_json("set_scroll", visible_scroll_ms, frames, 0),
                benchmark_phase_json("glyph_runs", visible_glyph_ms, frames, 0),
                benchmark_phase_json("areas", visible_areas_ms, frames, 0),
                benchmark_phase_json("vertices", visible_vertices_ms, frames, 0),
            ],
            "cold_start_cpu": [
                benchmark_phase_json("fresh_welcome_cold_text_frame", cold_fresh_ms, 1, cold_fresh_checksum),
                benchmark_phase_json("fresh_welcome_prewarmed_text_frame", prewarmed_fresh_ms, 1, prewarmed_fresh_checksum),
                benchmark_phase_json("fresh_welcome_warm_cached_text_frame", warm_fresh_ms, 1, warm_fresh_checksum),
            ],
            "cold_start_subphases": [
                benchmark_phase_json("font_system", cold_fresh_font_ms, 1, 0),
                benchmark_phase_json("text_key", cold_fresh_key_ms, 1, 0),
                benchmark_phase_json("text_buffers", cold_fresh_buffers_ms, 1, 0),
                benchmark_phase_json("text_areas", cold_fresh_areas_ms, 1, 0),
                benchmark_phase_json("vertices", cold_fresh_vertices_ms, 1, 0),
            ],
            "typing_redraw_subphases": [
                benchmark_phase_json("text_cache", typing_text_cache_ms, frames, 0),
                benchmark_phase_json("areas", typing_areas_ms, frames, 0),
                benchmark_phase_json("vertices", typing_vertices_ms, frames, 0),
            ],
            "fresh_welcome_typing_subphases": [
                benchmark_phase_json("text_cache", fresh_typing_text_cache_ms, frames, 0),
                benchmark_phase_json("areas", fresh_typing_areas_ms, frames, 0),
                benchmark_phase_json("vertices", fresh_typing_vertices_ms, frames, 0),
            ],
            "streaming_delta_subphases": [
                benchmark_phase_json("body_wrap", streaming_body_ms, frames, 0),
                benchmark_phase_json("text_cache", streaming_text_cache_ms, frames, 0),
                benchmark_phase_json("areas", streaming_areas_ms, frames, 0),
                benchmark_phase_json("vertices", streaming_vertices_ms, frames, 0),
            ],
            "streaming_incremental_wrap": {
                "static_base_rebuilds": streaming_static_base_rebuilds,
                "tail_text_buffer_rebuilds": streaming_tail_text_buffer_rebuilds,
                "static_base_rebuild_budget": 1,
                "passes_static_base_rebuild_budget": streaming_static_base_rebuilds <= 1,
            },
            "hero_boundary": {
                "start_scroll_lines": hero_boundary_scroll,
                "body_buffer_rebuilds": hero_body_buffer_rebuilds,
                "subphases": [
                    benchmark_phase_json("text_cache", hero_text_cache_ms, frames, 0),
                    benchmark_phase_json("viewport_and_key", hero_viewport_key_ms, frames, 0),
                    benchmark_phase_json("body_window_rebuild", hero_window_rebuild_ms, frames, hero_body_buffer_rebuilds),
                    benchmark_phase_json("reuse_text_buffers", hero_buffer_reuse_ms, frames, 0),
                    benchmark_phase_json("areas", hero_areas_ms, frames, 0),
                    benchmark_phase_json("vertices", hero_vertices_ms, frames, 0),
                ],
            },
            "action_tool_visible_subphases": [
                benchmark_phase_json("event_apply_body_mutation", action_apply_ms, frames, 0),
                benchmark_phase_json("body_wrap", action_body_ms, frames, 0),
                benchmark_phase_json("text_cache", action_text_cache_ms, frames, 0),
                benchmark_phase_json("areas", action_areas_ms, frames, 0),
                benchmark_phase_json("vertices", action_vertices_ms, frames, 0),
            ],
            "large_transcript_setup": benchmark_phase_json(
                "large_transcript_initial_body_wrap",
                large_body_elapsed.as_secs_f64() * 1000.0,
                1,
                large_body_lines.len(),
            ),
        }))?
    );
    Ok(())
}
