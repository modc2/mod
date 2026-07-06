//! Cross-language check: a token minted by the Python mod auth module
//! (the source of truth) must verify in the Rust verifier and recover the
//! same address. Token + expected address are passed via env so the Python
//! side can generate fresh material:
//!
//!   VENICE_TEST_TOKEN=... VENICE_TEST_ADDR=0x... cargo test --release \
//!       --test auth_roundtrip -- --ignored --nocapture

use venice_api::auth::verify_token;

#[test]
#[ignore]
fn python_token_verifies() {
    let token = std::env::var("VENICE_TEST_TOKEN").expect("set VENICE_TEST_TOKEN");
    let expect = std::env::var("VENICE_TEST_ADDR").expect("set VENICE_TEST_ADDR");
    let got = verify_token(&token, 86_400 * 3650).expect("token should verify");
    assert_eq!(got, expect.to_lowercase(), "recovered address mismatch");
}
