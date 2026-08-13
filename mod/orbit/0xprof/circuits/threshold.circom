pragma circom 2.0.0;

include "../node_modules/circomlib/circuits/poseidon.circom";
include "../node_modules/circomlib/circuits/comparators.circom";

// "The number I committed to is at least `threshold`" — and nothing else.
//
// Public:  commitment, threshold        Private: value, salt
//
// The commitment pins the value down before the threshold is chosen, so the
// proof is about a number the prover cannot swap out afterwards. This is the
// shape most real statements take — solvency, age, score, reputation — which
// is why it is the second fixture and the demo circuit in the console.
template Threshold(bits) {
    signal input value;
    signal input salt;
    signal input commitment;
    signal input threshold;

    component hash = Poseidon(2);
    hash.inputs[0] <== value;
    hash.inputs[1] <== salt;
    hash.out === commitment;

    component ge = GreaterEqThan(bits);
    ge.in[0] <== value;
    ge.in[1] <== threshold;
    ge.out === 1;
}

component main {public [commitment, threshold]} = Threshold(64);
