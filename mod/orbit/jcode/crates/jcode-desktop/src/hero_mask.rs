use super::*;

pub(crate) const HERO_MASK_SHADER: &str = r#"
struct HeroVertexOutput {
    @builtin(position) position: vec4<f32>,
    @location(0) uv: vec2<f32>,
};

struct HeroRevealUniform {
    progress: f32,
    feather: f32,
    padding: vec2<f32>,
    color: vec4<f32>,
};

@group(0) @binding(0) var hero_alpha: texture_2d<f32>;
@group(0) @binding(1) var hero_reveal: texture_2d<f32>;
@group(0) @binding(2) var hero_sampler: sampler;
@group(0) @binding(3) var<uniform> hero_uniform: HeroRevealUniform;

@vertex
fn vs_main(@location(0) position: vec2<f32>, @location(1) uv: vec2<f32>) -> HeroVertexOutput {
    var out: HeroVertexOutput;
    out.position = vec4<f32>(position, 0.0, 1.0);
    out.uv = uv;
    return out;
}

@fragment
fn fs_main(in: HeroVertexOutput) -> @location(0) vec4<f32> {
    let glyph_alpha = textureSample(hero_alpha, hero_sampler, in.uv).r;
    let reveal_at = textureSample(hero_reveal, hero_sampler, in.uv).r;
    let feather = max(hero_uniform.feather, 0.0001);
    let t = clamp((hero_uniform.progress - reveal_at + feather) / (2.0 * feather), 0.0, 1.0);
    let softened = t * t * (3.0 - 2.0 * t);
    let reveal_alpha = select(softened, 1.0, hero_uniform.progress >= 0.999);
    return vec4<f32>(hero_uniform.color.rgb, glyph_alpha * reveal_alpha * hero_uniform.color.a);
}
"#;

pub(crate) fn hero_screenshot_capture_dir(args: &[String]) -> Option<PathBuf> {
    args.iter().enumerate().find_map(|(index, arg)| {
        arg.strip_prefix("--capture-hero-animation=")
            .map(PathBuf::from)
            .or_else(|| {
                (arg == "--capture-hero-animation")
                    .then(|| args.get(index + 1).map(PathBuf::from))
                    .flatten()
            })
    })
}

pub(crate) async fn run_hero_screenshot_capture(output_dir: &Path) -> Result<()> {
    std::fs::create_dir_all(output_dir).with_context(|| {
        format!(
            "failed to create hero screenshot directory {}",
            output_dir.display()
        )
    })?;

    let app = SingleSessionApp::new(None);
    let size = PhysicalSize::new(DEFAULT_WINDOW_WIDTH as u32, DEFAULT_WINDOW_HEIGHT as u32);
    let (target_image, _) = render_hero_frame_to_image(&app, size, 0, 1.0, true).await?;
    let target_path = output_dir.join("hero-font-target.png");
    target_image
        .save(&target_path)
        .with_context(|| format!("failed to save {}", target_path.display()))?;
    let frames = [0_u64, 150, 300, 450, 675, 900, 1125, 1350];
    let mut manifest = Vec::new();
    for elapsed_ms in frames {
        let progress = welcome_hero_reveal_progress_for_elapsed(Duration::from_millis(elapsed_ms));
        let tick = elapsed_ms / DESKTOP_SPINNER_FRAME_MS as u64;
        let (image, vertices_len) =
            render_hero_frame_to_image(&app, size, tick, progress, false).await?;
        let filename = format!("hero-{elapsed_ms:04}ms.png");
        let path = output_dir.join(&filename);
        image
            .save(&path)
            .with_context(|| format!("failed to save {}", path.display()))?;
        manifest.push(serde_json::json!({
            "file": filename,
            "elapsed_ms": elapsed_ms,
            "progress": progress,
            "vertices": vertices_len,
        }));
    }

    let manifest_path = output_dir.join("manifest.json");
    let manifest_json = serde_json::to_string_pretty(&manifest)
        .context("failed to serialize hero frame manifest")?;
    std::fs::write(&manifest_path, manifest_json)
        .with_context(|| format!("failed to save {}", manifest_path.display()))?;
    println!(
        "{}",
        serde_json::json!({
            "output_dir": output_dir,
            "font_target": "hero-font-target.png",
            "frames": manifest,
        })
    );
    Ok(())
}

pub(crate) async fn render_hero_frame_to_image(
    app: &SingleSessionApp,
    size: PhysicalSize<u32>,
    spinner_tick: u64,
    welcome_hero_reveal_progress: f32,
    font_target_only: bool,
) -> Result<(RgbaImage, usize)> {
    let instance = wgpu::Instance::new(wgpu::InstanceDescriptor {
        backends: wgpu::Backends::PRIMARY,
        ..Default::default()
    });
    let adapter = instance
        .request_adapter(&wgpu::RequestAdapterOptions {
            power_preference: wgpu::PowerPreference::LowPower,
            compatible_surface: None,
            force_fallback_adapter: false,
        })
        .await
        .context("failed to find a GPU adapter for hero capture")?;
    let (device, queue) = adapter
        .request_device(
            &wgpu::DeviceDescriptor {
                label: Some("jcode-desktop-hero-capture-device"),
                required_features: wgpu::Features::empty(),
                required_limits: wgpu::Limits::default(),
            },
            None,
        )
        .await
        .context("failed to create GPU device for hero capture")?;

    let format = wgpu::TextureFormat::Rgba8UnormSrgb;
    let shader = device.create_shader_module(wgpu::ShaderModuleDescriptor {
        label: Some("jcode-desktop-hero-capture-primitive-shader"),
        source: wgpu::ShaderSource::Wgsl(SHADER.into()),
    });
    let pipeline_layout = device.create_pipeline_layout(&wgpu::PipelineLayoutDescriptor {
        label: Some("jcode-desktop-hero-capture-pipeline-layout"),
        bind_group_layouts: &[],
        push_constant_ranges: &[],
    });
    let render_pipeline = device.create_render_pipeline(&wgpu::RenderPipelineDescriptor {
        label: Some("jcode-desktop-hero-capture-primitive-pipeline"),
        layout: Some(&pipeline_layout),
        vertex: wgpu::VertexState {
            module: &shader,
            entry_point: "vs_main",
            buffers: &[Vertex::layout()],
        },
        fragment: Some(wgpu::FragmentState {
            module: &shader,
            entry_point: "fs_main",
            targets: &[Some(wgpu::ColorTargetState {
                format,
                blend: Some(wgpu::BlendState::ALPHA_BLENDING),
                write_mask: wgpu::ColorWrites::ALL,
            })],
        }),
        primitive: wgpu::PrimitiveState {
            topology: wgpu::PrimitiveTopology::TriangleList,
            strip_index_format: None,
            front_face: wgpu::FrontFace::Ccw,
            cull_mode: None,
            polygon_mode: wgpu::PolygonMode::Fill,
            unclipped_depth: false,
            conservative: false,
        },
        depth_stencil: None,
        multisample: wgpu::MultisampleState::default(),
        multiview: None,
    });

    let texture = device.create_texture(&wgpu::TextureDescriptor {
        label: Some("jcode-desktop-hero-capture-texture"),
        size: wgpu::Extent3d {
            width: size.width,
            height: size.height,
            depth_or_array_layers: 1,
        },
        mip_level_count: 1,
        sample_count: 1,
        dimension: wgpu::TextureDimension::D2,
        format,
        usage: wgpu::TextureUsages::RENDER_ATTACHMENT | wgpu::TextureUsages::COPY_SRC,
        view_formats: &[],
    });
    let view = texture.create_view(&wgpu::TextureViewDescriptor::default());

    let mut font_system = create_desktop_font_system();
    let mut swash_cache = SwashCache::new();
    let mut text_atlas = TextAtlas::new(&device, &queue, format);
    let mut text_renderer = TextRenderer::new(
        &mut text_atlas,
        &device,
        wgpu::MultisampleState::default(),
        None,
    );
    let mut hero_mask_renderer = HeroMaskRenderer::new(&device, format);

    let rendered_body_lines = single_session_rendered_body_lines_for_tick(app, size, spinner_tick);
    let text_key = single_session_text_key_for_tick_with_rendered_body(
        app,
        size,
        spinner_tick,
        0.0,
        &rendered_body_lines,
    );
    let text_buffers = single_session_text_buffers_from_key(&text_key, size, &mut font_system);
    let viewport = single_session_body_viewport_from_lines(app, size, 0.0, &rendered_body_lines);
    let hero_mask_spec = if font_target_only {
        welcome_hero_runtime_mask_spec_for_phrase(
            &app.welcome_hero_text(),
            size,
            app.text_scale(),
            0.0,
        )
    } else {
        welcome_hero_runtime_mask_spec_for_total_lines(app, size, 0.0, rendered_body_lines.len())
    };
    let text_areas = if font_target_only {
        Vec::new()
    } else {
        single_session_text_areas_for_app_with_cached_body_viewport_and_reveal(
            app,
            &text_buffers,
            size,
            0.0,
            viewport,
            welcome_hero_reveal_progress,
        )
    };
    let has_text_areas = !text_areas.is_empty();
    if has_text_areas {
        text_renderer
            .prepare(
                &device,
                &queue,
                &mut font_system,
                &mut text_atlas,
                Resolution {
                    width: size.width,
                    height: size.height,
                },
                text_areas,
                &mut swash_cache,
            )
            .context("failed to prepare hero capture text")?;
    }

    let vertices = if font_target_only {
        let mut vertices = build_single_session_vertices_with_cached_body(
            app,
            size,
            0.0,
            spinner_tick,
            0.0,
            0.0,
            &rendered_body_lines,
        );
        vertices.clear();
        push_gradient_rect(
            &mut vertices,
            Rect {
                x: 0.0,
                y: 0.0,
                width: size.width as f32,
                height: size.height as f32,
            },
            BACKGROUND_TOP_LEFT,
            BACKGROUND_BOTTOM_LEFT,
            BACKGROUND_BOTTOM_RIGHT,
            BACKGROUND_TOP_RIGHT,
            size,
        );
        vertices
    } else {
        build_single_session_vertices_with_cached_body(
            app,
            size,
            0.0,
            spinner_tick,
            0.0,
            welcome_hero_reveal_progress,
            &rendered_body_lines,
        )
    };
    let hero_mask_prepared = hero_mask_renderer.prepare(
        &device,
        &queue,
        size,
        hero_mask_spec.as_ref(),
        welcome_hero_reveal_progress,
    );
    let vertex_buffer = device.create_buffer(&wgpu::BufferDescriptor {
        label: Some("jcode-desktop-hero-capture-vertices"),
        size: (vertices.len() * std::mem::size_of::<Vertex>()) as wgpu::BufferAddress,
        usage: wgpu::BufferUsages::VERTEX | wgpu::BufferUsages::COPY_DST,
        mapped_at_creation: false,
    });
    queue.write_buffer(&vertex_buffer, 0, bytemuck::cast_slice(&vertices));

    let bytes_per_pixel = 4u32;
    let unpadded_bytes_per_row = size.width * bytes_per_pixel;
    let padded_bytes_per_row = unpadded_bytes_per_row.div_ceil(wgpu::COPY_BYTES_PER_ROW_ALIGNMENT)
        * wgpu::COPY_BYTES_PER_ROW_ALIGNMENT;
    let output_buffer_size = padded_bytes_per_row as u64 * size.height as u64;
    let output_buffer = device.create_buffer(&wgpu::BufferDescriptor {
        label: Some("jcode-desktop-hero-capture-readback"),
        size: output_buffer_size,
        usage: wgpu::BufferUsages::COPY_DST | wgpu::BufferUsages::MAP_READ,
        mapped_at_creation: false,
    });

    let mut encoder = device.create_command_encoder(&wgpu::CommandEncoderDescriptor {
        label: Some("jcode-desktop-hero-capture-encoder"),
    });
    {
        let mut render_pass = encoder.begin_render_pass(&wgpu::RenderPassDescriptor {
            label: Some("jcode-desktop-hero-capture-pass"),
            color_attachments: &[Some(wgpu::RenderPassColorAttachment {
                view: &view,
                resolve_target: None,
                ops: wgpu::Operations {
                    load: wgpu::LoadOp::Clear(CLEAR_COLOR),
                    store: wgpu::StoreOp::Store,
                },
            })],
            depth_stencil_attachment: None,
            timestamp_writes: None,
            occlusion_query_set: None,
        });
        render_pass.set_pipeline(&render_pipeline);
        render_pass.set_vertex_buffer(0, vertex_buffer.slice(..));
        render_pass.draw(0..vertices.len() as u32, 0..1);
        if hero_mask_prepared {
            hero_mask_renderer.render_prepared(&mut render_pass);
        }
        if has_text_areas {
            text_renderer
                .render(&text_atlas, &mut render_pass)
                .context("failed to render hero capture text")?;
        }
    }
    encoder.copy_texture_to_buffer(
        wgpu::ImageCopyTexture {
            texture: &texture,
            mip_level: 0,
            origin: wgpu::Origin3d::ZERO,
            aspect: wgpu::TextureAspect::All,
        },
        wgpu::ImageCopyBuffer {
            buffer: &output_buffer,
            layout: wgpu::ImageDataLayout {
                offset: 0,
                bytes_per_row: Some(padded_bytes_per_row),
                rows_per_image: Some(size.height),
            },
        },
        wgpu::Extent3d {
            width: size.width,
            height: size.height,
            depth_or_array_layers: 1,
        },
    );
    queue.submit(Some(encoder.finish()));

    let buffer_slice = output_buffer.slice(..);
    let (tx, rx) = mpsc::channel();
    buffer_slice.map_async(wgpu::MapMode::Read, move |result| {
        if tx.send(result).is_err() {
            desktop_log::warn(format_args!(
                "jcode-desktop: failed to deliver hero capture readback result"
            ));
        }
    });
    device.poll(wgpu::Maintain::Wait);
    rx.recv()
        .context("hero capture readback channel closed")?
        .context("failed to map hero capture readback buffer")?;
    let mapped = buffer_slice.get_mapped_range();
    let mut pixels = vec![0_u8; (unpadded_bytes_per_row * size.height) as usize];
    for y in 0..size.height as usize {
        let src_start = y * padded_bytes_per_row as usize;
        let dst_start = y * unpadded_bytes_per_row as usize;
        pixels[dst_start..dst_start + unpadded_bytes_per_row as usize]
            .copy_from_slice(&mapped[src_start..src_start + unpadded_bytes_per_row as usize]);
    }
    drop(mapped);
    output_buffer.unmap();
    let image = RgbaImage::from_raw(size.width, size.height, pixels)
        .context("failed to construct hero capture image")?;
    Ok((image, vertices.len()))
}

pub(crate) const HERO_MASK_TEXTURE_FORMAT: wgpu::TextureFormat = wgpu::TextureFormat::Rgba8Unorm;
pub(crate) const HERO_MASK_FEATHER: f32 = 0.026;
pub(crate) const HERO_MASK_MAX_TEXTURE_WIDTH: u32 = 2048;
pub(crate) const HERO_MASK_MAX_TEXTURE_HEIGHT: u32 = 512;

#[repr(C)]
#[derive(Clone, Copy, Debug, Pod, Zeroable)]
pub(crate) struct HeroMaskVertex {
    position: [f32; 2],
    uv: [f32; 2],
}

impl HeroMaskVertex {
    pub(crate) fn layout() -> wgpu::VertexBufferLayout<'static> {
        wgpu::VertexBufferLayout {
            array_stride: std::mem::size_of::<HeroMaskVertex>() as wgpu::BufferAddress,
            step_mode: wgpu::VertexStepMode::Vertex,
            attributes: &[
                wgpu::VertexAttribute {
                    offset: 0,
                    shader_location: 0,
                    format: wgpu::VertexFormat::Float32x2,
                },
                wgpu::VertexAttribute {
                    offset: std::mem::size_of::<[f32; 2]>() as wgpu::BufferAddress,
                    shader_location: 1,
                    format: wgpu::VertexFormat::Float32x2,
                },
            ],
        }
    }
}

#[repr(C)]
#[derive(Clone, Copy, Debug, Pod, Zeroable)]
pub(crate) struct HeroRevealUniform {
    progress: f32,
    feather: f32,
    padding: [f32; 2],
    color: [f32; 4],
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) struct HeroMaskKey {
    phrase: String,
    width: u32,
    height: u32,
    font_size_milli: u32,
}

pub(crate) struct HeroMaskImage {
    pub(crate) width: u32,
    pub(crate) height: u32,
    pub(crate) glyph_rgba: Vec<u8>,
    pub(crate) reveal_rgba: Vec<u8>,
}

pub(crate) struct HeroMaskResources {
    key: HeroMaskKey,
    bind_group: wgpu::BindGroup,
    _glyph_texture: wgpu::Texture,
    _glyph_view: wgpu::TextureView,
    _reveal_texture: wgpu::Texture,
    _reveal_view: wgpu::TextureView,
}

pub(crate) struct HeroMaskRenderer {
    pipeline: wgpu::RenderPipeline,
    bind_group_layout: wgpu::BindGroupLayout,
    sampler: wgpu::Sampler,
    vertex_buffer: wgpu::Buffer,
    uniform_buffer: wgpu::Buffer,
    resources: Option<HeroMaskResources>,
    prepared: bool,
}

impl HeroMaskRenderer {
    pub(crate) fn new(device: &wgpu::Device, target_format: wgpu::TextureFormat) -> Self {
        let bind_group_layout = device.create_bind_group_layout(&wgpu::BindGroupLayoutDescriptor {
            label: Some("jcode-desktop-hero-mask-bind-group-layout"),
            entries: &[
                wgpu::BindGroupLayoutEntry {
                    binding: 0,
                    visibility: wgpu::ShaderStages::FRAGMENT,
                    ty: wgpu::BindingType::Texture {
                        sample_type: wgpu::TextureSampleType::Float { filterable: true },
                        view_dimension: wgpu::TextureViewDimension::D2,
                        multisampled: false,
                    },
                    count: None,
                },
                wgpu::BindGroupLayoutEntry {
                    binding: 1,
                    visibility: wgpu::ShaderStages::FRAGMENT,
                    ty: wgpu::BindingType::Texture {
                        sample_type: wgpu::TextureSampleType::Float { filterable: true },
                        view_dimension: wgpu::TextureViewDimension::D2,
                        multisampled: false,
                    },
                    count: None,
                },
                wgpu::BindGroupLayoutEntry {
                    binding: 2,
                    visibility: wgpu::ShaderStages::FRAGMENT,
                    ty: wgpu::BindingType::Sampler(wgpu::SamplerBindingType::Filtering),
                    count: None,
                },
                wgpu::BindGroupLayoutEntry {
                    binding: 3,
                    visibility: wgpu::ShaderStages::FRAGMENT,
                    ty: wgpu::BindingType::Buffer {
                        ty: wgpu::BufferBindingType::Uniform,
                        has_dynamic_offset: false,
                        min_binding_size: None,
                    },
                    count: None,
                },
            ],
        });
        let pipeline_layout = device.create_pipeline_layout(&wgpu::PipelineLayoutDescriptor {
            label: Some("jcode-desktop-hero-mask-pipeline-layout"),
            bind_group_layouts: &[&bind_group_layout],
            push_constant_ranges: &[],
        });
        let shader = device.create_shader_module(wgpu::ShaderModuleDescriptor {
            label: Some("jcode-desktop-hero-mask-shader"),
            source: wgpu::ShaderSource::Wgsl(HERO_MASK_SHADER.into()),
        });
        let pipeline = device.create_render_pipeline(&wgpu::RenderPipelineDescriptor {
            label: Some("jcode-desktop-hero-mask-pipeline"),
            layout: Some(&pipeline_layout),
            vertex: wgpu::VertexState {
                module: &shader,
                entry_point: "vs_main",
                buffers: &[HeroMaskVertex::layout()],
            },
            fragment: Some(wgpu::FragmentState {
                module: &shader,
                entry_point: "fs_main",
                targets: &[Some(wgpu::ColorTargetState {
                    format: target_format,
                    blend: Some(wgpu::BlendState::ALPHA_BLENDING),
                    write_mask: wgpu::ColorWrites::ALL,
                })],
            }),
            primitive: wgpu::PrimitiveState {
                topology: wgpu::PrimitiveTopology::TriangleList,
                strip_index_format: None,
                front_face: wgpu::FrontFace::Ccw,
                cull_mode: None,
                polygon_mode: wgpu::PolygonMode::Fill,
                unclipped_depth: false,
                conservative: false,
            },
            depth_stencil: None,
            multisample: wgpu::MultisampleState::default(),
            multiview: None,
        });
        let sampler = device.create_sampler(&wgpu::SamplerDescriptor {
            label: Some("jcode-desktop-hero-mask-sampler"),
            address_mode_u: wgpu::AddressMode::ClampToEdge,
            address_mode_v: wgpu::AddressMode::ClampToEdge,
            address_mode_w: wgpu::AddressMode::ClampToEdge,
            mag_filter: wgpu::FilterMode::Linear,
            min_filter: wgpu::FilterMode::Linear,
            mipmap_filter: wgpu::FilterMode::Nearest,
            ..Default::default()
        });
        let vertex_buffer = device.create_buffer(&wgpu::BufferDescriptor {
            label: Some("jcode-desktop-hero-mask-vertices"),
            size: (6 * std::mem::size_of::<HeroMaskVertex>()) as wgpu::BufferAddress,
            usage: wgpu::BufferUsages::VERTEX | wgpu::BufferUsages::COPY_DST,
            mapped_at_creation: false,
        });
        let uniform_buffer = device.create_buffer(&wgpu::BufferDescriptor {
            label: Some("jcode-desktop-hero-mask-uniform"),
            size: std::mem::size_of::<HeroRevealUniform>() as wgpu::BufferAddress,
            usage: wgpu::BufferUsages::UNIFORM | wgpu::BufferUsages::COPY_DST,
            mapped_at_creation: false,
        });

        Self {
            pipeline,
            bind_group_layout,
            sampler,
            vertex_buffer,
            uniform_buffer,
            resources: None,
            prepared: false,
        }
    }

    pub(crate) fn prepare(
        &mut self,
        device: &wgpu::Device,
        queue: &wgpu::Queue,
        target_size: PhysicalSize<u32>,
        spec: Option<&WelcomeHeroRuntimeMaskSpec>,
        progress: f32,
    ) -> bool {
        self.prepared = false;
        let Some(spec) = spec else {
            return false;
        };
        if target_size.width == 0 || target_size.height == 0 {
            return false;
        }
        let width = (spec.rect.width.ceil() as u32).clamp(1, HERO_MASK_MAX_TEXTURE_WIDTH);
        let height = (spec.rect.height.ceil() as u32).clamp(1, HERO_MASK_MAX_TEXTURE_HEIGHT);
        let key = HeroMaskKey {
            phrase: spec.phrase.clone(),
            width,
            height,
            font_size_milli: (spec.font_size.max(1.0) * 1000.0).round() as u32,
        };

        if self.resources.as_ref().map(|resources| &resources.key) != Some(&key) {
            let Some(mask) = build_hero_mask_image(&spec.phrase, width, height, spec.font_size)
            else {
                self.resources = None;
                return false;
            };
            self.resources = Some(self.create_resources(device, queue, key, mask));
        }

        let vertices = hero_mask_quad_vertices(spec.rect, target_size);
        queue.write_buffer(&self.vertex_buffer, 0, bytemuck::cast_slice(&vertices));
        let uniform = HeroRevealUniform {
            progress: progress.clamp(0.0, 1.0),
            feather: HERO_MASK_FEATHER,
            padding: [0.0, 0.0],
            color: WELCOME_HANDWRITING_COLOR,
        };
        queue.write_buffer(&self.uniform_buffer, 0, bytemuck::bytes_of(&uniform));
        self.prepared = true;
        true
    }

    pub(crate) fn create_resources(
        &self,
        device: &wgpu::Device,
        queue: &wgpu::Queue,
        key: HeroMaskKey,
        mask: HeroMaskImage,
    ) -> HeroMaskResources {
        let glyph_texture = create_hero_mask_texture(
            device,
            queue,
            "jcode-desktop-hero-alpha-texture",
            mask.width,
            mask.height,
            &mask.glyph_rgba,
        );
        let reveal_texture = create_hero_mask_texture(
            device,
            queue,
            "jcode-desktop-hero-reveal-texture",
            mask.width,
            mask.height,
            &mask.reveal_rgba,
        );
        let glyph_view = glyph_texture.create_view(&wgpu::TextureViewDescriptor::default());
        let reveal_view = reveal_texture.create_view(&wgpu::TextureViewDescriptor::default());
        let bind_group = device.create_bind_group(&wgpu::BindGroupDescriptor {
            label: Some("jcode-desktop-hero-mask-bind-group"),
            layout: &self.bind_group_layout,
            entries: &[
                wgpu::BindGroupEntry {
                    binding: 0,
                    resource: wgpu::BindingResource::TextureView(&glyph_view),
                },
                wgpu::BindGroupEntry {
                    binding: 1,
                    resource: wgpu::BindingResource::TextureView(&reveal_view),
                },
                wgpu::BindGroupEntry {
                    binding: 2,
                    resource: wgpu::BindingResource::Sampler(&self.sampler),
                },
                wgpu::BindGroupEntry {
                    binding: 3,
                    resource: self.uniform_buffer.as_entire_binding(),
                },
            ],
        });

        HeroMaskResources {
            key,
            bind_group,
            _glyph_texture: glyph_texture,
            _glyph_view: glyph_view,
            _reveal_texture: reveal_texture,
            _reveal_view: reveal_view,
        }
    }

    pub(crate) fn render_prepared<'a>(&'a self, render_pass: &mut wgpu::RenderPass<'a>) {
        if !self.prepared {
            return;
        }
        let Some(resources) = self.resources.as_ref() else {
            return;
        };
        render_pass.set_pipeline(&self.pipeline);
        render_pass.set_bind_group(0, &resources.bind_group, &[]);
        render_pass.set_vertex_buffer(0, self.vertex_buffer.slice(..));
        render_pass.draw(0..6, 0..1);
    }
}

pub(crate) fn create_hero_mask_texture(
    device: &wgpu::Device,
    queue: &wgpu::Queue,
    label: &'static str,
    width: u32,
    height: u32,
    rgba: &[u8],
) -> wgpu::Texture {
    let texture = device.create_texture(&wgpu::TextureDescriptor {
        label: Some(label),
        size: wgpu::Extent3d {
            width,
            height,
            depth_or_array_layers: 1,
        },
        mip_level_count: 1,
        sample_count: 1,
        dimension: wgpu::TextureDimension::D2,
        format: HERO_MASK_TEXTURE_FORMAT,
        usage: wgpu::TextureUsages::TEXTURE_BINDING | wgpu::TextureUsages::COPY_DST,
        view_formats: &[],
    });
    queue.write_texture(
        wgpu::ImageCopyTexture {
            texture: &texture,
            mip_level: 0,
            origin: wgpu::Origin3d::ZERO,
            aspect: wgpu::TextureAspect::All,
        },
        rgba,
        wgpu::ImageDataLayout {
            offset: 0,
            bytes_per_row: Some(width * 4),
            rows_per_image: Some(height),
        },
        wgpu::Extent3d {
            width,
            height,
            depth_or_array_layers: 1,
        },
    );
    texture
}

pub(crate) fn hero_mask_quad_vertices(
    rect: Rect,
    target_size: PhysicalSize<u32>,
) -> [HeroMaskVertex; 6] {
    let target_width = target_size.width.max(1) as f32;
    let target_height = target_size.height.max(1) as f32;
    let left = rect.x / target_width * 2.0 - 1.0;
    let right = (rect.x + rect.width) / target_width * 2.0 - 1.0;
    let top = 1.0 - rect.y / target_height * 2.0;
    let bottom = 1.0 - (rect.y + rect.height) / target_height * 2.0;
    [
        HeroMaskVertex {
            position: [left, top],
            uv: [0.0, 0.0],
        },
        HeroMaskVertex {
            position: [left, bottom],
            uv: [0.0, 1.0],
        },
        HeroMaskVertex {
            position: [right, bottom],
            uv: [1.0, 1.0],
        },
        HeroMaskVertex {
            position: [left, top],
            uv: [0.0, 0.0],
        },
        HeroMaskVertex {
            position: [right, bottom],
            uv: [1.0, 1.0],
        },
        HeroMaskVertex {
            position: [right, top],
            uv: [1.0, 0.0],
        },
    ]
}

#[derive(Clone, Copy, Debug)]
pub(crate) struct HeroMaskPixelBounds {
    pub(crate) min_x: u32,
    pub(crate) min_y: u32,
    pub(crate) max_x: u32,
    pub(crate) max_y: u32,
}

impl HeroMaskPixelBounds {
    pub(crate) fn width(self) -> f32 {
        self.max_x.saturating_sub(self.min_x).max(1) as f32
    }

    pub(crate) fn height(self) -> f32 {
        self.max_y.saturating_sub(self.min_y).max(1) as f32
    }
}

pub(crate) fn build_hero_mask_image(
    phrase: &str,
    width: u32,
    height: u32,
    requested_font_size: f32,
) -> Option<HeroMaskImage> {
    let font = hero_handwriting_font()?;
    let mut font_size = requested_font_size.max(1.0);
    let mut glyphs = layout_hero_glyphs(font, phrase, font_size, point(0.0, 0.0));
    let mut bounds = hero_glyph_bounds(font, &glyphs)?;
    let target_width = width as f32 * 0.92;
    let target_height = height as f32 * 0.78;
    let glyph_width = (bounds.2 - bounds.0).max(1.0);
    let glyph_height = (bounds.3 - bounds.1).max(1.0);
    let fit = (target_width / glyph_width)
        .min(target_height / glyph_height)
        .min(1.0);
    if fit < 0.995 {
        font_size *= fit;
        glyphs = layout_hero_glyphs(font, phrase, font_size, point(0.0, 0.0));
        bounds = hero_glyph_bounds(font, &glyphs)?;
    }

    let glyph_width = (bounds.2 - bounds.0).max(1.0);
    let glyph_height = (bounds.3 - bounds.1).max(1.0);
    let origin = point(
        (width as f32 - glyph_width) * 0.5 - bounds.0,
        (height as f32 - glyph_height) * 0.48 - bounds.1,
    );
    let glyphs = layout_hero_glyphs(font, phrase, font_size, origin);
    let mut glyph_rgba = vec![0_u8; (width * height * 4) as usize];
    draw_hero_glyphs(font, &glyphs, width, height, &mut glyph_rgba);

    let alpha_bounds = hero_alpha_bounds(&glyph_rgba, width, height)?;
    let reveal_rgba = build_hero_reveal_texture(phrase, width, height, &glyph_rgba, alpha_bounds)?;
    Some(HeroMaskImage {
        width,
        height,
        glyph_rgba,
        reveal_rgba,
    })
}

pub(crate) fn hero_handwriting_font() -> Option<&'static FontArc> {
    static HERO_FONT: OnceLock<Option<FontArc>> = OnceLock::new();
    HERO_FONT
        .get_or_init(|| {
            FontArc::try_from_slice(include_bytes!("../assets/fonts/HomemadeApple-Regular.ttf"))
                .ok()
        })
        .as_ref()
}

pub(crate) fn layout_hero_glyphs(
    font: &FontArc,
    phrase: &str,
    font_size: f32,
    origin: ab_glyph::Point,
) -> Vec<AbGlyph> {
    let scale = PxScale::from(font_size);
    let scaled = font.as_scaled(scale);
    let mut caret_x = origin.x;
    let mut previous = None;
    let mut glyphs = Vec::new();
    for ch in phrase.chars() {
        let id = scaled.glyph_id(ch);
        if let Some(previous) = previous {
            caret_x += scaled.kern(previous, id);
        }
        glyphs.push(id.with_scale_and_position(scale, point(caret_x, origin.y)));
        caret_x += scaled.h_advance(id);
        previous = Some(id);
    }
    glyphs
}

pub(crate) fn hero_glyph_bounds(
    font: &FontArc,
    glyphs: &[AbGlyph],
) -> Option<(f32, f32, f32, f32)> {
    let mut bounds = None::<(f32, f32, f32, f32)>;
    for glyph in glyphs.iter().cloned() {
        let Some(outlined) = font.outline_glyph(glyph) else {
            continue;
        };
        let px = outlined.px_bounds();
        bounds = Some(match bounds {
            Some((min_x, min_y, max_x, max_y)) => (
                min_x.min(px.min.x),
                min_y.min(px.min.y),
                max_x.max(px.max.x),
                max_y.max(px.max.y),
            ),
            None => (px.min.x, px.min.y, px.max.x, px.max.y),
        });
    }
    bounds
}

pub(crate) fn draw_hero_glyphs(
    font: &FontArc,
    glyphs: &[AbGlyph],
    width: u32,
    height: u32,
    glyph_rgba: &mut [u8],
) {
    for glyph in glyphs.iter().cloned() {
        let Some(outlined) = font.outline_glyph(glyph) else {
            continue;
        };
        let bounds = outlined.px_bounds();
        let min_x = bounds.min.x as i32;
        let min_y = bounds.min.y as i32;
        outlined.draw(|x, y, coverage| {
            let px = min_x + x as i32;
            let py = min_y + y as i32;
            if px < 0 || py < 0 || px >= width as i32 || py >= height as i32 {
                return;
            }
            let alpha = (coverage.clamp(0.0, 1.0) * 255.0).round() as u8;
            let index = ((py as u32 * width + px as u32) * 4) as usize;
            if alpha > glyph_rgba[index] {
                glyph_rgba[index] = alpha;
                glyph_rgba[index + 1] = alpha;
                glyph_rgba[index + 2] = alpha;
                glyph_rgba[index + 3] = 255;
            }
        });
    }
}

pub(crate) fn hero_alpha_bounds(
    glyph_rgba: &[u8],
    width: u32,
    height: u32,
) -> Option<HeroMaskPixelBounds> {
    let mut min_x = width;
    let mut min_y = height;
    let mut max_x = 0;
    let mut max_y = 0;
    for y in 0..height {
        for x in 0..width {
            let alpha = glyph_rgba[((y * width + x) * 4) as usize];
            if alpha <= 2 {
                continue;
            }
            min_x = min_x.min(x);
            min_y = min_y.min(y);
            max_x = max_x.max(x + 1);
            max_y = max_y.max(y + 1);
        }
    }
    (min_x < max_x && min_y < max_y).then_some(HeroMaskPixelBounds {
        min_x,
        min_y,
        max_x,
        max_y,
    })
}

pub(crate) fn build_hero_reveal_texture(
    phrase: &str,
    width: u32,
    height: u32,
    glyph_rgba: &[u8],
    alpha_bounds: HeroMaskPixelBounds,
) -> Option<Vec<u8>> {
    let segments = welcome_hero_normalized_stroke_segments(phrase);
    if segments.is_empty() {
        return None;
    }

    let mut values = vec![1.0_f32; (width * height) as usize];
    let brush_delay_px = (alpha_bounds.height() * 0.10).max(5.0);

    // This per-pixel nearest-stroke search dominates the one-time hero mask
    // build (hundreds of ms on the UI thread). Each lit pixel is independent
    // and only reads `glyph_rgba`/`segments`, so split the rows across worker
    // threads. Output is bit-identical to the serial version; min/max are
    // reduced afterward from the filled buffer.
    let (min_value, max_value) = fill_hero_reveal_values(
        &mut values,
        width,
        height,
        glyph_rgba,
        alpha_bounds,
        &segments,
        brush_delay_px,
    );

    if !min_value.is_finite() || max_value <= min_value {
        return None;
    }

    let mut reveal_rgba = vec![255_u8; (width * height * 4) as usize];
    let scale = 0.970 / (max_value - min_value).max(0.001);
    for y in 0..height {
        for x in 0..width {
            let pixel_index = (y * width + x) as usize;
            let alpha = glyph_rgba[pixel_index * 4];
            if alpha <= 2 {
                continue;
            }
            let normalized = 0.006 + (values[pixel_index] - min_value) * scale;
            let value = normalized.clamp(0.0, 0.985);
            let encoded = (value * 255.0).round() as u8;
            let rgba_index = pixel_index * 4;
            reveal_rgba[rgba_index] = encoded;
            reveal_rgba[rgba_index + 1] = encoded;
            reveal_rgba[rgba_index + 2] = encoded;
            reveal_rgba[rgba_index + 3] = 255;
        }
    }
    Some(reveal_rgba)
}

/// Fill `values` with each lit pixel's reveal progress and return the
/// `(min, max)` of the written values.
///
/// The work is split into horizontal row bands processed on separate threads
/// when the image is large enough to amortize the spawn cost. Pixels are
/// independent, so the result is identical to a serial fill.
pub(crate) fn fill_hero_reveal_values(
    values: &mut [f32],
    width: u32,
    height: u32,
    glyph_rgba: &[u8],
    alpha_bounds: HeroMaskPixelBounds,
    segments: &[WelcomeHeroStrokeSegment],
    brush_delay_px: f32,
) -> (f32, f32) {
    let row_stride = width as usize;
    let compute_row = |row_index: u32, row_values: &mut [f32]| -> (f32, f32) {
        let mut min_value = f32::INFINITY;
        let mut max_value = 0.0_f32;
        let row_offset = row_index as usize * row_stride;
        for x in 0..width {
            let pixel_index = row_offset + x as usize;
            let alpha = glyph_rgba[pixel_index * 4];
            if alpha <= 2 {
                continue;
            }
            let (path_progress, distance) = nearest_hero_stroke_progress(
                x as f32 + 0.5,
                row_index as f32 + 0.5,
                alpha_bounds,
                segments,
            );
            let width_delay = (distance / brush_delay_px).min(1.0) * 0.045;
            let value = (path_progress + width_delay).clamp(0.0, 1.0);
            row_values[x as usize] = value;
            min_value = min_value.min(value);
            max_value = max_value.max(value);
        }
        (min_value, max_value)
    };

    let total_pixels = row_stride.saturating_mul(height as usize);
    let worker_count = hero_reveal_worker_count(total_pixels);
    if worker_count <= 1 || height < 2 {
        let mut min_value = f32::INFINITY;
        let mut max_value = 0.0_f32;
        for (row_index, row_values) in values.chunks_mut(row_stride).enumerate() {
            let (row_min, row_max) = compute_row(row_index as u32, row_values);
            min_value = min_value.min(row_min);
            max_value = max_value.max(row_max);
        }
        return (min_value, max_value);
    }

    let rows_per_band = (height as usize).div_ceil(worker_count).max(1);
    let mut min_value = f32::INFINITY;
    let mut max_value = 0.0_f32;
    std::thread::scope(|scope| {
        let mut handles = Vec::new();
        for (band_index, band) in values.chunks_mut(rows_per_band * row_stride).enumerate() {
            let first_row = (band_index * rows_per_band) as u32;
            let compute_row = &compute_row;
            handles.push(scope.spawn(move || {
                let mut band_min = f32::INFINITY;
                let mut band_max = 0.0_f32;
                for (offset, row_values) in band.chunks_mut(row_stride).enumerate() {
                    let (row_min, row_max) = compute_row(first_row + offset as u32, row_values);
                    band_min = band_min.min(row_min);
                    band_max = band_max.max(row_max);
                }
                (band_min, band_max)
            }));
        }
        for handle in handles {
            if let Ok((band_min, band_max)) = handle.join() {
                min_value = min_value.min(band_min);
                max_value = max_value.max(band_max);
            }
        }
    });
    (min_value, max_value)
}

/// Number of worker threads to use for the hero reveal fill. Returns 1 for
/// small images where threading overhead would dominate.
pub(crate) fn hero_reveal_worker_count(total_pixels: usize) -> usize {
    const MIN_PIXELS_PER_WORKER: usize = 32 * 1024;
    if total_pixels < MIN_PIXELS_PER_WORKER * 2 {
        return 1;
    }
    let available = std::thread::available_parallelism()
        .map(|value| value.get())
        .unwrap_or(1);
    let by_work = total_pixels / MIN_PIXELS_PER_WORKER;
    available.min(by_work).max(1)
}

pub(crate) fn nearest_hero_stroke_progress(
    x: f32,
    y: f32,
    alpha_bounds: HeroMaskPixelBounds,
    segments: &[WelcomeHeroStrokeSegment],
) -> (f32, f32) {
    let bounds_width = alpha_bounds.width();
    let bounds_height = alpha_bounds.height();
    let origin_x = alpha_bounds.min_x as f32;
    let origin_y = alpha_bounds.min_y as f32;
    let mut best_distance_sq = f32::INFINITY;
    let mut best_progress = 0.0;

    for segment in segments {
        let ax = origin_x + segment.start[0] * bounds_width;
        let ay = origin_y + segment.start[1] * bounds_height;
        let bx = origin_x + segment.end[0] * bounds_width;
        let by = origin_y + segment.end[1] * bounds_height;
        let dx = bx - ax;
        let dy = by - ay;
        let len_sq = dx * dx + dy * dy;
        if len_sq <= 0.001 {
            continue;
        }
        let t = (((x - ax) * dx + (y - ay) * dy) / len_sq).clamp(0.0, 1.0);
        let closest_x = ax + dx * t;
        let closest_y = ay + dy * t;
        let distance_sq = (x - closest_x).powi(2) + (y - closest_y).powi(2);
        if distance_sq < best_distance_sq {
            best_distance_sq = distance_sq;
            best_progress =
                segment.start_progress + (segment.end_progress - segment.start_progress) * t;
        }
    }

    (best_progress, best_distance_sq.sqrt())
}
