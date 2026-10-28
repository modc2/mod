//! See Cargo.toml: getrandom 0.2's API. Off wasm it is getrandom 0.3; in
//! the contract there is no entropy and every call fails.
#![no_std]

use core::num::NonZeroU32;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Error(NonZeroU32);

const fn nz(n: u32) -> NonZeroU32 {
    match NonZeroU32::new(n) {
        Some(n) => n,
        None => panic!("zero"),
    }
}

impl Error {
    pub const UNSUPPORTED: Error = Error(nz(Self::INTERNAL_START));
    pub const INTERNAL_START: u32 = 1 << 31;
    pub const CUSTOM_START: u32 = (1 << 31) + (1 << 30);

    pub fn code(self) -> NonZeroU32 {
        self.0
    }

    pub fn raw_os_error(self) -> Option<i32> {
        None
    }
}

impl From<NonZeroU32> for Error {
    fn from(code: NonZeroU32) -> Self {
        Error(code)
    }
}

impl core::fmt::Display for Error {
    fn fmt(&self, f: &mut core::fmt::Formatter<'_>) -> core::fmt::Result {
        write!(f, "getrandom error {}", self.0)
    }
}

impl core::error::Error for Error {}

#[cfg(not(target_arch = "wasm32"))]
pub fn getrandom(dest: &mut [u8]) -> Result<(), Error> {
    getrandom03::fill(dest).map_err(|_| Error::UNSUPPORTED)
}

#[cfg(target_arch = "wasm32")]
pub fn getrandom(_dest: &mut [u8]) -> Result<(), Error> {
    Err(Error::UNSUPPORTED)
}
