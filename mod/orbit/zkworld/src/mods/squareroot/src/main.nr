// Squareroot: prove you know x with x * x == y. y is public, x stays private.
fn main(x: Field, y: pub Field) {
    // x == 1 would make "1 * 1 == 1" trivially true; ask for a real root
    assert(x != 1);
    assert(x * x == y);
}

#[test]
fn test_square() {
    main(3, 9);
}

#[test(should_fail)]
fn test_wrong_root() {
    main(3, 10);
}
