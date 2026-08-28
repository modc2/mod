pragma circom 2.0.0;

// The hello-world of zk: I know two numbers whose product is the one you see.
// Small, fast, and useful here as the fixture every verifier is checked against.
template Multiplier2() {
    signal input a;
    signal input b;
    signal output c;
    c <== a * b;
}

component main = Multiplier2();
