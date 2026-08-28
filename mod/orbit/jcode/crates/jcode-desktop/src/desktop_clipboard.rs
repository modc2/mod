use super::*;

#[derive(Default)]
pub(crate) struct DesktopClipboard {
    pub(crate) clipboard: Option<arboard::Clipboard>,
}

impl DesktopClipboard {
    pub(crate) fn clipboard(&mut self) -> Result<&mut arboard::Clipboard> {
        if self.clipboard.is_none() {
            self.clipboard = Some(arboard::Clipboard::new().context("failed to access clipboard")?);
        }
        self.clipboard
            .as_mut()
            .context("failed to retain clipboard handle")
    }

    pub(crate) fn set_text(&mut self, text: &str) -> Result<()> {
        self.with_clipboard_retry("failed to set clipboard text", |clipboard| {
            clipboard.set_text(text.to_string())
        })
    }

    pub(crate) fn get_text(&mut self) -> Result<String> {
        self.with_clipboard_retry("clipboard does not contain text", |clipboard| {
            clipboard.get_text()
        })
    }

    pub(crate) fn get_image(&mut self) -> Result<arboard::ImageData<'static>> {
        self.with_clipboard_retry("clipboard does not contain an image", |clipboard| {
            clipboard.get_image()
        })
    }

    pub(crate) fn with_clipboard_retry<T>(
        &mut self,
        context: &'static str,
        mut operation: impl FnMut(&mut arboard::Clipboard) -> Result<T, arboard::Error>,
    ) -> Result<T> {
        const CLIPBOARD_RETRY_ATTEMPTS: usize = 3;
        const CLIPBOARD_RETRY_DELAY: Duration = Duration::from_millis(20);

        for attempt in 0..CLIPBOARD_RETRY_ATTEMPTS {
            let result = operation(self.clipboard()?);
            match result {
                Ok(value) => return Ok(value),
                Err(error)
                    if matches!(&error, arboard::Error::ClipboardOccupied)
                        && attempt + 1 < CLIPBOARD_RETRY_ATTEMPTS =>
                {
                    std::thread::sleep(CLIPBOARD_RETRY_DELAY);
                }
                Err(error) => {
                    if !matches!(
                        &error,
                        arboard::Error::ContentNotAvailable | arboard::Error::ClipboardOccupied
                    ) {
                        self.clipboard = None;
                    }
                    return Err(error).context(context);
                }
            }
        }

        anyhow::bail!("clipboard remained occupied after retrying")
    }
}

pub(crate) fn copy_text_to_clipboard(
    clipboard: &mut DesktopClipboard,
    text: &str,
    success_notice: &'static str,
    app: &mut DesktopApp,
) {
    match clipboard.set_text(text) {
        Ok(()) => app.set_single_session_status_label(success_notice),
        Err(error) => {
            desktop_log::error(format_args!(
                "jcode-desktop: failed to update clipboard after {success_notice}: {error:#}"
            ));
            app.apply_session_event(session_launch::DesktopSessionEvent::Error(format!(
                "failed to update clipboard after {success_notice}: {error:#}"
            )));
        }
    }
}

pub(crate) fn paste_clipboard_into_app(
    clipboard: &mut DesktopClipboard,
    app: &mut DesktopApp,
) -> Result<()> {
    match clipboard_text(clipboard) {
        Ok(text) => {
            if paste_clipboard_text(app, &text) || !app.accepts_clipboard_image_paste() {
                return Ok(());
            }
            paste_clipboard_image_into_app(clipboard, app)
                .with_context(|| "clipboard text was empty and no pasteable image was available")
        }
        Err(text_error) if app.accepts_clipboard_image_paste() => {
            paste_clipboard_image_into_app(clipboard, app)
                .with_context(|| format!("clipboard did not contain pasteable text: {text_error}"))
        }
        Err(error) => Err(error),
    }
}

pub(crate) fn paste_clipboard_text(app: &mut DesktopApp, text: &str) -> bool {
    let text = normalize_clipboard_text(text);
    if text.is_empty() {
        return false;
    }
    app.paste_text(&text);
    true
}

pub(crate) fn paste_clipboard_image_into_app(
    clipboard: &mut DesktopClipboard,
    app: &mut DesktopApp,
) -> Result<()> {
    let (media_type, base64_data) = clipboard_image_png_base64(clipboard)?;
    app.attach_clipboard_image(media_type, base64_data);
    Ok(())
}

pub(crate) fn normalize_clipboard_text(text: &str) -> String {
    text.replace("\r\n", "\n").replace('\r', "\n")
}

pub(crate) fn clipboard_image_png_base64(
    clipboard: &mut DesktopClipboard,
) -> Result<(String, String)> {
    let image = clipboard.get_image()?;
    let width = u32::try_from(image.width).context("clipboard image is too wide")?;
    let height = u32::try_from(image.height).context("clipboard image is too tall")?;
    let rgba = image.bytes.into_owned();
    let buffer = image::RgbaImage::from_raw(width, height, rgba)
        .context("clipboard image data had unexpected dimensions")?;
    let mut cursor = std::io::Cursor::new(Vec::new());
    image::DynamicImage::ImageRgba8(buffer)
        .write_to(&mut cursor, image::ImageFormat::Png)
        .context("failed to encode clipboard image as png")?;
    Ok((
        "image/png".to_string(),
        base64::engine::general_purpose::STANDARD.encode(cursor.into_inner()),
    ))
}

pub(crate) fn clipboard_text(clipboard: &mut DesktopClipboard) -> Result<String> {
    clipboard.get_text()
}
