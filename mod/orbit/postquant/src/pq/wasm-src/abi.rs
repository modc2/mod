//! The pq1 wasm ABI shared by every verifier module.
//!
//! Exports: `pq_reset()` then `pq_alloc(n) -> ptr` for each input buffer,
//! then `pq_verify(pk, pk_len, msg, msg_len, sig, sig_len, ctx, ctx_len)
//! -> i32` (1 valid, 0 invalid). The FIPS m' wrapping (0x00 || len(ctx) ||
//! ctx || msg) happens inside the module, so the wasm IS the algorithm —
//! the host hands it raw bytes and gets a verdict.
//!
//! No allocator, no imports, no floats: inputs land in a static bump arena,
//! working state lives on the shadow stack, and a fresh instance per call
//! means nothing persists between verifications.

use core::panic::PanicInfo;

#[panic_handler]
fn panic(_: &PanicInfo) -> ! {
    core::arch::wasm32::unreachable()
}

const ARENA_BYTES: usize = 1 << 17; // 128KB: pk + sig + a canonical tx body
static mut ARENA: [u8; ARENA_BYTES] = [0; ARENA_BYTES];
static mut ARENA_POS: usize = 0;

#[no_mangle]
pub extern "C" fn pq_reset() {
    unsafe {
        ARENA_POS = 0;
    }
}

#[no_mangle]
pub extern "C" fn pq_alloc(n: usize) -> *mut u8 {
    unsafe {
        let pos = ARENA_POS;
        if n > ARENA_BYTES || pos > ARENA_BYTES - n {
            return core::ptr::null_mut();
        }
        ARENA_POS = pos + n;
        core::ptr::addr_of_mut!(ARENA).cast::<u8>().add(pos)
    }
}

pub unsafe fn input<'a>(ptr: *const u8, len: usize) -> &'a [u8] {
    if ptr.is_null() {
        &[]
    } else {
        core::slice::from_raw_parts(ptr, len)
    }
}
