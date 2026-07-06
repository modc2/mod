//! Media store — holds images & videos produced by (or uploaded for) the
//! agent's tools, and serves them back to the browser at `/media/<id>`.
//!
//! An id is `<32-hex>.<ext>` so the mime type is recoverable from the id alone
//! and the bytes live at `<data_dir>/media/<id>`. Ids are unguessable random
//! tokens, which doubles as the access-control story for `GET /media/<id>`
//! (a capability URL — embeddable directly in `<img>` / `<video>` with no
//! auth header, which those elements can't send anyway).

use std::path::{Path, PathBuf};

use rand::rngs::OsRng;
use rand::RngCore;

#[derive(Clone)]
pub struct MediaStore {
    dir: PathBuf,
}

impl MediaStore {
    pub fn new() -> Self {
        let base = std::env::var("VENICE_DATA_DIR")
            .map(PathBuf::from)
            .unwrap_or_else(|_| {
                let home = std::env::var("HOME").unwrap_or_else(|_| "/root".into());
                PathBuf::from(home).join(".mod").join("venice")
            });
        let dir = base.join("media");
        std::fs::create_dir_all(&dir).ok();
        Self { dir }
    }

    /// Persist bytes with the given extension; returns the media id.
    pub fn save(&self, bytes: &[u8], ext: &str) -> std::io::Result<String> {
        let mut rnd = [0u8; 16];
        OsRng.fill_bytes(&mut rnd);
        let ext = sanitize_ext(ext);
        let id = format!("{}.{}", hex::encode(rnd), ext);
        std::fs::write(self.dir.join(&id), bytes)?;
        Ok(id)
    }

    /// Resolve an id to an on-disk path, rejecting anything that isn't a plain
    /// `<hex>.<ext>` token (no path traversal).
    pub fn path(&self, id: &str) -> Option<PathBuf> {
        if !is_safe_id(id) {
            return None;
        }
        let p = self.dir.join(id);
        if p.is_file() {
            Some(p)
        } else {
            None
        }
    }

    pub fn load(&self, id: &str) -> Option<(Vec<u8>, &'static str)> {
        let p = self.path(id)?;
        let bytes = std::fs::read(p).ok()?;
        Some((bytes, mime_for(id)))
    }

    /// Base64 of the stored bytes + its mime — used to feed an existing image
    /// back into an edit / image-to-video tool call.
    pub fn read_b64(&self, id: &str) -> Option<(String, &'static str)> {
        use base64::Engine;
        let (bytes, mime) = self.load(id)?;
        Some((base64::engine::general_purpose::STANDARD.encode(bytes), mime))
    }

    /// A `data:` URL for the stored bytes (Venice image-to-video accepts these).
    pub fn data_url(&self, id: &str) -> Option<String> {
        let (b64, mime) = self.read_b64(id)?;
        Some(format!("data:{mime};base64,{b64}"))
    }
}

fn is_safe_id(id: &str) -> bool {
    let mut parts = id.splitn(2, '.');
    let stem = parts.next().unwrap_or("");
    let ext = parts.next().unwrap_or("");
    !stem.is_empty()
        && !ext.is_empty()
        && stem.chars().all(|c| c.is_ascii_hexdigit())
        && ext.chars().all(|c| c.is_ascii_alphanumeric())
}

fn sanitize_ext(ext: &str) -> String {
    let e: String = ext.chars().filter(|c| c.is_ascii_alphanumeric()).collect();
    if e.is_empty() {
        "bin".into()
    } else {
        e.to_lowercase()
    }
}

pub fn mime_for(id: &str) -> &'static str {
    let ext = Path::new(id)
        .extension()
        .and_then(|e| e.to_str())
        .unwrap_or("")
        .to_lowercase();
    match ext.as_str() {
        "png" => "image/png",
        "jpg" | "jpeg" => "image/jpeg",
        "webp" => "image/webp",
        "gif" => "image/gif",
        "mp4" => "video/mp4",
        "webm" => "video/webm",
        _ => "application/octet-stream",
    }
}

pub fn kind_for(id: &str) -> &'static str {
    if mime_for(id).starts_with("video/") {
        "video"
    } else {
        "image"
    }
}
