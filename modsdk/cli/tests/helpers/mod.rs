//! Test helpers and utilities for the Subspace Explorer TUI

use std::sync::Once;

static INIT: Once = Once::new();

/// Initialize the test environment
/// This sets up logging and other test-specific configurations
pub fn init_test_env() {
    INIT.call_once(|| {
        // Initialize logging for tests
        let _ = env_logger::builder()
            .is_test(true)
            .try_init();
    });
}

/// Assert that two values are approximately equal, accounting for floating point precision
#[macro_export]
macro_rules! assert_approx_eq {
    ($a:expr, $b:expr, $eps:expr) => {
        assert!(
            ($a - $b).abs() < $eps,
            "assertion failed: |{} - {}| < {}",
            $a,
            $b,
            $eps
        );
    };
    ($a:expr, $b:expr) => {
        assert_approx_eq!($a, $b, 1e-6);
    };
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_assert_approx_eq() {
        assert_approx_eq!(1.0, 1.0000001);
        assert_approx_eq!(1.0, 1.1, 0.2);
    }
}
