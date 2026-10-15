// Native tests for factors_leo.aleo (leo test).
import factors_leo.aleo;
program test_factors_leo.aleo {
    @test
    fn test_three_times_eleven() {
        factors_leo.aleo::main(3field, 11field, 33field);
    }

    @test
    @should_fail
    fn test_trivial_factor() {
        factors_leo.aleo::main(1field, 33field, 33field);
    }

    @noupgrade
    constructor() {}
}
