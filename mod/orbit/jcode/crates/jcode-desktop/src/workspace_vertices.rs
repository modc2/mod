use super::*;

pub(crate) fn reserve_workspace_vertex_capacity(vertices: &mut Vec<Vertex>, workspace: &Workspace) {
    let hint = workspace_vertex_capacity_hint(workspace);
    if vertices.capacity() < hint {
        vertices.reserve(hint - vertices.capacity());
    }
}

pub(crate) fn workspace_vertex_capacity_hint(workspace: &Workspace) -> usize {
    WORKSPACE_BASE_VERTEX_CAPACITY_HINT
        + workspace
            .surfaces
            .len()
            .saturating_mul(WORKSPACE_SURFACE_VERTEX_CAPACITY_HINT)
}

pub(crate) struct WorkspaceSingleSessionTextPane<'a> {
    app: &'a SingleSessionApp,
    rect: Rect,
    size: PhysicalSize<u32>,
    rendered_body_lines: &'a [SingleSessionStyledLine],
    buffers: &'a [Buffer],
}

pub(crate) struct CachedWorkspaceSingleSessionTextPane {
    identity_key: u64,
    text_key: SingleSessionTextKey,
    app: SingleSessionApp,
    rect: Rect,
    size: PhysicalSize<u32>,
    rendered_body_lines: Vec<SingleSessionStyledLine>,
    buffers: Vec<Buffer>,
    child_vertices: Vec<Vertex>,
}

pub(crate) struct WorkspaceSurfaceTransitionFrames {
    pub(crate) frames: Vec<SurfaceVisualFrame>,
    pub(crate) animating: bool,
}

impl WorkspaceSurfaceTransitionFrames {
    pub(crate) fn new(frames: Vec<SurfaceVisualFrame>, animating: bool) -> Self {
        Self { frames, animating }
    }

    pub(crate) fn frame_for_surface(&self, surface_id: u64) -> Option<SurfaceVisualFrame> {
        self.frames
            .iter()
            .copied()
            .find(|frame| frame.id == surface_id)
    }

    pub(crate) fn exiting_frames(&self) -> impl Iterator<Item = SurfaceVisualFrame> + '_ {
        self.frames.iter().copied().filter(|frame| frame.exiting)
    }
}

pub(crate) fn update_workspace_surface_exit_cache(
    cache: &mut HashMap<u64, workspace::Surface>,
    workspace: &Workspace,
    surface_frames: &WorkspaceSurfaceTransitionFrames,
) {
    for surface in &workspace.surfaces {
        cache.insert(surface.id, surface.clone());
    }

    cache.retain(|surface_id, _| {
        workspace
            .surfaces
            .iter()
            .any(|surface| surface.id == *surface_id)
            || surface_frames.frame_for_surface(*surface_id).is_some()
    });
}

pub(crate) fn workspace_transitioned_surface_rect(
    frames: Option<&WorkspaceSurfaceTransitionFrames>,
    surface_id: u64,
    fallback: Rect,
) -> Rect {
    frames
        .and_then(|frames| frames.frame_for_surface(surface_id))
        .map(|frame| rect_from_animated_rect(frame.visual_rect()))
        .unwrap_or(fallback)
}

pub(crate) fn workspace_transitioned_surface_opacity(
    frames: Option<&WorkspaceSurfaceTransitionFrames>,
    surface_id: u64,
) -> f32 {
    frames
        .and_then(|frames| frames.frame_for_surface(surface_id))
        .map(|frame| frame.opacity)
        .unwrap_or(1.0)
}

pub(crate) fn animated_rect_from_rect(rect: Rect) -> AnimatedRect {
    AnimatedRect {
        x: rect.x,
        y: rect.y,
        width: rect.width,
        height: rect.height,
    }
}

pub(crate) fn rect_from_animated_rect(rect: AnimatedRect) -> Rect {
    Rect {
        x: rect.x,
        y: rect.y,
        width: rect.width,
        height: rect.height,
    }
}

pub(crate) fn workspace_surface_transition_targets(
    workspace: &Workspace,
    size: PhysicalSize<u32>,
    render_layout: WorkspaceRenderLayout,
) -> Vec<SurfaceVisualTarget> {
    let mut targets = Vec::new();
    if workspace.zoomed {
        if let Some(surface) = workspace.focused_surface() {
            targets.push(SurfaceVisualTarget {
                id: surface.id,
                rect: animated_rect_from_rect(Rect {
                    x: OUTER_PADDING,
                    y: STATUS_BAR_HEIGHT + OUTER_PADDING * 2.0,
                    width: (size.width as f32 - OUTER_PADDING * 2.0).max(1.0),
                    height: (size.height as f32 - STATUS_BAR_HEIGHT - OUTER_PADDING * 3.0).max(1.0),
                }),
            });
        }
        return targets;
    }

    for_each_visible_workspace_surface(
        workspace,
        size,
        render_layout,
        0.0,
        |surface, rect, _, _| {
            targets.push(SurfaceVisualTarget {
                id: surface.id,
                rect: animated_rect_from_rect(rect),
            });
        },
    );
    targets
}

pub(crate) fn workspace_panel_size(rect: Rect) -> PhysicalSize<u32> {
    PhysicalSize::new(
        rect.width.round().max(1.0) as u32,
        rect.height.round().max(1.0) as u32,
    )
}

/// Identity key for the fully-assembled workspace primitive vertex buffer.
///
/// This is only meaningful (and only consulted) when no workspace animation is
/// active, in which case the viewport layout, focus pulse, surface transitions
/// and status transitions are all settled and therefore pure functions of the
/// workspace content state. Hashing the inputs lets idle frames reuse the
/// previously assembled vertex buffer instead of re-transforming ~100k vertices
/// every redraw.
#[allow(clippy::too_many_arguments)]
pub(crate) fn workspace_primitive_vertices_cache_key(
    workspace: &Workspace,
    size: PhysicalSize<u32>,
    render_layout: WorkspaceRenderLayout,
    focus_pulse: f32,
    space_hold_progress: Option<f32>,
    status_color: [f32; 4],
    status_text_frame: Option<&StatusTextTransitionFrame>,
    panel_cache: &HashMap<u64, CachedWorkspaceSingleSessionTextPane>,
) -> u64 {
    let mut hasher = DefaultHasher::new();
    size.width.hash(&mut hasher);
    size.height.hash(&mut hasher);
    render_layout.column_width.to_bits().hash(&mut hasher);
    render_layout.scroll_offset.to_bits().hash(&mut hasher);
    render_layout
        .vertical_scroll_offset
        .to_bits()
        .hash(&mut hasher);
    focus_pulse.to_bits().hash(&mut hasher);
    space_hold_progress.map(f32::to_bits).hash(&mut hasher);
    for channel in status_color {
        channel.to_bits().hash(&mut hasher);
    }
    if let Some(frame) = status_text_frame {
        frame.current.text.hash(&mut hasher);
        frame.current.opacity.to_bits().hash(&mut hasher);
        frame.current.y_offset_pixels.to_bits().hash(&mut hasher);
        if let Some(previous) = &frame.previous {
            previous.text.hash(&mut hasher);
            previous.opacity.to_bits().hash(&mut hasher);
            previous.y_offset_pixels.to_bits().hash(&mut hasher);
        } else {
            0u8.hash(&mut hasher);
        }
    }
    workspace.zoomed.hash(&mut hasher);
    (workspace.mode == InputMode::Insert).hash(&mut hasher);
    workspace.focused_id.hash(&mut hasher);
    workspace.detail_scroll.hash(&mut hasher);
    workspace.current_workspace().hash(&mut hasher);
    for surface in &workspace.surfaces {
        surface.id.hash(&mut hasher);
        workspace_surface_kind_key(surface.kind).hash(&mut hasher);
        surface.lane.hash(&mut hasher);
        surface.column.hash(&mut hasher);
        surface.color_index.hash(&mut hasher);
        surface.title.hash(&mut hasher);
        surface.body_lines.hash(&mut hasher);
        surface.detail_lines.hash(&mut hasher);
        surface.session_id.hash(&mut hasher);
        // Cached panel vertex generation differentiates rendered transcript
        // content for session surfaces.
        if let Some(entry) = panel_cache.get(&surface.id) {
            entry.identity_key.hash(&mut hasher);
        }
    }
    if workspace.mode == InputMode::Insert {
        workspace.draft.hash(&mut hasher);
        workspace.draft_cursor.hash(&mut hasher);
        workspace.pending_images.len().hash(&mut hasher);
    }
    hasher.finish()
}

pub(crate) fn workspace_single_session_app_for_surface(
    workspace: &Workspace,
    surface: &workspace::Surface,
) -> Option<SingleSessionApp> {
    let card = surface.session_card()?;
    let session_id = card.session_id.clone();
    let mut app = SingleSessionApp::new(Some(card));
    app.live_session_id = Some(session_id);

    if workspace.mode == InputMode::Insert && workspace.is_focused(surface.id) {
        app.draft = workspace.draft.clone();
        app.draft_cursor = workspace.draft_cursor.min(app.draft.len());
        app.pending_images = workspace.pending_images.clone();
    }

    Some(app)
}

pub(crate) fn push_workspace_single_session_panel(
    vertices: &mut Vec<Vertex>,
    app: &SingleSessionApp,
    rect: Rect,
    parent_size: PhysicalSize<u32>,
    focus_pulse: f32,
    opacity: f32,
) {
    let panel_size = workspace_panel_size(rect);
    let rendered_body_lines = single_session_rendered_body_lines_for_tick(app, panel_size, 0);
    let child_vertices = build_single_session_vertices_with_cached_body(
        app,
        panel_size,
        focus_pulse,
        0,
        0.0,
        1.0,
        &rendered_body_lines,
    );
    append_child_vertices_to_parent_with_opacity(
        vertices,
        &child_vertices,
        panel_size,
        rect,
        parent_size,
        opacity,
    );
}

pub(crate) fn append_child_vertices_to_parent_with_opacity(
    vertices: &mut Vec<Vertex>,
    child_vertices: &[Vertex],
    child_size: PhysicalSize<u32>,
    rect: Rect,
    parent_size: PhysicalSize<u32>,
    opacity: f32,
) {
    let opacity = opacity.clamp(0.0, 1.0);
    let child_width = child_size.width.max(1) as f32;
    let child_height = child_size.height.max(1) as f32;
    vertices.extend(child_vertices.iter().map(|vertex| {
        let child_x = (vertex.position[0] + 1.0) * 0.5 * child_width;
        let child_y = (1.0 - vertex.position[1]) * 0.5 * child_height;
        let mut color = vertex.color;
        color[3] *= opacity;
        Vertex {
            position: pixel_to_ndc([rect.x + child_x, rect.y + child_y], parent_size),
            color,
        }
    }));
}

pub(crate) fn for_each_visible_workspace_surface(
    workspace: &Workspace,
    size: PhysicalSize<u32>,
    render_layout: WorkspaceRenderLayout,
    focus_pulse: f32,
    mut visit: impl FnMut(&workspace::Surface, Rect, bool, f32),
) {
    let width = size.width as f32;
    let height = size.height as f32;
    let workspace_height = (height - STATUS_BAR_HEIGHT - OUTER_PADDING * 3.0).max(1.0);
    let workspace_top = STATUS_BAR_HEIGHT + OUTER_PADDING * 2.0;
    let lane_pitch = workspace_height + GAP;
    let column_width = render_layout.column_width;
    let scroll_offset = render_layout.scroll_offset;
    let vertical_scroll_offset = render_layout.vertical_scroll_offset;
    let viewport_left = OUTER_PADDING - GAP;
    let viewport_right = width - OUTER_PADDING + GAP;

    for surface in &workspace.surfaces {
        let column = surface.column as f32;
        let y = workspace_top + surface.lane as f32 * lane_pitch - vertical_scroll_offset;
        if y + workspace_height < workspace_top || y > workspace_top + workspace_height {
            continue;
        }
        let rect = Rect {
            x: OUTER_PADDING + column * (column_width + GAP) - scroll_offset,
            y,
            width: column_width,
            height: workspace_height,
        };
        if rect.x + rect.width < viewport_left || rect.x > viewport_right {
            continue;
        }
        let focused = workspace.is_focused(surface.id);
        let surface_pulse = if focused { focus_pulse } else { 0.0 };
        visit(surface, rect, focused, surface_pulse);
    }
}

pub(crate) fn workspace_visible_surface_count(
    workspace: &Workspace,
    size: PhysicalSize<u32>,
    render_layout: WorkspaceRenderLayout,
) -> usize {
    let mut count = 0;
    for_each_visible_workspace_surface(workspace, size, render_layout, 0.0, |_, _, _, _| {
        count += 1;
    });
    count
}

pub(crate) fn build_workspace_single_session_text_panes<'a>(
    cache: &'a mut HashMap<u64, CachedWorkspaceSingleSessionTextPane>,
    workspace: &Workspace,
    size: PhysicalSize<u32>,
    render_layout: WorkspaceRenderLayout,
    surface_frames: Option<&WorkspaceSurfaceTransitionFrames>,
    font_system: &mut FontSystem,
) -> Vec<WorkspaceSingleSessionTextPane<'a>> {
    let mut visible_surface_ids = Vec::new();
    if workspace.zoomed {
        if let Some(surface) = workspace.focused_surface()
            && surface.kind == workspace::SurfaceKind::Session
            && surface.session_id.is_some()
        {
            let target_rect = Rect {
                x: OUTER_PADDING,
                y: STATUS_BAR_HEIGHT + OUTER_PADDING * 2.0,
                width: (size.width as f32 - OUTER_PADDING * 2.0).max(1.0),
                height: (size.height as f32 - STATUS_BAR_HEIGHT - OUTER_PADDING * 3.0).max(1.0),
            };
            let rect = workspace_transitioned_surface_rect(surface_frames, surface.id, target_rect);
            let panel_size = workspace_panel_size(rect);
            if refresh_workspace_text_pane_cache_entry(
                cache,
                surface.id,
                workspace_surface_text_pane_identity_key(workspace, surface, panel_size),
                rect,
                panel_size,
                font_system,
                || workspace_single_session_app_for_surface(workspace, surface),
            ) {
                visible_surface_ids.push(surface.id);
            }
        }
        retain_workspace_text_pane_cache_for_workspace(cache, workspace);
        return workspace_text_panes_from_cache(cache, &visible_surface_ids);
    }

    for_each_visible_workspace_surface(
        workspace,
        size,
        render_layout,
        0.0,
        |surface, target_rect, _, _| {
            if surface.kind != workspace::SurfaceKind::Session {
                return;
            }
            if surface.session_id.is_none() {
                return;
            }
            let rect = workspace_transitioned_surface_rect(surface_frames, surface.id, target_rect);
            let panel_size = workspace_panel_size(rect);
            if refresh_workspace_text_pane_cache_entry(
                cache,
                surface.id,
                workspace_surface_text_pane_identity_key(workspace, surface, panel_size),
                rect,
                panel_size,
                font_system,
                || workspace_single_session_app_for_surface(workspace, surface),
            ) {
                visible_surface_ids.push(surface.id);
            }
        },
    );

    retain_workspace_text_pane_cache_for_workspace(cache, workspace);
    workspace_text_panes_from_cache(cache, &visible_surface_ids)
}

pub(crate) fn retain_workspace_text_pane_cache_for_workspace(
    cache: &mut HashMap<u64, CachedWorkspaceSingleSessionTextPane>,
    workspace: &Workspace,
) {
    let session_surface_ids = workspace
        .surfaces
        .iter()
        .filter(|surface| surface.kind == workspace::SurfaceKind::Session)
        .map(|surface| surface.id)
        .collect::<HashSet<_>>();
    cache.retain(|surface_id, _| session_surface_ids.contains(surface_id));
}

pub(crate) fn workspace_surface_text_pane_identity_key(
    workspace: &Workspace,
    surface: &workspace::Surface,
    panel_size: PhysicalSize<u32>,
) -> u64 {
    let mut hasher = DefaultHasher::new();
    panel_size.width.hash(&mut hasher);
    panel_size.height.hash(&mut hasher);
    surface.id.hash(&mut hasher);
    workspace_surface_kind_key(surface.kind).hash(&mut hasher);
    surface.title.hash(&mut hasher);
    surface.body_lines.hash(&mut hasher);
    surface.detail_lines.hash(&mut hasher);
    surface.session_id.hash(&mut hasher);
    surface.transcript_messages.len().hash(&mut hasher);
    for message in &surface.transcript_messages {
        message.role.hash(&mut hasher);
        message.content.hash(&mut hasher);
    }
    if workspace.mode == InputMode::Insert && workspace.is_focused(surface.id) {
        workspace.draft.hash(&mut hasher);
        workspace.draft_cursor.hash(&mut hasher);
        workspace.pending_images.len().hash(&mut hasher);
    }
    hasher.finish()
}

pub(crate) fn workspace_surface_kind_key(kind: workspace::SurfaceKind) -> u8 {
    match kind {
        workspace::SurfaceKind::Session => 0,
        workspace::SurfaceKind::Scratch => 1,
        workspace::SurfaceKind::WorkspacePlaceholder => 2,
        workspace::SurfaceKind::HotkeyHelp => 3,
        workspace::SurfaceKind::Loading => 4,
        workspace::SurfaceKind::Empty => 5,
    }
}

pub(crate) fn refresh_workspace_text_pane_cache_entry(
    cache: &mut HashMap<u64, CachedWorkspaceSingleSessionTextPane>,
    surface_id: u64,
    identity_key: u64,
    rect: Rect,
    panel_size: PhysicalSize<u32>,
    font_system: &mut FontSystem,
    build_app: impl FnOnce() -> Option<SingleSessionApp>,
) -> bool {
    if let Some(entry) = cache.get_mut(&surface_id)
        && entry.identity_key == identity_key
    {
        entry.rect = rect;
        entry.size = panel_size;
        return true;
    }

    let Some(app) = build_app() else {
        return false;
    };

    let rendered_body_lines = single_session_rendered_body_lines_for_tick(&app, panel_size, 0);
    let key = single_session_text_key_for_tick_with_rendered_body(
        &app,
        panel_size,
        0,
        0.0,
        &rendered_body_lines,
    );

    if let Some(entry) = cache.get_mut(&surface_id)
        && entry.text_key == key
    {
        entry.app = app;
        entry.rect = rect;
        entry.size = panel_size;
        return true;
    }

    let child_vertices = build_single_session_vertices_with_cached_body(
        &app,
        panel_size,
        0.0,
        0,
        0.0,
        1.0,
        &rendered_body_lines,
    );
    let buffers = single_session_text_buffers_from_key(&key, panel_size, font_system);
    cache.insert(
        surface_id,
        CachedWorkspaceSingleSessionTextPane {
            identity_key,
            text_key: key,
            app,
            rect,
            size: panel_size,
            rendered_body_lines,
            buffers,
            child_vertices,
        },
    );
    true
}

pub(crate) fn workspace_text_panes_from_cache<'a>(
    cache: &'a HashMap<u64, CachedWorkspaceSingleSessionTextPane>,
    surface_ids: &[u64],
) -> Vec<WorkspaceSingleSessionTextPane<'a>> {
    surface_ids
        .iter()
        .filter_map(|surface_id| cache.get(surface_id))
        .map(|entry| WorkspaceSingleSessionTextPane {
            app: &entry.app,
            rect: entry.rect,
            size: entry.size,
            rendered_body_lines: &entry.rendered_body_lines,
            buffers: &entry.buffers,
        })
        .collect()
}

pub(crate) fn workspace_single_session_text_areas<'a>(
    panes: &'a [WorkspaceSingleSessionTextPane<'a>],
) -> Vec<TextArea<'a>> {
    let mut areas = Vec::new();
    for pane in panes {
        let viewport = single_session_body_viewport_from_lines(
            pane.app,
            pane.size,
            0.0,
            pane.rendered_body_lines,
        );
        let pane_areas = single_session_text_areas_for_app_with_cached_body_viewport_and_reveal(
            pane.app,
            pane.buffers,
            pane.size,
            0.0,
            viewport,
            1.0,
        );
        areas.extend(pane_areas.into_iter().filter_map(|area| {
            let area = offset_workspace_text_area(area, pane.rect);
            (area.bounds.right > area.bounds.left && area.bounds.bottom > area.bounds.top)
                .then_some(area)
        }));
    }
    areas
}

pub(crate) fn offset_workspace_text_area<'a>(area: TextArea<'a>, rect: Rect) -> TextArea<'a> {
    let clip_left = rect.x.floor() as i32;
    let clip_top = rect.y.floor() as i32;
    let clip_right = (rect.x + rect.width).ceil() as i32;
    let clip_bottom = (rect.y + rect.height).ceil() as i32;
    TextArea {
        buffer: area.buffer,
        left: area.left + rect.x,
        top: area.top + rect.y,
        scale: area.scale,
        bounds: TextBounds {
            left: offset_text_bound(area.bounds.left, rect.x).max(clip_left),
            top: offset_text_bound(area.bounds.top, rect.y).max(clip_top),
            right: offset_text_bound(area.bounds.right, rect.x).min(clip_right),
            bottom: offset_text_bound(area.bounds.bottom, rect.y).min(clip_bottom),
        },
        default_color: area.default_color,
    }
}

pub(crate) fn offset_text_bound(value: i32, offset: f32) -> i32 {
    (value as f32 + offset)
        .round()
        .clamp(i32::MIN as f32, i32::MAX as f32) as i32
}

pub(crate) struct WorkspaceVertexBuildParams<'a> {
    pub(crate) workspace: &'a Workspace,
    pub(crate) size: PhysicalSize<u32>,
    pub(crate) render_layout: WorkspaceRenderLayout,
    pub(crate) focus_pulse: f32,
    pub(crate) space_hold_progress: Option<f32>,
    pub(crate) surface_frames: Option<&'a WorkspaceSurfaceTransitionFrames>,
    pub(crate) exiting_surfaces: &'a HashMap<u64, workspace::Surface>,
    pub(crate) workspace_panel_cache:
        Option<&'a HashMap<u64, CachedWorkspaceSingleSessionTextPane>>,
    pub(crate) status_color: [f32; 4],
    pub(crate) status_text_frame: Option<&'a StatusTextTransitionFrame>,
}

pub(crate) fn build_vertices_into(
    params: WorkspaceVertexBuildParams<'_>,
    vertices: &mut Vec<Vertex>,
) {
    let WorkspaceVertexBuildParams {
        workspace,
        size,
        render_layout,
        focus_pulse,
        space_hold_progress,
        surface_frames,
        exiting_surfaces,
        workspace_panel_cache,
        status_color,
        status_text_frame,
    } = params;
    vertices.clear();
    let width = size.width as f32;
    let height = size.height as f32;

    push_gradient_rect(
        vertices,
        Rect {
            x: 0.0,
            y: 0.0,
            width,
            height,
        },
        BACKGROUND_TOP_LEFT,
        BACKGROUND_BOTTOM_LEFT,
        BACKGROUND_BOTTOM_RIGHT,
        BACKGROUND_TOP_RIGHT,
        size,
    );

    let status_rect = Rect {
        x: OUTER_PADDING,
        y: OUTER_PADDING,
        width: (width - OUTER_PADDING * 2.0).max(1.0),
        height: STATUS_BAR_HEIGHT,
    };
    push_rounded_rect(vertices, status_rect, STATUS_RADIUS, status_color, size);

    let active_workspace = workspace.current_workspace();
    push_workspace_number(vertices, active_workspace, status_rect, size);
    push_status_preview(
        vertices,
        workspace,
        active_workspace,
        render_layout,
        surface_frames,
        exiting_surfaces,
        focus_pulse,
        status_rect,
        size,
    );
    push_status_text(vertices, workspace, status_rect, size, status_text_frame);

    if workspace.zoomed {
        if let Some(surface) = workspace.focused_surface() {
            let target_rect = Rect {
                x: OUTER_PADDING,
                y: STATUS_BAR_HEIGHT + OUTER_PADDING * 2.0,
                width: (width - OUTER_PADDING * 2.0).max(1.0),
                height: (height - STATUS_BAR_HEIGHT - OUTER_PADDING * 3.0).max(1.0),
            };
            let rect = workspace_transitioned_surface_rect(surface_frames, surface.id, target_rect);
            let opacity = workspace_transitioned_surface_opacity(surface_frames, surface.id);
            let start_index = vertices.len();
            if surface.kind == workspace::SurfaceKind::Session {
                if let Some(entry) = workspace_panel_cache.and_then(|cache| cache.get(&surface.id))
                {
                    if focus_pulse > 0.001 {
                        push_surface(vertices, rect, surface.color_index, true, focus_pulse, size);
                    }
                    append_child_vertices_to_parent_with_opacity(
                        vertices,
                        &entry.child_vertices,
                        entry.size,
                        rect,
                        size,
                        opacity,
                    );
                } else if let Some(app) =
                    workspace_single_session_app_for_surface(workspace, surface)
                {
                    push_workspace_single_session_panel(
                        vertices,
                        &app,
                        rect,
                        size,
                        focus_pulse,
                        opacity,
                    );
                }
            } else {
                push_surface(vertices, rect, surface.color_index, true, focus_pulse, size);
                let draft = focused_panel_draft(workspace, surface.id);
                push_panel_contents(
                    vertices,
                    surface,
                    rect,
                    size,
                    true,
                    workspace.detail_scroll,
                    draft.as_deref(),
                );
                multiply_vertex_alpha(&mut vertices[start_index..], opacity);
            }
        }
        if let Some(progress) = space_hold_progress {
            push_space_hold_progress(vertices, progress, size);
        }
        push_workspace_exiting_surfaces(vertices, surface_frames, exiting_surfaces, size);
        return;
    }

    for_each_visible_workspace_surface(
        workspace,
        size,
        render_layout,
        focus_pulse,
        |surface, target_rect, focused, surface_pulse| {
            let rect = workspace_transitioned_surface_rect(surface_frames, surface.id, target_rect);
            let opacity = workspace_transitioned_surface_opacity(surface_frames, surface.id);
            let start_index = vertices.len();
            if surface.kind == workspace::SurfaceKind::Session {
                if let Some(entry) = workspace_panel_cache.and_then(|cache| cache.get(&surface.id))
                {
                    if surface_pulse > 0.001 {
                        push_surface(
                            vertices,
                            rect,
                            surface.color_index,
                            focused,
                            surface_pulse,
                            size,
                        );
                    }
                    append_child_vertices_to_parent_with_opacity(
                        vertices,
                        &entry.child_vertices,
                        entry.size,
                        rect,
                        size,
                        opacity,
                    );
                    return;
                }
                let Some(app) = workspace_single_session_app_for_surface(workspace, surface) else {
                    return;
                };
                push_workspace_single_session_panel(
                    vertices,
                    &app,
                    rect,
                    size,
                    surface_pulse,
                    opacity,
                );
                return;
            }
            push_surface(
                vertices,
                rect,
                surface.color_index,
                focused,
                surface_pulse,
                size,
            );
            let draft = focused_panel_draft(workspace, surface.id);
            push_panel_contents(vertices, surface, rect, size, false, 0, draft.as_deref());
            multiply_vertex_alpha(&mut vertices[start_index..], opacity);
        },
    );

    push_workspace_exiting_surfaces(vertices, surface_frames, exiting_surfaces, size);

    if let Some(progress) = space_hold_progress {
        push_space_hold_progress(vertices, progress, size);
    }
}

pub(crate) fn push_workspace_exiting_surfaces(
    vertices: &mut Vec<Vertex>,
    surface_frames: Option<&WorkspaceSurfaceTransitionFrames>,
    exiting_surfaces: &HashMap<u64, workspace::Surface>,
    size: PhysicalSize<u32>,
) {
    let Some(surface_frames) = surface_frames else {
        return;
    };

    for frame in surface_frames.exiting_frames() {
        let Some(surface) = exiting_surfaces.get(&frame.id) else {
            continue;
        };
        let rect = rect_from_animated_rect(frame.visual_rect());
        let start_index = vertices.len();
        push_surface(vertices, rect, surface.color_index, false, 0.0, size);
        push_panel_contents(vertices, surface, rect, size, false, 0, None);
        multiply_vertex_alpha(&mut vertices[start_index..], frame.opacity);
    }
}

pub(crate) fn push_space_hold_progress(
    vertices: &mut Vec<Vertex>,
    progress: f32,
    size: PhysicalSize<u32>,
) {
    let width = size.width as f32;
    let bar_width = (width * SPACE_HOLD_PROGRESS_WIDTH_FRACTION).clamp(120.0, 460.0);
    let rect = Rect {
        x: (width - bar_width) * 0.5,
        y: OUTER_PADDING + STATUS_BAR_HEIGHT + 4.0,
        width: bar_width,
        height: SPACE_HOLD_PROGRESS_HEIGHT,
    };
    push_rounded_rect(
        vertices,
        rect,
        SPACE_HOLD_PROGRESS_HEIGHT * 0.5,
        SPACE_HOLD_PROGRESS_TRACK_COLOR,
        size,
    );
    let fill = Rect {
        width: (rect.width * progress.clamp(0.0, 1.0)).max(SPACE_HOLD_PROGRESS_HEIGHT),
        ..rect
    };
    push_rounded_rect(
        vertices,
        fill,
        SPACE_HOLD_PROGRESS_HEIGHT * 0.5,
        SPACE_HOLD_PROGRESS_FILL_COLOR,
        size,
    );
}

pub(crate) fn multiply_vertex_alpha(vertices: &mut [Vertex], opacity: f32) {
    let opacity = opacity.clamp(0.0, 1.0);
    if opacity >= 0.999 {
        return;
    }
    for vertex in vertices {
        vertex.color[3] *= opacity;
    }
}

pub(crate) fn workspace_render_layout(
    workspace: &Workspace,
    size: PhysicalSize<u32>,
    monitor_size: Option<PhysicalSize<u32>>,
) -> WorkspaceRenderLayout {
    let workspace_width = (size.width as f32 - OUTER_PADDING * 2.0).max(1.0);
    let workspace_height = (size.height as f32 - STATUS_BAR_HEIGHT - OUTER_PADDING * 3.0).max(1.0);
    let lane_pitch = workspace_height + GAP;
    let active_workspace = workspace.current_workspace();
    let visible = visible_column_layout(
        workspace,
        size.width,
        monitor_size.map(|size| size.width),
        active_workspace,
    );
    let visible_columns_f = visible.visible_columns as f32;
    let total_gap_width = GAP * (visible_columns_f - 1.0).max(0.0);
    let column_width = ((workspace_width - total_gap_width) / visible_columns_f).max(1.0);
    let scroll_offset = visible.first_visible_column as f32 * (column_width + GAP);
    let vertical_scroll_offset = active_workspace as f32 * lane_pitch;

    WorkspaceRenderLayout {
        visible,
        column_width,
        scroll_offset,
        vertical_scroll_offset,
    }
}

pub(crate) fn visible_column_layout(
    workspace: &Workspace,
    window_width: u32,
    monitor_width: Option<u32>,
    active_workspace: i32,
) -> VisibleColumnLayout {
    let visible_columns = inferred_visible_column_count(
        window_width,
        monitor_width,
        workspace.preferred_panel_screen_fraction(),
    );
    let focused_column = workspace
        .focused_surface()
        .map(|surface| surface.column)
        .unwrap_or_default();
    let (min_column, max_column) = workspace
        .surfaces
        .iter()
        .filter(|surface| surface.lane == active_workspace)
        .map(|surface| surface.column)
        .fold((focused_column, focused_column), |(min, max), column| {
            (min.min(column), max.max(column))
        });
    let visible_columns_i = visible_columns as i32;
    let max_first_column = (max_column - visible_columns_i + 1).max(min_column);
    let preferred_first_column = focused_column - visible_columns_i / 2;
    let first_visible_column = preferred_first_column.clamp(min_column, max_first_column);

    VisibleColumnLayout {
        visible_columns,
        first_visible_column,
    }
}

pub(crate) fn inferred_visible_column_count(
    window_width: u32,
    monitor_width: Option<u32>,
    preferred_panel_screen_fraction: f32,
) -> u32 {
    let Some(monitor_width) = monitor_width.filter(|width| *width > 0) else {
        return 1;
    };

    let preferred_panel_screen_fraction = preferred_panel_screen_fraction.clamp(0.25, 1.0);
    let target_panel_width = monitor_width as f32 * preferred_panel_screen_fraction;
    ((window_width as f32 / target_panel_width + PANEL_FIT_TOLERANCE).floor() as u32).clamp(1, 4)
}

pub(crate) fn push_status_text(
    vertices: &mut Vec<Vertex>,
    workspace: &Workspace,
    status_rect: Rect,
    size: PhysicalSize<u32>,
    transition_frame: Option<&StatusTextTransitionFrame>,
) {
    let settled;
    let frame = if let Some(frame) = transition_frame {
        frame
    } else {
        settled = StatusTextTransitionFrame::settled(workspace_status_text(workspace));
        &settled
    };
    if let Some(previous) = frame.previous.as_ref() {
        push_status_text_visual(vertices, previous, status_rect, size);
    }
    push_status_text_visual(vertices, &frame.current, status_rect, size);
}

pub(crate) fn push_status_text_visual(
    vertices: &mut Vec<Vertex>,
    visual: &StatusTextVisualFrame,
    status_rect: Rect,
    size: PhysicalSize<u32>,
) {
    if visual.opacity <= 0.001 {
        return;
    }
    let text_width = bitmap_text_width(&visual.text, BITMAP_TEXT_PIXEL);
    let x = status_rect.x + status_rect.width - STATUS_TEXT_RIGHT_PADDING - text_width;
    let y = status_rect.y
        + (status_rect.height - bitmap_text_height(BITMAP_TEXT_PIXEL)) / 2.0
        + visual.y_offset_pixels;
    if x > status_rect.x {
        let mut color = STATUS_TEXT_COLOR;
        color[3] *= visual.opacity.clamp(0.0, 1.0);
        push_bitmap_text(
            vertices,
            &visual.text,
            x,
            y,
            BITMAP_TEXT_PIXEL,
            color,
            size,
            text_width,
        );
    }
}

pub(crate) fn workspace_status_text(workspace: &Workspace) -> String {
    let mode = match workspace.mode {
        InputMode::Navigation => "NAV",
        InputMode::Insert => "INS",
    };
    let panel_percent = (workspace.preferred_panel_screen_fraction() * 100.0).round() as u32;
    format!("{mode} P{panel_percent} {}", desktop_build_hash_label())
}

pub(crate) fn workspace_status_bar_target_color(workspace: &Workspace) -> [f32; 4] {
    match workspace.mode {
        InputMode::Navigation => NAV_STATUS_COLOR,
        InputMode::Insert => INSERT_STATUS_COLOR,
    }
}
