use super::*;

pub(crate) struct DesktopAsyncJobPermit<'a> {
    pub(crate) counter: &'a AtomicUsize,
}

impl Drop for DesktopAsyncJobPermit<'_> {
    fn drop(&mut self) {
        self.counter.fetch_sub(1, Ordering::Relaxed);
    }
}

pub(crate) fn try_acquire_desktop_async_job_slot<'a>(
    counter: &'a AtomicUsize,
    limit: usize,
) -> Result<DesktopAsyncJobPermit<'a>> {
    let mut current = counter.load(Ordering::Relaxed);
    loop {
        if current >= limit {
            anyhow::bail!("desktop async job limit reached ({limit})");
        }
        match counter.compare_exchange_weak(
            current,
            current + 1,
            Ordering::Acquire,
            Ordering::Relaxed,
        ) {
            Ok(_) => return Ok(DesktopAsyncJobPermit { counter }),
            Err(next_current) => current = next_current,
        }
    }
}

pub(crate) fn spawn_bounded_desktop_async_job(
    name: impl Into<String>,
    job: impl FnOnce() + Send + 'static,
) -> Result<()> {
    let name = name.into();
    let permit =
        try_acquire_desktop_async_job_slot(&DESKTOP_ASYNC_JOB_COUNT, DESKTOP_ASYNC_JOB_LIMIT)
            .with_context(|| format!("failed to start {name}"))?;
    std::thread::Builder::new()
        .name(name.clone())
        .spawn(move || {
            let _permit = permit;
            job();
        })
        .with_context(|| format!("failed to spawn {name}"))?;
    Ok(())
}

#[derive(Clone)]
pub(crate) struct DesktopReasoningEffortRequestQueue {
    pub(crate) request_tx: mpsc::Sender<DesktopReasoningEffortRequest>,
    pub(crate) latest_generation: Arc<AtomicU64>,
}

pub(crate) struct DesktopReasoningEffortRequest {
    pub(crate) generation: u64,
    pub(crate) effort: String,
    pub(crate) target_session_id: Option<String>,
    pub(crate) event_tx: session_launch::DesktopSessionEventSender,
}

impl DesktopReasoningEffortRequestQueue {
    pub(crate) fn request(
        &self,
        effort: String,
        target_session_id: Option<String>,
        event_tx: session_launch::DesktopSessionEventSender,
    ) -> Result<()> {
        let generation = self.latest_generation.fetch_add(1, Ordering::AcqRel) + 1;
        self.request_tx
            .send(DesktopReasoningEffortRequest {
                generation,
                effort,
                target_session_id,
                event_tx,
            })
            .context("failed to queue desktop reasoning effort change")
    }
}

pub(crate) fn spawn_desktop_reasoning_effort_request_queue()
-> Result<DesktopReasoningEffortRequestQueue> {
    let (request_tx, request_rx) = mpsc::channel();
    let latest_generation = Arc::new(AtomicU64::new(0));
    let worker_latest_generation = Arc::clone(&latest_generation);
    std::thread::Builder::new()
        .name("jcode-desktop-effort-queue".to_string())
        .spawn(move || {
            run_desktop_reasoning_effort_request_queue(request_rx, worker_latest_generation);
        })
        .context("failed to spawn desktop reasoning effort queue")?;
    Ok(DesktopReasoningEffortRequestQueue {
        request_tx,
        latest_generation,
    })
}

pub(crate) fn run_desktop_reasoning_effort_request_queue(
    request_rx: mpsc::Receiver<DesktopReasoningEffortRequest>,
    latest_generation: Arc<AtomicU64>,
) {
    while let Ok(mut request) = request_rx.recv() {
        let mut coalesced = 0usize;
        let mut disconnected = false;
        loop {
            match request_rx.try_recv() {
                Ok(next_request) => {
                    request = next_request;
                    coalesced += 1;
                }
                Err(mpsc::TryRecvError::Empty) => break,
                Err(mpsc::TryRecvError::Disconnected) => {
                    disconnected = true;
                    break;
                }
            }
        }
        if coalesced > 0 {
            desktop_log::info(format_args!(
                "jcode-desktop: coalesced {coalesced} superseded reasoning effort request(s); applying {}",
                desktop_log::truncate_for_log(&request.effort, 64)
            ));
        }
        apply_desktop_reasoning_effort_request(request, &latest_generation);
        if disconnected {
            break;
        }
    }
}

pub(crate) fn apply_desktop_reasoning_effort_request(
    request: DesktopReasoningEffortRequest,
    latest_generation: &AtomicU64,
) {
    let (response_tx, response_rx) = mpsc::channel();
    let result = session_launch::set_reasoning_effort(
        &request.effort,
        request.target_session_id.as_deref(),
        Some(response_tx),
    );
    let still_latest = latest_generation.load(Ordering::Acquire) == request.generation;
    if still_latest {
        for event in response_rx.try_iter() {
            let _ = request.event_tx.send(event);
        }
        if let Err(error) = result {
            desktop_log::error(format_args!(
                "jcode-desktop: reasoning effort sync failed generation={} target_session={}: {error:#}",
                request.generation,
                request.target_session_id.as_deref().unwrap_or("<current>")
            ));
            let _ = request
                .event_tx
                .send(session_launch::DesktopSessionEvent::Status(
                    DesktopSessionStatus::ReasoningEffortFailed(format!("{error:#}")),
                ));
        }
    } else if let Err(error) = result {
        desktop_log::warn(format_args!(
            "jcode-desktop: stale reasoning effort sync failed generation={} target_session={}: {error:#}",
            request.generation,
            request.target_session_id.as_deref().unwrap_or("<current>")
        ));
    } else {
        let dropped = response_rx.try_iter().count();
        desktop_log::info(format_args!(
            "jcode-desktop: dropped stale reasoning effort response generation={} event_count={dropped}",
            request.generation
        ));
    }
}
