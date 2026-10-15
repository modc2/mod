// FactorsNoir: prove you know a and b with a * b == c. c is public, a and b stay
// private, and neither may be 1 (otherwise "1 * c" proves nothing).
fn main(a: Field, b: Field, c: pub Field) {
    assert(a != 1);
    assert(b != 1);
    assert(a * b == c);
}

#[test]
fn test_three_times_eleven() {
    main(3, 11, 33);
}

#[test(should_fail)]
fn test_trivial_factor() {
    main(1, 33, 33);
}
