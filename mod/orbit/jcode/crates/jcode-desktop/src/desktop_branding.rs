//! Central user-facing branding for the desktop app.
//!
//! Every surface that names the product (window title, status titles, help
//! text, version output, in-app header labels) should use these constants so
//! the release channel is marked consistently and can be changed in one place
//! when the desktop app graduates from beta.

/// Release channel shown across user-facing desktop surfaces.
pub(crate) const DESKTOP_RELEASE_CHANNEL: &str = "Beta";

/// Product name without the release-channel suffix.
#[cfg_attr(not(test), allow(dead_code))]
pub(crate) const DESKTOP_PRODUCT_BASE_NAME: &str = "Jcode Desktop";

/// Full user-facing product name, including the release channel.
pub(crate) const DESKTOP_PRODUCT_NAME: &str = "Jcode Desktop (Beta)";

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn product_name_is_base_name_plus_release_channel() {
        assert_eq!(
            DESKTOP_PRODUCT_NAME,
            format!("{DESKTOP_PRODUCT_BASE_NAME} ({DESKTOP_RELEASE_CHANNEL})")
        );
    }
}
