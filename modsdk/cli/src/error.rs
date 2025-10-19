//! Error types and utilities for the Subspace Explorer

use std::fmt;
use thiserror::Error;

/// A type alias for handling errors throughout the application
pub type _Result<T> = std::result::Result<T, Error>;

/// The error type for the Subspace Explorer application
#[derive(Error, Debug)]
pub enum Error {
    /// I/O errors
    #[error("I/O error: {0}")]
    Io(#[from] std::io::Error),

    /// Configuration errors
    #[error("Configuration error: {0}")]
    _Config(String),

    /// Substrate/Subspace client errors
    #[error("Substrate client error: {0}")]
    _Substrate(String),

    /// JSON serialization/deserialization errors
    #[error("JSON error: {0}")]
    Json(#[from] serde_json::Error),

    /// Custom error message
    #[error("{0}")]
    Message(String),
}

impl From<&str> for Error {
    fn from(s: &str) -> Self {
        Error::Message(s.to_string())
    }
}

impl From<String> for Error {
    fn from(s: String) -> Self {
        Error::Message(s)
    }
}

/// A wrapper around `std::io::Error` to implement `std::error::Error` for `Box<dyn std::error::Error>`
#[derive(Debug)]
pub struct BoxedError(Box<dyn std::error::Error + Send + Sync>);

impl fmt::Display for BoxedError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}", self.0)
    }
}

impl std::error::Error for BoxedError {}

impl From<Box<dyn std::error::Error + Send + Sync>> for Error {
    fn from(err: Box<dyn std::error::Error + Send + Sync>) -> Self {
        Error::Message(err.to_string())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_error_from_str() {
        let err = Error::from("test error");
        assert_eq!(err.to_string(), "test error");
    }

    #[test]
    fn test_error_from_string() {
        let s = String::from("test error");
        let err = Error::from(s);
        assert_eq!(err.to_string(), "test error");
    }
}
