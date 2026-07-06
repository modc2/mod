// Verifies the Rust auth verifier accepts a token produced by the Python
// module's auth.py (mod-protocol interop). Token + expected address are read
// from files written by the python generator.
// Fixtures are produced by the python generator (see README). When absent
// (clean checkout / CI) the test no-ops instead of failing.
#[test]
fn verifies_python_mod_token() {
    let (Ok(token), Ok(addr)) = (
        std::fs::read_to_string("/tmp/ft_token.txt"),
        std::fs::read_to_string("/tmp/ft_addr.txt"),
    ) else {
        eprintln!("skipping interop test — no token fixture");
        return;
    };
    match freetune_api::auth::verify_token(token.trim()) {
        Ok(recovered) => {
            assert!(
                recovered.eq_ignore_ascii_case(addr.trim()),
                "recovered {recovered} != expected {addr}"
            );
            println!("interop OK: recovered {recovered}");
        }
        Err(e) => panic!("verify failed: {e}"),
    }
}
